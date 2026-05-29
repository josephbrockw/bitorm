# BitORM Roadmap

## Migrations

**Problem:** There's `create_table()` and `drop_table()`, but no way to evolve a schema. Adding a column to an existing table requires dropping and recreating it, losing all data.

**Planned API:**

```python
from bitorm import connect, migrate

db = connect("app.db")

# Detect differences between model definitions and the actual schema
migrate(db)
```

`migrate()` introspects the SQLite schema (`PRAGMA table_info`, `PRAGMA foreign_key_list`) and compares it to the current model definitions. It generates and executes the necessary ALTER TABLE statements.

**Supported operations:**
- Add column (`ALTER TABLE ... ADD COLUMN ...`)
- Drop column (SQLite 3.35+ supports `ALTER TABLE ... DROP COLUMN`)
- Rename column (`ALTER TABLE ... RENAME COLUMN ... TO ...`, SQLite 3.25+)
- Add/remove table

**Out of scope (initially):**
- Column type changes (requires table rebuild)
- Index management
- Migration history / versioning (no migrations table — this is a diff-and-apply tool)

**Approach:** Since SQLite has limited ALTER TABLE support, complex changes (type changes, reordering) would use the [12-step table rebuild](https://www.sqlite.org/lang_altertable.html#making_other_kinds_of_table_schema_changes) pattern: create new table, copy data, drop old, rename. This is the same approach sqlite-utils uses for its `transform` API.

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
