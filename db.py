from __future__ import annotations

import importlib.util
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


__all__ = ["Database", "connect", "make_migration", "migrate"]


# --------------------------------------------------------------------------- #
# Connection handling (moved from bitorm.py)
# --------------------------------------------------------------------------- #

class Database:
    """Thin wrapper over a sqlite3 connection with sensible defaults."""

    def __init__(self, path: str = ":memory:"):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def raw(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """Execute raw SQL and return a list of sqlite3.Row objects."""
        return self.execute(sql, params).fetchall()

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


_default_db: Optional[Database] = None


def connect(path: str = ":memory:") -> Database:
    """Open (or replace) the default database used by all models."""
    global _default_db
    _default_db = Database(path)
    return _default_db


def _db() -> Database:
    if _default_db is None:
        raise RuntimeError("No database connected. Call connect(path) first.")
    return _default_db


# --------------------------------------------------------------------------- #
# Schema introspection
# --------------------------------------------------------------------------- #

@dataclass
class ColumnSchema:
    name: str
    sql_type: str
    notnull: bool
    primary_key: bool
    unique: bool
    references: Optional[str] = None


@dataclass
class TableSchema:
    name: str
    columns: dict[str, ColumnSchema] = field(default_factory=dict)


@dataclass
class SchemaDiff:
    added_tables: list[str] = field(default_factory=list)
    removed_tables: list[str] = field(default_factory=list)
    added_columns: dict[str, list[ColumnSchema]] = field(default_factory=dict)
    removed_columns: dict[str, list[str]] = field(default_factory=dict)
    changed_columns: dict[str, list[tuple[str, ColumnSchema, ColumnSchema]]] = field(
        default_factory=dict
    )

    def is_empty(self) -> bool:
        return not (
            self.added_tables
            or self.removed_tables
            or self.added_columns
            or self.removed_columns
            or self.changed_columns
        )


_INTERNAL_TABLES = {"_migrations"}


def _get_db_tables(db: Database) -> set[str]:
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r["name"] for r in rows} - _INTERNAL_TABLES


def _get_table_columns(db: Database, table: str) -> list[dict]:
    return [dict(r) for r in db.execute(f"PRAGMA table_info(\"{table}\")").fetchall()]


def _get_foreign_keys(db: Database, table: str) -> dict[str, str]:
    """Return {local_col: 'referenced_table(referenced_col) ON DELETE action'}."""
    rows = db.execute(f"PRAGMA foreign_key_list(\"{table}\")").fetchall()
    result = {}
    for r in rows:
        ref = f"\"{r['table']}\"(\"{r['to']}\")"
        if r["on_delete"] and r["on_delete"] != "NO ACTION":
            ref += f" ON DELETE {r['on_delete']}"
        result[r["from"]] = ref
    return result


def _get_unique_columns(db: Database, table: str) -> set[str]:
    indexes = db.execute(f"PRAGMA index_list(\"{table}\")").fetchall()
    unique_cols: set[str] = set()
    for idx in indexes:
        if not idx["unique"]:
            continue
        info = db.execute(f"PRAGMA index_info(\"{idx['name']}\")").fetchall()
        if len(info) == 1:
            unique_cols.add(info[0]["name"])
    return unique_cols


def _get_db_schema(db: Database) -> dict[str, TableSchema]:
    schema: dict[str, TableSchema] = {}
    for table_name in _get_db_tables(db):
        cols_raw = _get_table_columns(db, table_name)
        fks = _get_foreign_keys(db, table_name)
        uniques = _get_unique_columns(db, table_name)
        columns: dict[str, ColumnSchema] = {}
        for c in cols_raw:
            columns[c["name"]] = ColumnSchema(
                name=c["name"],
                sql_type=c["type"] or "TEXT",
                notnull=bool(c["notnull"]),
                primary_key=bool(c["pk"]),
                unique=c["name"] in uniques,
                references=fks.get(c["name"]),
            )
        schema[table_name] = TableSchema(name=table_name, columns=columns)
    return schema


# --------------------------------------------------------------------------- #
# Model schema extraction (lazy import to avoid circular dep)
# --------------------------------------------------------------------------- #

def _all_model_subclasses(base: type) -> list[type]:
    result = []
    for sub in base.__subclasses__():
        result.append(sub)
        result.extend(_all_model_subclasses(sub))
    return result


