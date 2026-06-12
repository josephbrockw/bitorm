"""bitorm — a lightweight SQLite ORM, standard library only.

Public API is re-exported here so application code only needs:

    from bitorm import Model, Field, connect, make_migration, migrate
"""

from .models import (  # noqa: F401
    Model,
    Field,
    FileField,
    FileRef,
    ForeignKey,
    ManyToMany,
    connect,
    Database,
    make_migration,
    migrate,
)

__version__ = "0.1.0"

__all__ = [
    "Model",
    "Field",
    "FileField",
    "FileRef",
    "ForeignKey",
    "ManyToMany",
    "connect",
    "Database",
    "make_migration",
    "migrate",
    "__version__",
]
