# Models & Fields

[← Back to README](../README.md) · [Querying](querying.md) · [Relationships](relationships.md) · [Migrations](migrations.md) · [Transactions & Concurrency](concurrency.md)

## Connecting

```python
from bitorm import connect

db = connect("app.db")       # file-based database
db = connect(":memory:")     # in-memory database (default)
```

`connect()` sets the module-level default database used by all models. Calling it again replaces the previous connection.

## Defining Models

Declare columns with type annotations. BitORM maps Python types to SQLite types:

| Python type  | SQLite type |
|-------------|-------------|
| `int`       | INTEGER     |
| `float`     | REAL        |
| `str`       | TEXT        |
| `bytes`     | BLOB        |
| `bool`      | INTEGER     |
| `datetime`  | TEXT (ISO)  |
| `date`      | TEXT (ISO)  |

```python
class User(Model):
    name: str
    email: str = Field(unique=True)
    age: int = 0
    bio: str = Field(nullable=False)
```

An `id` integer primary key is added automatically unless you declare your own:

```python
class Product(Model):
    sku: str = Field(primary_key=True)
    name: str
    price: float
```

### Custom Table Names

```python
class UserAccount(Model):
    __tablename__ = "accounts"
    name: str
```

### Field Options

```python
Field(
    primary_key=False,    # make this column the primary key
    unique=False,         # add a UNIQUE constraint
    nullable=True,        # allow NULL values
    default=...,          # static default value
    default_factory=...,  # callable, invoked per-instance (e.g. datetime.now)
)
```

You can also use a bare value as a default:

```python
class User(Model):
    age: int = 0              # same as Field(default=0)
    name: str = Field()       # nullable, no default
```

### Model Inheritance

Child models inherit columns from their parents:

```python
class TimestampMixin(Model):
    created: datetime = Field(default_factory=datetime.now)

class Article(TimestampMixin):
    title: str
    body: str
```

`Article` will have `id`, `title`, `body`, and `created` columns.

## Creating and Dropping Tables

```python
User.create_table()                    # CREATE TABLE IF NOT EXISTS ...
User.create_table(if_not_exists=False) # raises if table already exists
User.drop_table()                      # DROP TABLE IF EXISTS ...
```

`create_table()` works for new projects; once your database has data, use
[migrations](migrations.md) to evolve the schema without losing it.

## Saving and Deleting

```python
u = User(name="Ada", email="ada@example.com")
u.save()        # INSERT — populates u.id
u.age = 37
u.save()        # UPDATE — u.id is set, so it updates the existing row
u.delete()      # DELETE — also clears u.id back to None
```

`save()` returns `self`, so you can chain:

```python
u = User(name="Ada", email="ada@example.com").save()
```

If you assign a primary key value and call `save()`, BitORM tries UPDATE first. If no row matches, it falls back to INSERT. This means user-assigned PKs work correctly:

```python
class Product(Model):
    sku: str = Field(primary_key=True)
    name: str

Product.create_table()
p = Product(sku="ABC-123", name="Widget")
p.save()  # INSERT (no row with that PK yet)
p.name = "Updated Widget"
p.save()  # UPDATE (row exists now)
```

