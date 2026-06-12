"""
bitorm — a super lightweight ORM for SQLite, standard library only.

Design goals:
    * Zero third-party dependencies (sqlite3 + typing + datetime).
    * Declare tables as classes using type annotations.
    * Safe by default: every value goes through a parameterized query.
    * Small, readable, hackable surface area.

Quick start:

    from bitorm import Model, Field, connect
    from datetime import datetime

    class User(Model):
        name: str
        email: str = Field(unique=True)
        age: int = 0
        created: datetime = Field(default_factory=datetime.now)

    connect("app.db")
    User.create_table()

    u = User(name="Ada", email="ada@example.com", age=36)
    u.save()                       # INSERT, populates u.id

    User.filter(age__gte=18).order_by("-age").limit(10).all()
    User.get(email="ada@example.com")
    u.age = 37; u.save()           # UPDATE (row already has a pk)
    u.delete()
"""

from __future__ import annotations

import inspect
import sqlite3
from typing import Any, Iterator, Optional

from .db import Database, connect, _db, make_migration, migrate  # noqa: F401
from .fields import (  # noqa: F401
    _UNSET, _qi, Field, FileField, FileRef, _FileDescriptor,
    _Column, ForeignKey, _ForwardFK, _ReverseFK,
    ManyToMany, _M2MManager, _M2MDescriptor,
)

__all__ = [
    "Model", "Field", "FileField", "FileRef", "ForeignKey", "ManyToMany",
    "connect", "Database", "make_migration", "migrate",
]


# --------------------------------------------------------------------------- #
# Field / column definitions
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# WHERE-clause operators (Django-style suffixes)
# --------------------------------------------------------------------------- #

_OPERATORS = {
    "eq": "=",
    "ne": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "like": "LIKE",
    "in": "IN",
}


def _build_where(
    filters: dict,
    base: Optional[str] = None,
    relations: Optional[dict] = None,
    joins: Optional[set] = None,
) -> tuple[str, list]:
    """Turn a filter dict into an SQL WHERE clause and its params.

        {'age__gte': 18, 'name': 'x'}  ->  ('age >= ? AND name = ?', [18, 'x'])

    base:       table name used to qualify plain columns (e.g. "email").
                When None, columns are emitted unqualified — the path the
                bulk DELETE uses, since SQLite DELETE has no FROM-join.
    relations:  the model's {relation_name: fk_column} map, which enables
                single-hop related lookups such as author__name / author__name__like.
    joins:      a set supplied by the caller; any relation that needs a JOIN
                gets its name added here so the caller can build the JOIN SQL.
    """
    relations = relations or {}
    clauses, params = [], []
    for key, value in filters.items():
        field, sep, suffix = key.rpartition("__")
        if sep and suffix in _OPERATORS:
            op = _OPERATORS[suffix]
        else:
            field, op = key, "="

        # is the leading segment a relation? (author__name -> author . name)
        rel, sep, col = field.partition("__")
        if sep and rel in relations:
            if joins is not None:
                joins.add(rel)
            ref = f"{_qi(rel)}.{_qi(col)}"
        elif base:
            ref = f"{_qi(base)}.{_qi(field)}"
        else:
            ref = _qi(field)

        if op == "IN":
            if not value:
                clauses.append("0")
            else:
                placeholders = ", ".join("?" for _ in value)
                clauses.append(f"{ref} IN ({placeholders})")
                params.extend(value)
        elif value is None and op == "=":
            clauses.append(f"{ref} IS NULL")
        elif value is None and op == "!=":
            clauses.append(f"{ref} IS NOT NULL")
        else:
            clauses.append(f"{ref} {op} ?")
            params.append(value)
    return " AND ".join(clauses), params


# --------------------------------------------------------------------------- #
# QuerySet — lazy, chainable
# --------------------------------------------------------------------------- #

