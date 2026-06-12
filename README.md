# BitORM

A lightweight SQLite ORM. Zero dependencies beyond the standard library.

## Requirements

Python 3.11+

## Installation

Add BitORM to your project with [uv](https://docs.astral.sh/uv/):

```
uv add git+https://github.com/josephbrockw/bitorm
```

This installs the package and the `bitorm` CLI. (Plain pip works too:
`pip install git+https://github.com/josephbrockw/bitorm`.)

To hack on BitORM itself, clone it and sync:

```
git clone https://github.com/josephbrockw/bitorm
cd bitorm
uv sync
```

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

## Quick Start

```python
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
u.save()

u.age = 37
u.save()

u.delete()
```

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

## Migrations

`create_table()` works for new projects, but once your database has data you need migrations to evolve the schema without losing it.

The easiest way to run migrations is the [CLI](#command-line-interface)
(`uv run bitorm makemigrations` / `uv run bitorm migrate`). The functions below are
what those commands call, and are useful when you want to drive migrations from your
own code.

### Generating a Migration

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

### Applying Migrations

```python
applied = migrate(db)
# Returns list of applied migration names, e.g. ["001_initial"]
```

`migrate` discovers all `NNN_name.py` files in the migrations directory, skips any already applied, and runs the rest in order. Applied migrations are tracked in a `_migrations` table.

### Migration Files

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

### Supported Operations

- Add/remove tables
- Add/remove columns (`ALTER TABLE ADD/DROP COLUMN`)
- Column type, nullability, or uniqueness changes (uses SQLite's table rebuild pattern)

### Migration Directory

The directory is derived from the database filename: `app.db` uses `app_migrations/`, `data.db` uses `data_migrations/`, etc. In-memory databases don't support migrations — use `create_table()` directly.

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

Types not in the table above (e.g., `list`) skip validation and are passed through to SQLite as-is.

Validation runs on both INSERT and UPDATE, so mutating a field to an invalid value after the first save is also caught:

```python
u = User(name="Ada", email="ada@example.com", age=36)
u.save()       # OK
u.age = "old"
u.save()       # TypeError: Field 'age' expects int, got str.
```

## Querying

### Basic Queries

```python
User.all()                    # lazy QuerySet of all rows
User.get(email="ada@x.com")  # single object or None (raises if >1 match)
```

### Raw SQL

For queries the ORM can't express — GROUP BY, window functions, CTEs, complex joins:

```python
# Returns model instances (deserialized through _from_row)
users = User.raw("SELECT * FROM user WHERE age > ? ORDER BY name", (18,))

# Returns raw sqlite3.Row objects (for aggregations, cross-table queries)
db = connect("app.db")
rows = db.raw("SELECT label, COUNT(*) as cnt FROM classification GROUP BY label")
for row in rows:
    print(row["label"], row["cnt"])
```

Both methods use parameterized queries. `Model.raw()` applies the same type deserialization as normal queries (datetime, date, bool).

### Get or Create

Look up an object by filter criteria, creating it if it doesn't exist:

```python
user, created = User.get_or_create(
    email="ada@example.com",
    defaults={"name": "Ada", "age": 36},
)
# created=True  → new row was inserted
# created=False → existing row was returned
```

`**kwargs` are the lookup fields. `defaults` is an optional dict of additional fields applied only when creating — they're not used in the lookup. If `get()` finds multiple matches, `ValueError` is raised (same as calling `get()` directly).

### Filtering

Filter with Django-style keyword arguments:

```python
User.filter(age=36)                 # age = 36
User.filter(age__gt=18)             # age > 18
User.filter(age__gte=18)            # age >= 18
User.filter(age__lt=65)             # age < 65
User.filter(age__lte=65)            # age <= 65
User.filter(name__ne="Bob")         # name != 'Bob'
User.filter(name__like="%ada%")     # name LIKE '%ada%'
User.filter(id__in=[1, 2, 3])       # id IN (1, 2, 3)
User.filter(value=None)             # value IS NULL
User.filter(value__ne=None)         # value IS NOT NULL
```

### Chaining

QuerySets are lazy and immutable. Each method returns a new QuerySet:

```python
qs = User.filter(age__gte=18).order_by("-age").limit(10)
# No query runs until you evaluate the QuerySet:
results = list(qs)
```

### Ordering

```python
User.all().order_by("name")         # ASC
User.all().order_by("-name")        # DESC
User.all().order_by("age", "name")  # multiple columns
```

### Evaluation

QuerySets evaluate lazily. These operations trigger a query:

```python
list(qs)          # iterate
qs[0]             # index
qs[1:3]           # slice
len(qs)           # length
bool(qs)          # truthiness
repr(qs)          # representation
```

Other methods:

```python
qs.first()        # first result or None (adds LIMIT 1)
qs.count()        # COUNT(*) query (or len(cache) if already evaluated)
qs.delete()       # bulk DELETE matching the filters, returns row count
```

### Distinct

```python
User.all().distinct()                # SELECT DISTINCT ...
User.filter(age__gte=18).distinct().count()  # count distinct matching rows
```

`distinct()` is chainable and returns a new QuerySet. Note that `SELECT DISTINCT *` with a unique primary key has no effect since every row is already unique — this matches standard SQL behavior.

### Aggregates

`sum()`, `avg()`, `min()`, and `max()` are terminal methods that return a scalar:

```python
User.all().sum("age")               # total of all ages
User.filter(active=True).avg("age") # average age of active users
User.all().min("name")              # alphabetically first name
User.all().max("created")           # most recent datetime
```

Aggregates respect filters but ignore `order_by()` and `limit()` (which are irrelevant to aggregate results). Returns `None` when no rows match.

The column name is validated — passing a nonexistent column raises `ValueError` with a helpful message listing valid columns.

Results are deserialized through the same type conversion as regular queries, so `min("created")` on a datetime column returns a `datetime` object, not a string.

### Filtering Across Relations

Single-hop related field lookups work via JOINs:

```python
Post.filter(author__name="Ada")            # JOIN + WHERE
Post.filter(author__email__like="%ada%")   # JOIN + WHERE with operator
Post.all().order_by("author__name")        # JOIN + ORDER BY
```

## Foreign Keys

```python
class Author(Model):
    name: str

class Post(Model):
    title: str
    author: Author = ForeignKey(Author)
```

This creates an `author_id` INTEGER column on the `post` table with a foreign key reference to `author(id)`.

### Forward Access

```python
post = Post.get(id=1)
post.author        # lazily fetches and caches the Author object
post.author_id     # the raw FK value (integer)
```

### Setting Foreign Keys

```python
author = Author(name="Ada")
author.save()                          # must be saved first
post = Post(title="Hello", author=author)
post.save()

post.author = other_author             # reassign (must be saved)
post.author = None                     # clear the relationship
```

Assigning an unsaved object raises `ValueError`.

### Reverse Access

The target model gets a reverse accessor that returns a QuerySet:

```python
author.posts.count()                   # default name: <model>s
author.posts.filter(title__like="%x%")
list(author.posts)
```

### Options

```python
ForeignKey(
    Author,
    related_name="written_posts",  # custom reverse accessor name
    nullable=True,                 # allow NULL (default)
    on_delete="CASCADE",           # CASCADE | SET NULL | RESTRICT | NO ACTION
)
```

## Many-to-Many

```python
class Tag(Model):
    label: str = Field(unique=True)

class Article(Model):
    title: str
    tags: list = ManyToMany(Tag)
```

`Article.create_table()` also creates the join table (`article_tag`).

### Managing Relations

```python
article = Article(title="Hello")
article.save()
tag = Tag(label="python")
tag.save()

article.tags.add(tag)              # link
article.tags.add(tag1, tag2)       # link multiple
article.tags.remove(tag)           # unlink
article.tags.clear()               # remove all links

article.tags.all()                 # list of Tag objects
article.tags.count()               # number of linked tags
list(article.tags)                 # iterable

# reverse side:
tag.articles.all()                 # default name: <owner>s
```

All objects must be saved before calling `add()`.

Custom reverse name:

```python
tags: list = ManyToMany(Tag, related_name="tagged_articles")
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
- Everything else is passed through as-is

## Running Tests

```
uv run pytest tests/ -v
```
