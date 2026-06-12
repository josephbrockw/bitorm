"""bitorm — a lightweight SQLite ORM, standard library only.

Public API is re-exported here so application code only needs:

    from bitorm import Model, Field, connect, make_migration, migrate
"""

from importlib.metadata import PackageNotFoundError, version

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

# Single source of truth is pyproject.toml; read it from installed metadata.
try:
    __version__ = version("bitorm")
except PackageNotFoundError:  # running from a bare source tree, not installed
    __version__ = "0.0.0+unknown"

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