class QuerySet:
    def __init__(self, model: type["Model"]):
        self.model = model
        self._filters: dict = {}
        self._order: list[str] = []
        self._limit: Optional[int] = None
        self._distinct: bool = False
        self._result_cache: Optional[list] = None  # populated on first evaluation

    def _clone(self) -> "QuerySet":
        """A fresh, unevaluated copy carrying the same query spec."""
        qs = QuerySet(self.model)
        qs._filters = dict(self._filters)
        qs._order = list(self._order)
        qs._limit = self._limit
        qs._distinct = self._distinct
        return qs  # cache deliberately NOT copied -> the clone re-queries

    # -- chainable builders (return a new QuerySet, never mutate self) ------
    def filter(self, **kwargs) -> "QuerySet":
        qs = self._clone()
        qs._filters.update(kwargs)
        return qs

    def order_by(self, *fields: str) -> "QuerySet":
        # "-age" means DESC, "age" means ASC
        qs = self._clone()
        qs._order.extend(fields)
        return qs

    def limit(self, n: int) -> "QuerySet":
        qs = self._clone()
        qs._limit = n
        return qs

    def all(self) -> "QuerySet":
        """Return a fresh QuerySet (lazy). Matches Django: no query runs here."""
        return self._clone()

    def distinct(self) -> "QuerySet":
        qs = self._clone()
        qs._distinct = True
        return qs

    # -- SQL assembly -------------------------------------------------------
    def _sql(self) -> tuple[str, list]:
        base = self.model._table
        relations = self.model._fks  # {relation_name: fk_column}
        joins: set = set()           # relation names that need a LEFT JOIN

        where_sql, params = _build_where(self._filters, base, relations, joins)

        order_parts = []
        for f in self._order:
            desc = f.startswith("-")           # "-age" => DESC
            name = f[1:] if desc else f
            rel, sep, col = name.partition("__")
            if sep and rel in relations:        # order by a related field
                joins.add(rel)
                ref = f"{_qi(rel)}.{_qi(col)}"
            else:
                ref = f"{_qi(base)}.{_qi(name)}"
            order_parts.append(f"{ref} {'DESC' if desc else 'ASC'}")

        # Build the joins. FK is many-to-one, so a LEFT JOIN never multiplies
        # rows — which is exactly why count()/first() stay correct here.
        join_sql = ""
        for rel in joins:
            fk = getattr(self.model, rel)  # _ForwardFK descriptor (.to, .col)
            join_sql += (
                f" LEFT JOIN {_qi(fk.to._table)} {_qi(rel)} "
                f"ON {_qi(rel)}.{_qi(fk.to._pk)} = {_qi(base)}.{_qi(fk.col)}"
            )

        # Always qualify + select base.* so joined columns never leak into rows.
        distinct = "DISTINCT " if self._distinct else ""
        sql = f"SELECT {distinct}{_qi(base)}.* FROM {_qi(base)}{join_sql}"
        if where_sql:
            sql += f" WHERE {where_sql}"
        if order_parts:
            sql += f" ORDER BY {', '.join(order_parts)}"
        if self._limit is not None:
            sql += f" LIMIT {int(self._limit)}"
        return sql, params

    # -- evaluation ---------------------------------------------------------
    def _fetch(self) -> list["Model"]:
        """Run the query once and cache the rows for this QuerySet."""
        if self._result_cache is None:
            sql, params = self._sql()
            rows = _db().execute(sql, tuple(params)).fetchall()
            self._result_cache = [self.model._from_row(r) for r in rows]
        return self._result_cache

    def first(self) -> Optional["Model"]:
        results = self.limit(1)._fetch()
        return results[0] if results else None

    def count(self) -> int:
        # if already evaluated, count the cache; otherwise ask the database
        if self._result_cache is not None:
            return len(self._result_cache)
        sql, params = self._sql()
        count_sql = f"SELECT COUNT(*) AS n FROM ({sql})"
        return _db().execute(count_sql, tuple(params)).fetchone()["n"]

    def _aggregate(self, func: str, column: str) -> Any:
        valid = {c.name for c in self.model._columns}
        if column not in valid:
            raise ValueError(
                f"'{column}' is not a column on {self.model.__name__}. "
                f"Valid columns: {', '.join(sorted(valid))}"
            )
        qs = self._clone()
        qs._order = []
        qs._limit = None
        sql, params = qs._sql()
        col_obj = next(c for c in self.model._columns if c.name == column)
        agg_sql = f"SELECT {func}({_qi(column)}) AS val FROM ({sql})"
        result = _db().execute(agg_sql, tuple(params)).fetchone()["val"]
        if result is not None:
            return col_obj.from_db(result)
        return None

    def sum(self, column: str) -> Any:
        return self._aggregate("SUM", column)

    def avg(self, column: str) -> Any:
        return self._aggregate("AVG", column)

    def min(self, column: str) -> Any:
        return self._aggregate("MIN", column)

    def max(self, column: str) -> Any:
        return self._aggregate("MAX", column)

    def delete(self) -> int:
        """Bulk delete every row matching the current filters."""
        for key in self._filters:
            if key.split("__", 1)[0] in self.model._fks:
                raise NotImplementedError(
                    "Bulk delete across a relation isn't supported "
                    "(SQLite DELETE can't join). Fetch the rows and "
                    "delete them individually instead."
                )
        sql = f"DELETE FROM {_qi(self.model._table)}"
        params: list = []
        if self._filters:
            where, params = _build_where(self._filters)  # unqualified, no joins
            sql += f" WHERE {where}"
        cur = _db().execute(sql, tuple(params))
        _db().commit()
        return cur.rowcount

    # -- sequence protocol: these trigger evaluation ------------------------
    def __iter__(self) -> Iterator["Model"]:
        return iter(self._fetch())

    def __len__(self) -> int:
        return len(self._fetch())

    def __bool__(self) -> bool:
        return bool(self._fetch())

    def __getitem__(self, index):
        return self._fetch()[index]

    def __repr__(self) -> str:
        return f"<QuerySet {self._fetch()!r}>"


