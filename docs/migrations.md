# Migrations

[← Back to README](../README.md) · [Models & Fields](fields.md) · [Querying](querying.md) · [Relationships](relationships.md) · [Transactions & Concurrency](concurrency.md)

`create_table()` works for new projects, but once your database has data you need
migrations to evolve the schema without losing it. BitORM's migrations are
forward-only, file-based, and auto-detected from your models.

## Command-Line Interface

BitORM ships a CLI for scaffolding and migrations. Run it through uv:

```
uv run bitorm init             # scaffold config + a starter models.py
uv run bitorm makemigrations   # generate a migration from model changes
uv run bitorm migrate          # apply pending migrations
```

### Setup

`bitorm init` adds a `[tool.bitorm]` section to your `pyproject.toml` and creates a
starter `models.py`:

```toml
[tool.bitorm]
database = "app.db"   # path to your SQLite database file
models = "models"     # importable module that defines your Model subclasses
```

Point `database` at wherever you want the DB file, and `models` at the module that
declares your tables (a dotted path like `myapp.models` works too).

### Workflow

```
# 1. Edit models.py to define your tables.
# 2. Generate a migration (optionally name it):
uv run bitorm makemigrations initial
#    → Created app_migrations/001_initial.py
# 3. Apply it:
uv run bitorm migrate
#    → Applied 001_initial
```

`makemigrations` diffs your models against the database and writes a migration file;
`migrate` applies any that haven't run yet. Both are thin wrappers over the
`make_migration()` / `migrate()` functions documented below, so you can mix CLI and
programmatic use freely.

You can also invoke the CLI as a module: `uv run python -m bitorm migrate`.

## Generating a Migration

```python
from bitorm import connect, make_migration, migrate

db = connect("app.db")

# Auto-detect differences between your models and the database schema
make_migration(db, name="initial")
# Creates app_migrations/001_initial.py
```

`make_migration` introspects the database with `PRAGMA table_info` and compares it to your current model definitions. It generates a migration file with the necessary SQL.

You can scope it to specific models:

```python
make_migration(db, name="add_users", models=[User, Post])
```

If no models are passed, all `Model` subclasses are included.

## Applying Migrations

```python
applied = migrate(db)
# Returns list of applied migration names, e.g. ["001_initial"]
```

`migrate` discovers all `NNN_name.py` files in the migrations directory, skips any already applied, and runs the rest in order. Applied migrations are tracked in a `_migrations` table.

Each migration is applied in its own [transaction](concurrency.md), so a failure
mid-migration rolls back cleanly instead of leaving the schema half-migrated.

## Migration Files

Generated files are plain Python with a `forward(db)` function:

```python
"""
  - Create table user
  - Create table post
"""


def forward(db):
    db.execute("CREATE TABLE \"user\" (\"id\" INTEGER PRIMARY KEY, \"name\" TEXT)")
    db.execute("CREATE TABLE \"post\" (\"id\" INTEGER PRIMARY KEY, \"title\" TEXT)")
    db.commit()
```

You can also write migration files by hand for operations that auto-detection can't handle, like column renames or data migrations. Any file matching `NNN_name.py` with a `forward(db)` function will be picked up.

## Supported Operations

- Add/remove tables
- Add/remove columns (`ALTER TABLE ADD/DROP COLUMN`)
- Column type, nullability, or uniqueness changes (uses SQLite's table rebuild pattern)

## Migration Directory

The directory is derived from the database filename: `app.db` uses `app_migrations/`, `data.db` uses `data_migrations/`, etc. In-memory databases don't support migrations — use `create_table()` directly.
