# BitORM Roadmap

## ~~Migrations~~ (Done)

Implemented in `db.py`. See README for usage. Forward-only file-based migrations with auto-detection via `make_migration()` and `migrate()`. Migration files live in `{db_name}_migrations/`. Supports add/remove tables, add/remove columns, and column type/constraint changes via the 12-step table rebuild pattern.

**Not yet supported:**
- Column renames (write a manual migration with `ALTER TABLE ... RENAME COLUMN`)
- Index management

## ~~Raw SQL Escape Hatch~~ (Done)

Implemented as `Model.raw(sql, params)` and `Database.raw(sql, params)`. See README for usage.

## Transactions

**Problem:** Every `save()`, `delete()`, and M2M operation commits immediately. If you save two related objects and the second fails, the first is already committed — there's no way to roll back.

**Planned API:**

```python
from bitorm import connect

db = connect("app.db")

with db.atomic():
    author = Author(name="Ada")
    author.save()
    post = Post(title="Hello", author=author)
    post.save()
    # both committed at the end, or neither if an exception occurs
```

`db.atomic()` returns a context manager that wraps the block in a SQLite transaction. On success, it commits. On exception, it rolls back and re-raises. Nesting uses SAVEPOINTs.

**Scope:**
- `Database.atomic()` context manager
- SAVEPOINT support for nesting
- All ORM write operations (`save`, `delete`, `QuerySet.delete`, M2M `add`/`remove`/`clear`) participate automatically
