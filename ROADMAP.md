# BitORM Roadmap

## ~~Migrations~~ (Done)

Implemented in `bitorm/db.py`. See [docs/migrations.md](docs/migrations.md) for usage. Forward-only file-based migrations with auto-detection via `make_migration()` and `migrate()`. Migration files live in `{db_name}_migrations/`. Supports add/remove tables, add/remove columns, and column type/constraint changes via the 12-step table rebuild pattern.

**Not yet supported:**
- Column renames (write a manual migration with `ALTER TABLE ... RENAME COLUMN`)
- Index management

## ~~CLI~~ (Done)

Implemented in `bitorm/cli.py`, exposed as the `bitorm` console script. `bitorm init`
scaffolds `[tool.bitorm]` config + a starter `models.py`; `bitorm makemigrations` and
`bitorm migrate` wrap the migration functions. See [docs/migrations.md](docs/migrations.md) for usage.

## ~~Raw SQL Escape Hatch~~ (Done)

Implemented as `Model.raw(sql, params)` and `Database.raw(sql, params)`. See [docs/querying.md](docs/querying.md) for usage.

## ~~Transactions~~ (Done)

Implemented as `Database.atomic()` in `bitorm/db.py`. See [docs/concurrency.md](docs/concurrency.md) for usage. Context
manager that commits on success and rolls back on exception; nesting uses
SAVEPOINTs; all ORM write operations participate automatically (`db.commit()` is
deferred inside a block). Migrations are applied atomically (one transaction per
migration file).

## ~~Bulk Inserts~~ (Done)

Implemented as `Model.bulk_create(objs)` in `bitorm/models.py`. All rows are
validated up front and inserted in a single transaction (all-or-nothing); generated
primary keys are populated. Orders of magnitude faster than `save()` in a loop.

## ~~JSONField~~ (Done)

Implemented in `bitorm/fields.py`. Stores JSON-serializable values as TEXT via
`json.dumps`/`json.loads`. See [docs/fields.md](docs/fields.md) for usage.

## ~~Concurrency (WAL + fork safety)~~ (Done)

File-backed databases open in WAL mode with a 5s busy timeout; connections detect
`fork()` and reopen lazily in the child process (PyTorch DataLoader support). See
[docs/concurrency.md](docs/concurrency.md).

**Not yet supported:**
- Sharing one connection across threads