For inserting many objects at once, use
[`bulk_create()`](concurrency.md#bulk-inserts) — it's orders of magnitude faster
than calling `save()` in a loop.

## Validation

`save()` validates every column before executing SQL. You get a clear Python error naming the field instead of a cryptic SQLite exception.

### Not-Null

Fields with `nullable=False` raise `ValueError` if the value is `None` at save time:

```python
class User(Model):
    name: str = Field(nullable=False)

u = User(name=None)
u.save()  # ValueError: Field 'name' cannot be None (not nullable).
```

### Type Checking

Values are checked against the column's annotated type. The rules follow Python's type hierarchy:

| Field type   | Accepts              | Rejects           |
|-------------|----------------------|--------------------|
| `int`       | `int`, `bool`        | `str`, `float`     |
| `float`     | `float`, `int`       | `str`              |
| `str`       | `str`                | `int`, `float`     |
| `bytes`     | `bytes`              | `str`              |
| `bool`      | `bool`, `int`        | `str`              |
| `datetime`  | `datetime`           | `date`, `str`      |
| `date`      | `date`               | `datetime`, `str`  |

```python
u = User(name="Ada", email="ada@example.com", age="old")
u.save()  # TypeError: Field 'age' expects int, got str.
```

Types not in the table above skip validation, but the value must still be something
SQLite can store (`None`, `int`, `float`, `str`, `bytes`) — saving a `dict` or
`list` directly raises `sqlite3.ProgrammingError`. To store structured data, use a
[`JSONField`](#json-fields).

Validation runs on both INSERT and UPDATE, so mutating a field to an invalid value after the first save is also caught:

```python
u = User(name="Ada", email="ada@example.com", age=36)
u.save()       # OK
u.age = "old"
u.save()       # TypeError: Field 'age' expects int, got str.
```

## JSON Fields

Store any JSON-serializable value (dicts, lists, nested structures) as TEXT.
Ideal for hyperparameters, metrics, and other structured metadata:

```python
from bitorm import Model, JSONField

class Run(Model):
    name: str
    metrics: dict = JSONField()
    config: dict = JSONField(nullable=False)
    history: list = JSONField(default_factory=list)

run = Run(name="exp1", metrics={"loss": 0.3, "acc": 0.91})
run.save()

Run.get(name="exp1").metrics["loss"]   # 0.3 — parsed back into a dict
```

Values are serialized with `json.dumps` on save and parsed with `json.loads` on
read. A non-serializable value raises `TypeError` naming the field.

Notes:

- Use `default_factory=dict` rather than `default={}` to avoid sharing one mutable
  dict across instances.
- Filtering on the contents of a JSON column isn't supported by the ORM — use
  `db.raw()` with SQLite's `json_extract()` for that.

### Options

```python
JSONField(
    nullable=True,         # allow NULL (default)
    default=...,           # static default value
    default_factory=...,   # callable, invoked per-instance (e.g. dict)
)
```

## File Fields

Reference files on disk with portable paths. Useful for managing datasets of documents, images, or any file-based data alongside structured metadata.

```python
class Email(Model):
    subject: str
    sender: str
    raw: str = FileField(base_dir="emails")

class TrainingImage(Model):
    label: str
    split: str = "train"
    image: str = FileField(base_dir="images")
```

The database stores a relative path as TEXT. On access, you get a `FileRef` object with file operations:

```python
email = Email.get(id=1)

email.raw                    # FileRef('inbox/msg_001.eml')
email.raw.relative           # 'inbox/msg_001.eml' (the stored path)
email.raw.path               # Path('/abs/project/emails/inbox/msg_001.eml')
email.raw.read_text()        # file contents as string
email.raw.read_bytes()       # file contents as bytes
email.raw.read_json()        # json.loads(file contents)
email.raw.exists             # True/False
email.raw.size               # file size in bytes
email.raw.ext                # '.eml'
email.raw.stem               # 'msg_001'
email.raw.hash               # SHA-256 hex digest (cached)
str(email.raw)               # 'inbox/msg_001.eml'
```

Set values with a string path relative to `base_dir`:

```python
email = Email(subject="Hello", sender="ada@example.com", raw="inbox/msg_001.eml")
email.save()
```

### Path Resolution

Paths resolve as: **DB directory / base_dir / stored path**

With `connect("project/app.db")` and `FileField(base_dir="emails")`, storing `"inbox/msg_001.eml"` resolves to `/abs/project/emails/inbox/msg_001.eml`. If `base_dir` is omitted, files resolve relative to the DB directory.

This makes the setup portable — move the project folder and everything still works.

### Options

```python
FileField(
    base_dir="emails",     # subdirectory for this field's files (default: "")
    nullable=True,         # allow NULL (default)
)
```

## Type Serialization

BitORM automatically handles conversion between Python and SQLite:

- `datetime` and `date` are stored as ISO 8601 strings and parsed back on read
- `bool` is stored as INTEGER (0/1) and returned as `bool`
- `bytes` is stored as BLOB
- `JSONField` values are stored as JSON TEXT and parsed back on read
- Everything else is passed through as-is (and must be a type SQLite can bind)
