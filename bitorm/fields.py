from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Any, Optional

from .db import _db


# Marker so "no default given" is distinguishable from "default is None".
_UNSET = object()


def _qi(name: str) -> str:
    """Quote a SQL identifier (table or column name) with double quotes."""
    return '"' + name.replace('"', '""') + '"'


# --------------------------------------------------------------------------- #
# Field / column definitions
# --------------------------------------------------------------------------- #

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


class FileField:
    """Reference a file on disk. Stores a relative path as TEXT in the DB."""

    def __init__(self, *, base_dir: str = "", nullable: bool = True):
        self.base_dir = base_dir
        self.nullable = nullable


class FileRef:
    """Returned when accessing a FileField. Provides file operations."""

    def __init__(self, relative: str, base_path: Path):
        self.relative = relative
        self._base = base_path
        self._hash_cache: Optional[str] = None

    @property
    def path(self) -> Path:
        return self._base / self.relative

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.path.read_text(encoding=encoding)

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()

    def read_json(self, encoding: str = "utf-8") -> Any:
        return json.loads(self.read_text(encoding=encoding))

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def size(self) -> int:
        return self.path.stat().st_size

    @property
    def ext(self) -> str:
        return self.path.suffix

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def hash(self) -> str:
        if self._hash_cache is None:
            h = hashlib.sha256()
            with open(self.path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            self._hash_cache = h.hexdigest()
        return self._hash_cache

    def __str__(self) -> str:
        return self.relative

    def __repr__(self) -> str:
        return f"FileRef({self.relative!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, FileRef):
            return self.relative == other.relative and self._base == other._base
        return NotImplemented


class _FileDescriptor:
    """Installed on a Model class for each FileField. Returns FileRef on access."""

    def __init__(self, name: str, base_dir: str):
        self.name = name
        self.base_dir = base_dir

    def _resolve_base(self) -> Path:
        db = _db()
        if db.path == ":memory:" or not db.path:
            return Path(self.base_dir) if self.base_dir else Path(".")
        db_dir = Path(os.path.abspath(db.path)).parent
        if self.base_dir:
            return db_dir / self.base_dir
        return db_dir

    def __get__(self, instance: Any, owner: type) -> Any:
        if instance is None:
            return self
        raw = instance.__dict__.get(self.name)
        if raw is None:
            return None
        return FileRef(raw, self._resolve_base())

    def __set__(self, instance: Any, value: Any) -> None:
        if value is None:
            instance.__dict__[self.name] = None
        elif isinstance(value, FileRef):
            instance.__dict__[self.name] = value.relative
        else:
            instance.__dict__[self.name] = str(value)


# --------------------------------------------------------------------------- #
# Column metadata
# --------------------------------------------------------------------------- #

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
        self.references = references

    @property
    def sql_type(self) -> str:
        return self.PY_TO_SQL.get(self.py_type, "TEXT")

    def ddl(self) -> str:
        parts = [_qi(self.name), self.sql_type]
        if self.field.primary_key:
            parts.append("PRIMARY KEY")
        else:
            if not self.field.nullable:
                parts.append("NOT NULL")
            if self.field.unique:
                parts.append("UNIQUE")
        if self.references:
            parts.append(f"REFERENCES {self.references}")
        return " ".join(parts)

    def to_db(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, FileRef):
            return value.relative
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

    def _validate(self, value: Any) -> None:
        if value is None:
            if not self.field.nullable and not self.field.primary_key:
                raise ValueError(
                    f"Field '{self.name}' cannot be None (not nullable)."
                )
            return

        if isinstance(value, FileRef):
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
        on_delete: str = "CASCADE",
    ):
        self.to = to
        self.related_name = related_name
        self.nullable = nullable
        self.on_delete = on_delete


class _ForwardFK:
    """Descriptor for the owning side: post.author."""

    def __init__(self, name: str, to: type):
        self.name = name
        self.col = name + "_id"
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
        self.fk_col = fk_col

    def __get__(self, instance, owner):
        if instance is None:
            return self
        from .models import QuerySet
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
        self.this_col = this_col
        self.other_col = other_col
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

    def all(self) -> list:
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
