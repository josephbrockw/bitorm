"""Tests for the bitorm CLI (init / makemigrations / migrate)."""

import os
import sys
import tomllib

import pytest

import bitorm.db as _db_module
from bitorm.cli import main, CommandError


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A scratch project dir on cwd + sys.path, with a reset global db."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    # Drop any cached user models module between tests.
    sys.modules.pop("models", None)
    _db_module._default_db = None
    yield tmp_path
    sys.modules.pop("models", None)


def _write_pyproject(path, body="[project]\nname = \"demo\"\nversion = \"0\"\n"):
    (path / "pyproject.toml").write_text(body)


# --------------------------------------------------------------------------- #
# init
# --------------------------------------------------------------------------- #

def test_init_scaffolds_config_and_models(project, capsys):
    _write_pyproject(project)
    rc = main(["init"])
    assert rc == 0

    data = tomllib.loads((project / "pyproject.toml").read_text())
    assert data["tool"]["bitorm"]["database"] == "app.db"
    assert data["tool"]["bitorm"]["models"] == "models"
    assert (project / "models.py").exists()


def test_init_without_pyproject_errors(project, capsys):
    rc = main(["init"])
    assert rc == 1
    assert "uv init" in capsys.readouterr().err


def test_init_is_idempotent(project):
    _write_pyproject(project)
    main(["init"])
    custom = (project / "models.py").read_text() + "\n# custom\n"
    (project / "models.py").write_text(custom)
    # Second init must not clobber an existing models.py or re-add config.
    main(["init"])
    assert (project / "models.py").read_text() == custom
    data = tomllib.loads((project / "pyproject.toml").read_text())
    # Only one [tool.bitorm] table (would be a dict, not a parse error).
    assert data["tool"]["bitorm"]["database"] == "app.db"


# --------------------------------------------------------------------------- #
# makemigrations / migrate
# --------------------------------------------------------------------------- #

def _init_with_model(project, body):
    _write_pyproject(project)
    main(["init"])
    (project / "models.py").write_text(body)


ONE_MODEL = (
    "from bitorm import Model, Field\n\n\n"
    "class Widget(Model):\n"
    "    name: str\n"
)


def test_makemigrations_creates_file(project, capsys):
    _init_with_model(project, ONE_MODEL)
    rc = main(["makemigrations", "init"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Created" in out
    mig_dir = project / "app_migrations"
    files = list(mig_dir.glob("001_init.py"))
    assert len(files) == 1


def test_makemigrations_no_changes(project, capsys):
    _init_with_model(project, ONE_MODEL)
    main(["makemigrations", "init"])
    main(["migrate"])
    capsys.readouterr()
    rc = main(["makemigrations"])
    assert rc == 0
    assert "No changes detected." in capsys.readouterr().out


def test_migrate_applies_and_is_idempotent(project, capsys):
    _init_with_model(project, ONE_MODEL)
    main(["makemigrations", "init"])
    capsys.readouterr()

    rc = main(["migrate"])
    assert rc == 0
    assert "Applied 001_init" in capsys.readouterr().out

    # The table is really there.
    rows = _db_module._db().raw(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='widget'"
    )
    assert len(rows) == 1

    # Re-running migrate does nothing.
    rc = main(["migrate"])
    assert rc == 0
    assert "Nothing to apply" in capsys.readouterr().out


def test_commands_without_config_error(project, capsys):
    _write_pyproject(project)  # no [tool.bitorm]
    rc = main(["migrate"])
    assert rc == 1
    assert "bitorm init" in capsys.readouterr().err
