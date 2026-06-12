"""Command-line interface for bitorm.

Thin wrapper around connect()/make_migration()/migrate(). Configuration lives in
the consuming project's pyproject.toml under [tool.bitorm]:

    [tool.bitorm]
    database = "app.db"
    models = "models"   # importable module that defines your Model subclasses

Usage:
    bitorm init
    bitorm makemigrations [name]
    bitorm migrate
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import tomllib
from typing import Optional

from . import __version__
from .db import connect, make_migration, migrate

PYPROJECT = "pyproject.toml"

_CONFIG_BLOCK = """
[tool.bitorm]
database = "app.db"
models = "models"
"""

_STARTER_MODELS = '''"""Define your bitorm models here.

Each Model subclass becomes a table. After editing, run:
    uv run bitorm makemigrations
    uv run bitorm migrate
"""

from bitorm import Model, Field


class Example(Model):
    name: str
    count: int = 0
'''


class CommandError(Exception):
    """Raised for expected, user-facing CLI failures."""


# --------------------------------------------------------------------------- #
# Config + model loading
# --------------------------------------------------------------------------- #

def _load_config() -> dict:
    """Read [tool.bitorm] from the project's pyproject.toml."""
    if not os.path.exists(PYPROJECT):
        raise CommandError(
            f"No {PYPROJECT} found in {os.getcwd()}. "
            "Run `bitorm init` from your project root first."
        )
    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    config = data.get("tool", {}).get("bitorm")
    if not config:
        raise CommandError(
            f"No [tool.bitorm] section in {PYPROJECT}. Run `bitorm init` first."
        )
    if "database" not in config or "models" not in config:
        raise CommandError(
            "[tool.bitorm] must define both `database` and `models`."
        )
    return config


def _import_models(models_name: str) -> None:
    """Import the user's models module so Model subclasses register themselves."""
    if "" not in sys.path:
        sys.path.insert(0, "")
    try:
        importlib.import_module(models_name)
    except ModuleNotFoundError as exc:
        raise CommandError(
            f"Could not import models module '{models_name}': {exc}. "
            "Check the `models` key in [tool.bitorm]."
        ) from exc


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_init(_args: argparse.Namespace) -> int:
    if not os.path.exists(PYPROJECT):
        raise CommandError(
            f"No {PYPROJECT} found in {os.getcwd()}. "
            "Run `uv init` to create a project first, then `bitorm init`."
        )

    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    if data.get("tool", {}).get("bitorm"):
        print(f"[tool.bitorm] already present in {PYPROJECT}; leaving it as is.")
    else:
        with open(PYPROJECT, "a") as f:
            f.write(_CONFIG_BLOCK)
        print(f"Added [tool.bitorm] to {PYPROJECT}.")

    if os.path.exists("models.py"):
        print("models.py already exists; leaving it as is.")
    else:
        with open("models.py", "w") as f:
            f.write(_STARTER_MODELS)
        print("Created models.py with an example model.")

    print(
        "\nNext steps:\n"
        "  1. Edit models.py to define your tables.\n"
        "  2. uv run bitorm makemigrations\n"
        "  3. uv run bitorm migrate"
    )
    return 0


def cmd_makemigrations(args: argparse.Namespace) -> int:
    config = _load_config()
    _import_models(config["models"])
    db = connect(config["database"])
    path = make_migration(db, name=args.name or "auto")
    if path is None:
        print("No changes detected.")
    else:
        print(f"Created {path}")
    return 0


def cmd_migrate(_args: argparse.Namespace) -> int:
    config = _load_config()
    _import_models(config["models"])
    db = connect(config["database"])
    applied = migrate(db)
    if not applied:
        print("Nothing to apply; database is up to date.")
    else:
        for name in applied:
            print(f"Applied {name}")
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bitorm", description="bitorm CLI")
    parser.add_argument(
        "--version", action="version", version=f"bitorm {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Scaffold [tool.bitorm] config and models.py")

    p_make = sub.add_parser(
        "makemigrations", help="Generate a migration from model changes"
    )
    p_make.add_argument(
        "name", nargs="?", default=None, help="Optional migration name"
    )

    sub.add_parser("migrate", help="Apply pending migrations")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "init": cmd_init,
        "makemigrations": cmd_makemigrations,
        "migrate": cmd_migrate,
    }
    try:
        return handlers[args.command](args)
    except CommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
