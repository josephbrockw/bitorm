# BitORM

A lightweight SQLite ORM in a single Python file. Zero dependencies beyond the standard library.

## Requirements

Python 3.11+

## Installation

Copy `bitorm.py` into your project. That's it — there's nothing to install.

If you're using it as a package:

```
pip install -e .
```

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

## Type Serialization

BitORM automatically handles conversion between Python and SQLite:

- `datetime` and `date` are stored as ISO 8601 strings and parsed back on read
- `bool` is stored as INTEGER (0/1) and returned as `bool`
- `bytes` is stored as BLOB
- Everything else is passed through as-is

## Running Tests

```
uv run pytest test_bitorm.py -v
```