def _get_model_schema(models: Optional[list[type]] = None) -> dict[str, TableSchema]:
    from bitorm import Model

    if models is None:
        models = _all_model_subclasses(Model)
    schema: dict[str, TableSchema] = {}
    for cls in models:
        table_name = cls._table
        columns: dict[str, ColumnSchema] = {}
        for col in cls._columns:
            columns[col.name] = ColumnSchema(
                name=col.name,
                sql_type=col.sql_type,
                notnull=(not col.field.nullable and not col.field.primary_key),
                primary_key=col.field.primary_key,
                unique=col.field.unique,
                references=col.references,
            )
        schema[table_name] = TableSchema(name=table_name, columns=columns)

        for join_table, owner_col, target_col, target in cls._m2m:
            if join_table not in schema:
                schema[join_table] = TableSchema(
                    name=join_table,
                    columns={
                        owner_col: ColumnSchema(
                            owner_col, "INTEGER", True, False, False,
                            f"\"{cls._table}\"(\"{cls._pk}\") ON DELETE CASCADE",
                        ),
                        target_col: ColumnSchema(
                            target_col, "INTEGER", True, False, False,
                            f"\"{target._table}\"(\"{target._pk}\") ON DELETE CASCADE",
                        ),
                    },
                )
    return schema


# --------------------------------------------------------------------------- #
# Schema diffing
# --------------------------------------------------------------------------- #

def _diff_schemas(
    db_schema: dict[str, TableSchema],
    model_schema: dict[str, TableSchema],
) -> SchemaDiff:
    diff = SchemaDiff()

    db_tables = set(db_schema)
    model_tables = set(model_schema)

    diff.added_tables = sorted(model_tables - db_tables)
    diff.removed_tables = sorted(db_tables - model_tables)

    for table in db_tables & model_tables:
        db_cols = db_schema[table].columns
        model_cols = model_schema[table].columns
        db_col_names = set(db_cols)
        model_col_names = set(model_cols)

        added = model_col_names - db_col_names
        if added:
            diff.added_columns[table] = [model_cols[n] for n in sorted(added)]

        removed = db_col_names - model_col_names
        if removed:
            diff.removed_columns[table] = sorted(removed)

        for col_name in db_col_names & model_col_names:
            db_c = db_cols[col_name]
            m_c = model_cols[col_name]
            if (
                db_c.sql_type != m_c.sql_type
                or db_c.notnull != m_c.notnull
                or db_c.unique != m_c.unique
            ):
                diff.changed_columns.setdefault(table, []).append(
                    (col_name, db_c, m_c)
                )

    return diff


# --------------------------------------------------------------------------- #
# SQL generation
# --------------------------------------------------------------------------- #

def _qi(name: str) -> str:
    from bitorm import _qi as bitorm_qi
    return bitorm_qi(name)


def _col_ddl(col: ColumnSchema) -> str:
    parts = [_qi(col.name), col.sql_type]
    if col.primary_key:
        parts.append("PRIMARY KEY")
    else:
        if col.notnull:
            parts.append("NOT NULL")
        if col.unique:
            parts.append("UNIQUE")
    if col.references:
        parts.append(f"REFERENCES {col.references}")
    return " ".join(parts)


def _generate_forward_sql(
    diff: SchemaDiff,
    model_schema: dict[str, TableSchema],
) -> list[str]:
    stmts: list[str] = []

    for table in diff.added_tables:
        ts = model_schema[table]
        cols_ddl = ", ".join(_col_ddl(c) for c in ts.columns.values())
        stmts.append(f"CREATE TABLE {_qi(table)} ({cols_ddl})")

    for table in diff.removed_tables:
        stmts.append(f"DROP TABLE IF EXISTS {_qi(table)}")

    for table, columns in diff.added_columns.items():
        needs_rebuild = any(
            (c.notnull and not c.primary_key) or c.unique for c in columns
        )
        if needs_rebuild:
            stmts.extend(_rebuild_table_sql(table, model_schema[table]))
        else:
            for col in columns:
                stmts.append(
                    f"ALTER TABLE {_qi(table)} ADD COLUMN {_col_ddl(col)}"
                )

    for table, col_names in diff.removed_columns.items():
        for col_name in col_names:
            stmts.append(
                f"ALTER TABLE {_qi(table)} DROP COLUMN {_qi(col_name)}"
            )

    for table, changes in diff.changed_columns.items():
        rebuild = _rebuild_table_sql(table, model_schema[table])
        stmts.extend(rebuild)

    return stmts


def _rebuild_table_sql(table: str, target: TableSchema) -> list[str]:
    tmp = f"{table}__new"
    cols_ddl = ", ".join(_col_ddl(c) for c in target.columns.values())
    col_names = ", ".join(_qi(c) for c in target.columns)
    return [
        f"CREATE TABLE {_qi(tmp)} ({cols_ddl})",
        f"INSERT INTO {_qi(tmp)} ({col_names}) "
        f"SELECT {col_names} FROM {_qi(table)}",
        f"DROP TABLE {_qi(table)}",
        f"ALTER TABLE {_qi(tmp)} RENAME TO {_qi(table)}",
    ]


