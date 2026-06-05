"""
microrm — a super lightweight ORM for SQLite, standard library only.

Design goals:
    * Single file, zero third-party dependencies (sqlite3 + typing + datetime).
    * Declare tables as classes using type annotations.
    * Safe by default: every value goes through a parameterized query.
    * Small, readable, hackable surface area.

Quick start:

    from microrm import Model, Field, connect
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
from datetime import datetime, date
from typing import Any, Iterator, Optional

from db import Database, connect, _db, make_migration, migrate  # noqa: F401

__all__ = [
    "Model", "Field", "ForeignKey", "ManyToMany",
    "connect", "Database", "make_migration", "migrate",
]


# --------------------------------------------------------------------------- #
# Field / column definitions
# --------------------------------------------------------------------------- #

# Marker so "no default given" is distinguishable from "default is None".
_UNSET = object()


class Field:
    """Optional per-column metadata. Use as the default value of an annotation."""

    def __init__(
        self,
        *,
        primary_key: bool = False,
        unique: bool = False,
        nullable: bool = True,
        default: Any = _UNSET,
        default_factory: Optional[Any] = None,
    ):
        if default is not _UNSET and default_factory is not None:
            raise ValueError("Cannot specify both 'default' and 'default_factory'.")
        self.primary_key = primary_key
        self.unique = unique
        self.nullable = nullable
        self.default = default
        self.default_factory = default_factory


class _Column:
    """Resolved metadata for one column (built at class creation time)."""

    PY_TO_SQL = {
        int: "INTEGER",
        float: "REAL",
        str: "TEXT",
        bytes: "BLOB",
        bool: "INTEGER",
        datetime: "TEXT",
        date: "TEXT",
    }

    def __init__(self, name: str, py_type: type, field: Field, references: Optional[str] = None):
        self.name = name
        self.py_type = py_type
        self.field = field
        self.references = references  # e.g. "user(id) ON DELETE CASCADE", or None

    @property
    def sql_type(self) -> str:
        return self.PY_TO_SQL.get(self.py_type, "TEXT")

    def ddl(self) -> str:
        parts = [_qi(self.name), self.sql_type]
        if self.field.primary_key:
            # INTEGER PRIMARY KEY = rowid alias = autoincrement in SQLite.
            parts.append("PRIMARY KEY")
        else:
            if not self.field.nullable:
                parts.append("NOT NULL")
            if self.field.unique:
                parts.append("UNIQUE")
        if self.references:
            parts.append(f"REFERENCES {self.references}")
        return " ".join(parts)

    # --- value (de)serialization between Python and SQLite -----------------
    def to_db(self, value: Any) -> Any:
        if value is None:
            return None
        if self.py_type in (datetime, date):
            return value.isoformat()
        if self.py_type is bool:
            return int(value)
        return value

    def from_db(self, value: Any) -> Any:
        if value is None:
            return None
        if self.py_type is datetime:
            return datetime.fromisoformat(value)
        if self.py_type is date:
            return date.fromisoformat(value)
        if self.py_type is bool:
            return bool(value)
        return value

    # --- validation ----------------------------------------------------------
    def _validate(self, value: Any) -> None:
        if value is None:
            if not self.field.nullable and not self.field.primary_key:
                raise ValueError(
                    f"Field '{self.name}' cannot be None (not nullable)."
                )
            return

        if self.py_type not in self.PY_TO_SQL:
            return

        if self.py_type is float:
            if not isinstance(value, (int, float)):
                raise TypeError(
                    f"Field '{self.name}' expects float, got {type(value).__name__}."
                )
        elif self.py_type is bool:
            if not isinstance(value, (bool, int)):
                raise TypeError(
                    f"Field '{self.name}' expects bool, got {type(value).__name__}."
                )
        elif self.py_type is date:
            if isinstance(value, datetime) or not isinstance(value, date):
                raise TypeError(
                    f"Field '{self.name}' expects date, got {type(value).__name__}."
                )
        else:
            if not isinstance(value, self.py_type):
                raise TypeError(
                    f"Field '{self.name}' expects {self.py_type.__name__}, "
                    f"got {type(value).__name__}."
                )


# --------------------------------------------------------------------------- #
# Foreign keys (one-to-many / many-to-one)
# --------------------------------------------------------------------------- #

class ForeignKey:
    """Declare as the default of an annotation:  author: User = ForeignKey(User)

    Creates an INTEGER `<name>_id` column referencing the target's primary key,
    a forward accessor (`post.author` -> User object, lazily fetched/cached),
    and a reverse accessor on the target (`user.posts` -> QuerySet).
    """

    def __init__(
        self,
        to: type,
        *,
        related_name: Optional[str] = None,
        nullable: bool = True,
        on_delete: str = "CASCADE",  # CASCADE | SET NULL | RESTRICT | NO ACTION
    ):
        self.to = to
        self.related_name = related_name
        self.nullable = nullable
        self.on_delete = on_delete


class _ForwardFK:
    """Descriptor for the owning side: post.author."""

    def __init__(self, name: str, to: type):
        self.name = name          # "author"
        self.col = name + "_id"   # "author_id" (the real column)
        self.to = to

    def __get__(self, instance, owner):
        if instance is None:
            return self
        fk_value = getattr(instance, self.col, None)
        if fk_value is None:
            return None
        cache = instance.__dict__.setdefault("_fk_cache", {})
        if self.name not in cache:
            cache[self.name] = self.to.get(**{self.to._pk: fk_value})
        return cache[self.name]

    def __set__(self, instance, value):
        if value is None:
            setattr(instance, self.col, None)
        else:
            pk_value = getattr(value, value._pk)
            if pk_value is None:
                raise ValueError("Cannot assign an unsaved object to a foreign key. Save it first.")
            setattr(instance, self.col, pk_value)
        instance.__dict__.get("_fk_cache", {}).pop(self.name, None)


class _ReverseFK:
    """Descriptor installed on the target: user.posts -> QuerySet of related rows."""

    def __init__(self, related_model: type, fk_col: str):
        self.related_model = related_model
        self.fk_col = fk_col  # e.g. "author_id" on Post

    def __get__(self, instance, owner):
        if instance is None:
            return self
        pk_value = getattr(instance, instance._pk)
        return QuerySet(self.related_model).filter(**{self.fk_col: pk_value})


# --------------------------------------------------------------------------- #
# Many-to-many (via an auto-created join table)
# --------------------------------------------------------------------------- #

class ManyToMany:
    """Declare as the default of an annotation:  tags: list = ManyToMany(Tag)

    Auto-creates a join table (e.g. `post_tag(post_id, tag_id)`) when the
    owning model's table is created, and exposes a manager on both sides:
        post.tags.add(t) / .remove(t) / .clear() / .all() / .count()
        tag.posts.all()  (reverse side, default name "<owner>s")
    """

    def __init__(self, to: type, *, related_name: Optional[str] = None):
        self.to = to
        self.related_name = related_name


class _M2MManager:
    """Bound to one instance; operates on the join table for that instance."""

    def __init__(self, join_table, this_col, other_col, other_model, this_pk_value):
        self.join_table = join_table
        self.this_col = this_col          # column for the owning instance
        self.other_col = other_col        # column for the related rows
        self.other_model = other_model
        self.this_pk_value = this_pk_value

    def _require_saved(self):
        if self.this_pk_value is None:
            raise ValueError("Save the object before using its m2m relation.")

    def add(self, *objs) -> None:
        self._require_saved()
        for o in objs:
            if getattr(o, o._pk) is None:
                raise ValueError("All objects must be saved before adding to a many-to-many relation.")
        rows = [(self.this_pk_value, getattr(o, o._pk)) for o in objs]
        _db().conn.executemany(
            f"INSERT OR IGNORE INTO {_qi(self.join_table)} "
            f"({_qi(self.this_col)}, {_qi(self.other_col)}) VALUES (?, ?)",
            rows,
        )
        _db().commit()

    def remove(self, *objs) -> None:
        self._require_saved()
        rows = [(self.this_pk_value, getattr(o, o._pk)) for o in objs]
        _db().conn.executemany(
            f"DELETE FROM {_qi(self.join_table)} "
            f"WHERE {_qi(self.this_col)} = ? AND {_qi(self.other_col)} = ?",
            rows,
        )
        _db().commit()

    def clear(self) -> None:
        self._require_saved()
        _db().execute(
            f"DELETE FROM {_qi(self.join_table)} WHERE {_qi(self.this_col)} = ?",
            (self.this_pk_value,),
        )
        _db().commit()

    def all(self) -> list["Model"]:
        self._require_saved()
        other = self.other_model
        sql = (
            f"SELECT o.* FROM {_qi(other._table)} o "
            f"JOIN {_qi(self.join_table)} j ON j.{_qi(self.other_col)} = o.{_qi(other._pk)} "
            f"WHERE j.{_qi(self.this_col)} = ?"
        )
        rows = _db().execute(sql, (self.this_pk_value,)).fetchall()
        return [other._from_row(r) for r in rows]

    def count(self) -> int:
        self._require_saved()
        sql = f"SELECT COUNT(*) AS n FROM {_qi(self.join_table)} WHERE {_qi(self.this_col)} = ?"
        return _db().execute(sql, (self.this_pk_value,)).fetchone()["n"]

    def __iter__(self):
        return iter(self.all())


class _M2MDescriptor:
    """Returns an _M2MManager bound to the accessed instance."""

    def __init__(self, join_table, this_col, other_col, other_model):
        self.join_table = join_table
        self.this_col = this_col
        self.other_col = other_col
        self.other_model = other_model

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return _M2MManager(
            self.join_table,
            self.this_col,
            self.other_col,
            self.other_model,
            getattr(instance, instance._pk, None),
        )


# --------------------------------------------------------------------------- #
# WHERE-clause operators (Django-style suffixes)
# --------------------------------------------------------------------------- #

def _qi(name: str) -> str:
    """Quote a SQL identifier (table or column name) with double quotes."""
    return '"' + name.replace('"', '""') + '"'


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
