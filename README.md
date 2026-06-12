# BitORM

A lightweight SQLite ORM. Zero dependencies beyond the standard library.

- Declare tables as classes with type annotations
- Django-style lazy QuerySets: `filter`, `order_by`, aggregates, relation lookups
- Foreign keys and many-to-many with forward/reverse accessors
- Auto-generated migrations with a small CLI
- Transactions (`db.atomic()`) and fast bulk inserts (`bulk_create()`)
- `JSONField` for structured metadata, `FileField` for on-disk datasets
- WAL mode and fork-safe connections — plays well with PyTorch DataLoaders

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

User.filter(age__gte=18).order_by("-age").limit(10)
User.get(email="ada@example.com")

u.age = 37
u.save()

u.delete()
```

## Command-Line Interface

BitORM ships a CLI for scaffolding and migrations:

```
uv run bitorm init             # scaffold config + a starter models.py
uv run bitorm makemigrations   # generate a migration from model changes
uv run bitorm migrate          # apply pending migrations
```

See the [Migrations guide](docs/migrations.md) for setup and the full workflow.

## Documentation

- **[Models & Fields](docs/fields.md)** — connecting, defining models, field
  options, inheritance, saving and deleting, validation, `JSONField`, `FileField`,
  type serialization
- **[Querying](docs/querying.md)** — filtering, chaining, ordering, aggregates,
  `get_or_create`, raw SQL
- **[Relationships](docs/relationships.md)** — foreign keys and many-to-many
- **[Migrations](docs/migrations.md)** — the CLI, auto-detection, migration files
- **[Transactions & Concurrency](docs/concurrency.md)** — `db.atomic()`,
  `bulk_create()`, WAL mode, fork safety, PyTorch DataLoaders

## Running Tests

```
uv run pytest tests/ -v
```