# --------------------------------------------------------------------------- #
# Migration file writer
# --------------------------------------------------------------------------- #

def _migrations_dir(db: Database) -> str:
    if db.path == ":memory:" or not db.path:
        raise ValueError("Cannot create migrations for :memory: databases.")
    stem = os.path.splitext(os.path.basename(db.path))[0]
    parent = os.path.dirname(os.path.abspath(db.path))
    return os.path.join(parent, f"{stem}_migrations")


def _next_migration_number(migrations_dir: str) -> int:
    if not os.path.isdir(migrations_dir):
        return 1
    numbers = []
    for f in os.listdir(migrations_dir):
        m = re.match(r"^(\d+)_.+\.py$", f)
        if m:
            numbers.append(int(m.group(1)))
    return max(numbers, default=0) + 1


def _describe_diff(diff: SchemaDiff) -> str:
    lines = []
    for t in diff.added_tables:
        lines.append(f"  - Create table {t}")
    for t in diff.removed_tables:
        lines.append(f"  - Drop table {t}")
    for t, cols in diff.added_columns.items():
        for c in cols:
            lines.append(f"  - Add column {t}.{c.name}")
    for t, names in diff.removed_columns.items():
        for n in names:
            lines.append(f"  - Remove column {t}.{n}")
    for t, changes in diff.changed_columns.items():
        for name, old, new in changes:
            lines.append(f"  - Alter column {t}.{name}")
    return "\n".join(lines)


def make_migration(
    db: Database,
    name: str = "auto",
    models: Optional[list[type]] = None,
) -> Optional[str]:
    """Auto-detect schema changes and write a migration file.

    Returns the path to the generated file, or None if no changes detected.
    Pass models to limit scope; otherwise all Model subclasses are included.
    """
    mdir = _migrations_dir(db)
    db_schema = _get_db_schema(db)
    model_schema = _get_model_schema(models)
    diff = _diff_schemas(db_schema, model_schema)

    if diff.is_empty():
        return None

    sql_stmts = _generate_forward_sql(diff, model_schema)
    num = _next_migration_number(mdir)
    filename = f"{num:03d}_{name}.py"
    os.makedirs(mdir, exist_ok=True)
    filepath = os.path.join(mdir, filename)

    description = _describe_diff(diff)
    body_lines = []
    for stmt in sql_stmts:
        escaped = stmt.replace("\\", "\\\\").replace('"', '\\"')
        body_lines.append(f'    db.execute("{escaped}")')
    body_lines.append("    db.commit()")

    content = (
        f'"""\n{description}\n"""\n'
        f"\n\ndef forward(db):\n"
        + "\n".join(body_lines)
        + "\n"
    )

    with open(filepath, "w") as f:
        f.write(content)

    return filepath


# --------------------------------------------------------------------------- #
# Migration runner
# --------------------------------------------------------------------------- #

def _ensure_migrations_table(db: Database) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS _migrations ("
        "id INTEGER PRIMARY KEY, "
        "name TEXT NOT NULL UNIQUE, "
        "applied_at TEXT NOT NULL)"
    )
    db.commit()


def _applied_migrations(db: Database) -> list[str]:
    rows = db.execute(
        "SELECT name FROM _migrations ORDER BY id"
    ).fetchall()
    return [r["name"] for r in rows]


def _discover_migrations(mdir: str) -> list[tuple[str, str]]:
    """Return [(name, filepath)] sorted by numeric prefix."""
    if not os.path.isdir(mdir):
        return []
    results = []
    for f in os.listdir(mdir):
        m = re.match(r"^(\d+)_.+\.py$", f)
        if m:
            name = f[:-3]  # strip .py
            results.append((int(m.group(1)), name, os.path.join(mdir, f)))
    results.sort(key=lambda x: x[0])
    return [(name, path) for _, name, path in results]


def _load_migration(filepath: str, name: str):
    spec = importlib.util.spec_from_file_location(name, filepath)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load migration: {filepath}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrate(db: Database) -> list[str]:
    """Apply all pending migrations. Returns list of applied migration names."""
    mdir = _migrations_dir(db)
    _ensure_migrations_table(db)
    applied = set(_applied_migrations(db))
    all_migrations = _discover_migrations(mdir)
    newly_applied = []

    for name, filepath in all_migrations:
        if name in applied:
            continue
        module = _load_migration(filepath, name)
        if not hasattr(module, "forward"):
            raise AttributeError(
                f"Migration {name} has no forward() function."
            )
        module.forward(db)
        db.execute(
            "INSERT INTO _migrations (name, applied_at) VALUES (?, ?)",
            (name, datetime.now().isoformat()),
        )
        db.commit()
        newly_applied.append(name)

    return newly_applied