# --------------------------------------------------------------------------- #
# Model base class
# --------------------------------------------------------------------------- #

class Model:
    """Subclass this and declare columns with type annotations."""

    # Filled in by __init_subclass__:
    _columns: list[_Column] = []
    _pk: str = "id"
    _table: str = ""
    _fks: dict = {}  # relation name -> column name, e.g. {"author": "author_id"}
    _m2m: list = []  # (join_table, owner_col, target_col, target_model) tuples

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # resolve the table name first; m2m join names depend on it
        cls._table = cls.__dict__.get("__tablename__", cls.__name__.lower())

        annotations = inspect.get_annotations(cls, eval_str=True)
        columns: list[_Column] = []
        fks: dict = {}
        m2m: list = []
        has_pk = False

        parent_columns: list[_Column] = []
        parent_fks: dict = {}
        parent_m2m: list = []
        parent_pk: str | None = None
        for base in cls.__mro__[1:]:
            if base is Model or base is object:
                continue
            if hasattr(base, '_columns') and base._columns:
                parent_columns = list(base._columns)
                parent_fks = dict(getattr(base, '_fks', {}))
                parent_m2m = list(getattr(base, '_m2m', []))
                parent_pk = base._pk
                break

        for name, py_type in annotations.items():
            raw = cls.__dict__.get(name, _UNSET)

            # ---- many-to-many ------------------------------------------------
            if isinstance(raw, ManyToMany):
                rel = raw
                join_table = f"{cls._table}_{name}"
                owner_col = f"{cls._table}_id"
                target_col = f"{rel.to._table}_id"
                m2m.append((join_table, owner_col, target_col, rel.to))
                # forward manager (post.tags)
                setattr(cls, name, _M2MDescriptor(join_table, owner_col, target_col, rel.to))
                # reverse manager on the target (tag.posts)
                related = rel.related_name or f"{cls.__name__.lower()}s"
                if hasattr(rel.to, related) and isinstance(getattr(rel.to, related), _M2MDescriptor):
                    existing = getattr(rel.to, related)
                    if existing.other_model is cls:
                        raise ValueError(
                            f"M2M field '{name}' on {cls.__name__} would overwrite "
                            f"reverse accessor '{related}' on {rel.to.__name__}. "
                            f"Specify a unique related_name."
                        )
                setattr(rel.to, related, _M2MDescriptor(join_table, target_col, owner_col, cls))
                continue

            # ---- foreign key -------------------------------------------------
            if isinstance(raw, ForeignKey):
                fk = raw
                col_name = f"{name}_id"
                ref = f"{_qi(fk.to._table)}({_qi(fk.to._pk)})"
                if fk.on_delete:
                    ref += f" ON DELETE {fk.on_delete}"
                columns.append(
                    _Column(col_name, int, Field(nullable=fk.nullable), references=ref)
                )
                fks[name] = col_name
                # forward accessor (post.author) replaces the class attribute
                setattr(cls, name, _ForwardFK(name, fk.to))
                # reverse accessor on the target (user.posts)
                related = fk.related_name or f"{cls.__name__.lower()}s"
                setattr(fk.to, related, _ReverseFK(cls, col_name))
                continue

            # ---- file field --------------------------------------------------
            if isinstance(raw, FileField):
                ff = raw
                columns.append(_Column(name, str, Field(nullable=ff.nullable)))
                setattr(cls, name, _FileDescriptor(name, ff.base_dir))
                continue

            # ---- normal column ----------------------------------------------
            if isinstance(raw, Field):
                field = raw
            elif raw is _UNSET:
                field = Field()
            else:  # a bare default value, e.g. `age: int = 0`
                field = Field(default=raw)
            # remove the class-level value so it can't shadow instance attrs
            if name in cls.__dict__:
                delattr(cls, name)
            if field.primary_key:
                has_pk = True
                cls._pk = name
            columns.append(_Column(name, py_type, field))

        own_names = {c.name for c in columns}
        for pc in parent_columns:
            if pc.name not in own_names:
                columns.append(pc)
        for k, v in parent_fks.items():
            if k not in fks:
                fks[k] = v
        for pm in parent_m2m:
            if pm not in m2m:
                m2m.append(pm)

        if not has_pk:
            if parent_pk:
                cls._pk = parent_pk
            else:
                pk_col = _Column("id", int, Field(primary_key=True))
                columns.insert(0, pk_col)
                cls._pk = "id"

        cls._columns = columns
        cls._fks = fks
        cls._m2m = m2m

    # -- instances ----------------------------------------------------------
    def __init__(self, **kwargs):
        valid_names = {c.name for c in self._columns} | set(self._fks)
        unknown = set(kwargs) - valid_names
        if unknown:
            raise TypeError(f"Unknown field(s): {', '.join(sorted(unknown))}")
        for col in self._columns:
            if col.name in kwargs:
                value = kwargs[col.name]
            elif col.field.default_factory is not None:
                value = col.field.default_factory()
            elif col.field.default is not _UNSET:
                value = col.field.default
            else:
                value = None
            setattr(self, col.name, value)
        for rel_name in self._fks:
            if rel_name in kwargs:
                setattr(self, rel_name, kwargs[rel_name])

    def __repr__(self) -> str:
        fields = ", ".join(f"{c.name}={getattr(self, c.name)!r}" for c in self._columns)
        return f"{type(self).__name__}({fields})"

    # -- schema -------------------------------------------------------------
    @classmethod
    def create_table(cls, if_not_exists: bool = True) -> None:
        cols_ddl = ", ".join(c.ddl() for c in cls._columns)
        exists = "IF NOT EXISTS " if if_not_exists else ""
        _db().execute(f"CREATE TABLE {exists}{_qi(cls._table)} ({cols_ddl})")
        # create any m2m join tables owned by this model
        for join_table, owner_col, target_col, target in cls._m2m:
            _db().execute(
                f"CREATE TABLE IF NOT EXISTS {_qi(join_table)} ("
                f"{_qi(owner_col)} INTEGER NOT NULL "
                f"REFERENCES {_qi(cls._table)}({_qi(cls._pk)}) ON DELETE CASCADE, "
                f"{_qi(target_col)} INTEGER NOT NULL "
                f"REFERENCES {_qi(target._table)}({_qi(target._pk)}) ON DELETE CASCADE, "
                f"PRIMARY KEY ({_qi(owner_col)}, {_qi(target_col)}))"
            )
        _db().commit()

    @classmethod
    def drop_table(cls) -> None:
        _db().execute(f"DROP TABLE IF EXISTS {_qi(cls._table)}")
        _db().commit()

    # -- row <-> object -----------------------------------------------------
    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> "Model":
        obj = cls.__new__(cls)  # bypass __init__/defaults
        by_name = {c.name: c for c in cls._columns}
        for key in row.keys():
            col = by_name.get(key)
            value = col.from_db(row[key]) if col else row[key]
            setattr(obj, key, value)
        return obj

    # -- persistence --------------------------------------------------------
    def save(self) -> "Model":
        """INSERT if this object has no primary key value, else UPDATE."""
        pk = self._pk
        pk_value = getattr(self, pk, None)
        data_cols = [c for c in self._columns if c.name != pk]

        for col in self._columns:
            col._validate(getattr(self, col.name))

        if pk_value is None:  # INSERT
            names = [c.name for c in data_cols]
            values = [c.to_db(getattr(self, c.name)) for c in data_cols]
            placeholders = ", ".join("?" for _ in names)
            sql = (
                f"INSERT INTO {_qi(self._table)} ({', '.join(_qi(n) for n in names)}) "
                f"VALUES ({placeholders})"
            )
            cur = _db().execute(sql, tuple(values))
            setattr(self, pk, cur.lastrowid)  # populate generated id
        else:  # UPDATE
            assignments = ", ".join(f"{_qi(c.name)} = ?" for c in data_cols)
            values = [c.to_db(getattr(self, c.name)) for c in data_cols]
            values.append(pk_value)
            sql = f"UPDATE {_qi(self._table)} SET {assignments} WHERE {_qi(pk)} = ?"
            cur = _db().execute(sql, tuple(values))
            if cur.rowcount == 0:
                all_cols = self._columns
                names = [c.name for c in all_cols]
                vals = [c.to_db(getattr(self, c.name)) for c in all_cols]
                placeholders = ", ".join("?" for _ in names)
                sql = (
                    f"INSERT INTO {_qi(self._table)} ({', '.join(_qi(n) for n in names)}) "
                    f"VALUES ({placeholders})"
                )
                _db().execute(sql, tuple(vals))

        _db().commit()
        return self

    def delete(self) -> None:
        pk_value = getattr(self, self._pk, None)
        if pk_value is None:
            raise ValueError("Cannot delete an unsaved object (no primary key).")
        _db().execute(
            f"DELETE FROM {_qi(self._table)} WHERE {_qi(self._pk)} = ?", (pk_value,)
        )
        _db().commit()
        setattr(self, self._pk, None)

    # -- query entry points -------------------------------------------------
    @classmethod
    def raw(cls, sql: str, params: tuple = ()) -> list["Model"]:
        """Execute raw SQL and return model instances."""
        rows = _db().execute(sql, tuple(params)).fetchall()
        return [cls._from_row(r) for r in rows]

    @classmethod
    def filter(cls, **kwargs) -> QuerySet:
        return QuerySet(cls).filter(**kwargs)

    @classmethod
    def all(cls) -> "QuerySet":
        return QuerySet(cls)

    @classmethod
    def get(cls, **kwargs) -> Optional["Model"]:
        """Fetch a single row, or None. Raises if more than one matches."""
        results = QuerySet(cls).filter(**kwargs).limit(2)
        if len(results) > 1:
            raise ValueError(f"get() matched multiple rows for {kwargs!r}")
        return results[0] if results else None

    @classmethod
    def get_or_create(cls, defaults: Optional[dict] = None, **kwargs) -> tuple["Model", bool]:
        """Fetch a row matching kwargs, or create one.

        Returns (instance, created) where created is True if a new row was inserted.
        """
        obj = cls.get(**kwargs)
        if obj is not None:
            return obj, False
        create_kwargs = dict(kwargs)
        if defaults:
            create_kwargs.update(defaults)
        obj = cls(**create_kwargs)
        obj.save()
        return obj, True
