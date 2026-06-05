# BitORM Roadmap

## ~~Migrations~~ (Done)

Implemented in `db.py`. See README for usage. Forward-only file-based migrations with auto-detection via `make_migration()` and `migrate()`. Migration files live in `{db_name}_migrations/`. Supports add/remove tables, add/remove columns, and column type/constraint changes via the 12-step table rebuild pattern.

**Not yet supported:**
- Column renames (write a manual migration with `ALTER TABLE ... RENAME COLUMN`)
- Index management

## Raw SQL Escape Hatch

**Problem:** The ORM can't express every query. Users who need window functions, CTEs, complex joins, or database-specific features have to bypass the ORM entirely and manage their own connections.

**Planned API:**

```python
# Returns a list of model instances
users = User.raw("SELECT * FROM simpleuser WHERE age > ? ORDER BY name", [18])

# Returns raw sqlite3.Row objects when no model mapping is needed
rows = db.raw("SELECT name, COUNT(*) as cnt FROM simpleuser GROUP BY name")
```

**Scope:**
- `Model.raw(sql, params)` — executes SQL, maps results through `_from_row()`, returns a list of model instances. The SQL must select all columns the model expects.
- `Database.raw(sql, params)` — executes SQL, returns a list of `sqlite3.Row` objects. For queries that don't map to a model (aggregations, joins across multiple tables, etc.).
- Both use parameterized queries — no f-string SQL.

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
