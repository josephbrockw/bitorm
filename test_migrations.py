"""Tests for the migration system in db.py."""

import os
import shutil
import tempfile

import pytest

from bitorm import Model, Field, ForeignKey, ManyToMany, Database
from db import (
    connect,
    _db,
    _get_db_schema,
    _get_model_schema,
    _diff_schemas,
    _generate_forward_sql,
    _migrations_dir,
    _next_migration_number,
    _discover_migrations,
    _ensure_migrations_table,
    _applied_migrations,
    make_migration,
    migrate,
    ColumnSchema,
    TableSchema,
    SchemaDiff,
    _default_db,
)
import db as _db_module


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def tmp_dir(tmp_path, monkeypatch):
    """Run every test in a fresh temp directory."""
    monkeypatch.chdir(tmp_path)
    yield tmp_path


@pytest.fixture
def file_db(tmp_dir):
    """Database backed by a temp file (required for migrations)."""
    path = str(tmp_dir / "test.db")
    database = connect(path)
    yield database
    _db_module._default_db = None


@pytest.fixture
def mem_db():
    """In-memory database (no migrations support)."""
    database = connect(":memory:")
    yield database
    _db_module._default_db = None


# --------------------------------------------------------------------------- #
# Test models (defined locally to avoid polluting other tests)
# --------------------------------------------------------------------------- #

class MigUser(Model):
    __tablename__ = "mig_user"
    name: str
    email: str = Field(unique=True)
    age: int = 0


class MigPost(Model):
    __tablename__ = "mig_post"
    title: str
    author: MigUser = ForeignKey(MigUser, related_name="mig_posts")


class MigTag(Model):
    __tablename__ = "mig_tag"
    label: str = Field(unique=True)


class MigArticle(Model):
    __tablename__ = "mig_article"
    title: str
    tags: list = ManyToMany(MigTag, related_name="mig_articles")


ALL_MIG_MODELS = [MigUser, MigPost, MigTag, MigArticle]


# =========================================================================== #
# Schema Introspection
# =========================================================================== #

class TestSchemaIntrospection:

    def test_empty_db_returns_no_tables(self, file_db):
        schema = _get_db_schema(file_db)
        assert schema == {}

    def test_introspects_columns(self, file_db):
        MigUser.create_table()
        schema = _get_db_schema(file_db)
        assert "mig_user" in schema
        cols = schema["mig_user"].columns
        assert "id" in cols
        assert cols["id"].primary_key is True
        assert cols["name"].sql_type == "TEXT"
        assert cols["age"].sql_type == "INTEGER"

    def test_introspects_unique(self, file_db):
        MigUser.create_table()
        schema = _get_db_schema(file_db)
        assert schema["mig_user"].columns["email"].unique is True
        assert schema["mig_user"].columns["name"].unique is False

    def test_introspects_foreign_key(self, file_db):
        MigUser.create_table()
        MigPost.create_table()
        schema = _get_db_schema(file_db)
        fk_col = schema["mig_post"].columns["author_id"]
        assert fk_col.references is not None
        assert "mig_user" in fk_col.references

    def test_introspects_m2m_join_table(self, file_db):
        MigTag.create_table()
        MigArticle.create_table()
        schema = _get_db_schema(file_db)
        assert "mig_article_tags" in schema

    def test_excludes_migrations_table(self, file_db):
        _ensure_migrations_table(file_db)
        schema = _get_db_schema(file_db)
        assert "_migrations" not in schema


# =========================================================================== #
# Model Schema Extraction
# =========================================================================== #

class TestModelSchemaExtraction:

    def test_extracts_model_columns(self, file_db):
        schema = _get_model_schema(ALL_MIG_MODELS)
        assert "mig_user" in schema
        cols = schema["mig_user"].columns
        assert "id" in cols
        assert cols["id"].primary_key is True
        assert cols["name"].sql_type == "TEXT"

    def test_extracts_fk_column(self, file_db):
        schema = _get_model_schema(ALL_MIG_MODELS)
        assert "mig_post" in schema
        assert "author_id" in schema["mig_post"].columns
        assert schema["mig_post"].columns["author_id"].references is not None

    def test_extracts_m2m_join_table(self, file_db):
        schema = _get_model_schema(ALL_MIG_MODELS)
        assert "mig_article_tags" in schema
        jt = schema["mig_article_tags"]
        assert "mig_article_id" in jt.columns
        assert "mig_tag_id" in jt.columns


# =========================================================================== #
# Schema Diffing
# =========================================================================== #

class TestSchemaDiffing:

    def test_no_diff_when_identical(self, file_db):
        MigUser.create_table()
        db_schema = _get_db_schema(file_db)
        model_schema = _get_model_schema(ALL_MIG_MODELS)
        diff = _diff_schemas(
            {k: db_schema[k] for k in db_schema if k == "mig_user"},
            {k: model_schema[k] for k in model_schema if k == "mig_user"},
        )
        assert diff.is_empty()

    def test_detects_added_table(self, file_db):
        db_schema = _get_db_schema(file_db)
        model_schema = {"new_table": TableSchema(
            name="new_table",
            columns={"id": ColumnSchema("id", "INTEGER", False, True, False)},
        )}
        diff = _diff_schemas(db_schema, model_schema)
        assert "new_table" in diff.added_tables

    def test_detects_removed_table(self, file_db):
        MigUser.create_table()
        db_schema = _get_db_schema(file_db)
        diff = _diff_schemas(db_schema, {})
        assert "mig_user" in diff.removed_tables

    def test_detects_added_column(self, file_db):
        file_db.execute(
            'CREATE TABLE "mig_user" ("id" INTEGER PRIMARY KEY, "name" TEXT)'
        )
        file_db.commit()
        db_schema = _get_db_schema(file_db)
        model_schema = _get_model_schema(ALL_MIG_MODELS)
        diff = _diff_schemas(
            {k: db_schema[k] for k in db_schema if k == "mig_user"},
            {k: model_schema[k] for k in model_schema if k == "mig_user"},
        )
        added_names = [c.name for c in diff.added_columns.get("mig_user", [])]
        assert "email" in added_names
        assert "age" in added_names

    def test_detects_removed_column(self, file_db):
        file_db.execute(
            'CREATE TABLE "mig_user" ('
            '"id" INTEGER PRIMARY KEY, "name" TEXT, "email" TEXT UNIQUE, '
            '"age" INTEGER, "obsolete" TEXT)'
        )
        file_db.commit()
        db_schema = _get_db_schema(file_db)
        model_schema = _get_model_schema(ALL_MIG_MODELS)
        diff = _diff_schemas(
            {k: db_schema[k] for k in db_schema if k == "mig_user"},
            {k: model_schema[k] for k in model_schema if k == "mig_user"},
        )
        assert "obsolete" in diff.removed_columns.get("mig_user", [])

    def test_detects_changed_column_type(self, file_db):
        file_db.execute(
            'CREATE TABLE "mig_user" ('
            '"id" INTEGER PRIMARY KEY, "name" BLOB, "email" TEXT UNIQUE, "age" INTEGER)'
        )
        file_db.commit()
        db_schema = _get_db_schema(file_db)
        model_schema = _get_model_schema(ALL_MIG_MODELS)
        diff = _diff_schemas(
            {k: db_schema[k] for k in db_schema if k == "mig_user"},
            {k: model_schema[k] for k in model_schema if k == "mig_user"},
        )
        changed_names = [name for name, _, _ in diff.changed_columns.get("mig_user", [])]
        assert "name" in changed_names

    def test_empty_diff_is_empty(self, file_db):
        diff = SchemaDiff()
        assert diff.is_empty()


# =========================================================================== #
# SQL Generation
# =========================================================================== #

class TestSQLGeneration:

    def test_generate_create_table(self, file_db):
        model_schema = {
            "tbl": TableSchema(
                name="tbl",
                columns={
                    "id": ColumnSchema("id", "INTEGER", False, True, False),
                    "name": ColumnSchema("name", "TEXT", False, False, False),
                },
            )
        }
        diff = SchemaDiff(added_tables=["tbl"])
        stmts = _generate_forward_sql(diff, model_schema)
        assert len(stmts) == 1
        assert "CREATE TABLE" in stmts[0]
        assert "tbl" in stmts[0]

    def test_generate_drop_table(self, file_db):
        diff = SchemaDiff(removed_tables=["old_tbl"])
        stmts = _generate_forward_sql(diff, {})
        assert any("DROP TABLE" in s for s in stmts)

    def test_generate_add_column(self, file_db):
        model_schema = {
            "tbl": TableSchema(
                name="tbl",
                columns={
                    "id": ColumnSchema("id", "INTEGER", False, True, False),
                    "bio": ColumnSchema("bio", "TEXT", False, False, False),
                },
            )
        }
        diff = SchemaDiff(
            added_columns={"tbl": [ColumnSchema("bio", "TEXT", False, False, False)]}
        )
        stmts = _generate_forward_sql(diff, model_schema)
        assert any("ALTER TABLE" in s and "ADD COLUMN" in s for s in stmts)

    def test_generate_drop_column(self, file_db):
        diff = SchemaDiff(removed_columns={"tbl": ["old_col"]})
        stmts = _generate_forward_sql(diff, {})
        assert any("DROP COLUMN" in s for s in stmts)

    def test_notnull_column_triggers_rebuild(self, file_db):
        model_schema = {
            "tbl": TableSchema(
                name="tbl",
                columns={
                    "id": ColumnSchema("id", "INTEGER", False, True, False),
                    "req": ColumnSchema("req", "TEXT", True, False, False),
                },
            )
        }
        diff = SchemaDiff(
            added_columns={"tbl": [ColumnSchema("req", "TEXT", True, False, False)]}
        )
        stmts = _generate_forward_sql(diff, model_schema)
        assert any("__new" in s for s in stmts)

    def test_changed_column_triggers_rebuild(self, file_db):
        old = ColumnSchema("name", "BLOB", False, False, False)
        new = ColumnSchema("name", "TEXT", False, False, False)
        model_schema = {
            "tbl": TableSchema(
                name="tbl",
                columns={"id": ColumnSchema("id", "INTEGER", False, True, False), "name": new},
            )
        }
        diff = SchemaDiff(changed_columns={"tbl": [("name", old, new)]})
        stmts = _generate_forward_sql(diff, model_schema)
        assert any("__new" in s for s in stmts)


# =========================================================================== #
# Migration Directory & File Naming
# =========================================================================== #

class TestMigrationDir:

    def test_migrations_dir_from_path(self, file_db):
        mdir = _migrations_dir(file_db)
        assert mdir.endswith("test_migrations")

    def test_memory_db_raises(self, mem_db):
        with pytest.raises(ValueError, match=":memory:"):
            _migrations_dir(mem_db)

    def test_next_number_starts_at_1(self, tmp_dir):
        assert _next_migration_number(str(tmp_dir / "nonexistent")) == 1

    def test_next_number_increments(self, tmp_dir):
        mdir = str(tmp_dir / "migs")
        os.makedirs(mdir)
        open(os.path.join(mdir, "001_first.py"), "w").close()
        open(os.path.join(mdir, "002_second.py"), "w").close()
        assert _next_migration_number(mdir) == 3

    def test_ignores_non_migration_files(self, tmp_dir):
        mdir = str(tmp_dir / "migs")
        os.makedirs(mdir)
        open(os.path.join(mdir, "001_first.py"), "w").close()
        open(os.path.join(mdir, "README.md"), "w").close()
        open(os.path.join(mdir, "__init__.py"), "w").close()
        assert _next_migration_number(mdir) == 2


# =========================================================================== #
# Migration Discovery
# =========================================================================== #

class TestMigrationDiscovery:

    def test_discover_empty_dir(self, tmp_dir):
        mdir = str(tmp_dir / "migs")
        os.makedirs(mdir)
        assert _discover_migrations(mdir) == []

    def test_discover_sorted_by_number(self, tmp_dir):
        mdir = str(tmp_dir / "migs")
        os.makedirs(mdir)
        open(os.path.join(mdir, "003_third.py"), "w").close()
        open(os.path.join(mdir, "001_first.py"), "w").close()
        open(os.path.join(mdir, "002_second.py"), "w").close()
        result = _discover_migrations(mdir)
        names = [name for name, _ in result]
        assert names == ["001_first", "002_second", "003_third"]

    def test_discover_nonexistent_dir(self, tmp_dir):
        assert _discover_migrations(str(tmp_dir / "nope")) == []

    def test_discover_skips_non_py(self, tmp_dir):
        mdir = str(tmp_dir / "migs")
        os.makedirs(mdir)
        open(os.path.join(mdir, "001_first.py"), "w").close()
        open(os.path.join(mdir, "001_first.pyc"), "w").close()
        result = _discover_migrations(mdir)
        assert len(result) == 1


# =========================================================================== #
# Migration Tracking
# =========================================================================== #

class TestMigrationTracking:

    def test_ensure_creates_table(self, file_db):
        _ensure_migrations_table(file_db)
        rows = file_db.execute(
            "SELECT name FROM sqlite_master WHERE name='_migrations'"
        ).fetchall()
        assert len(rows) == 1

    def test_applied_empty(self, file_db):
        _ensure_migrations_table(file_db)
        assert _applied_migrations(file_db) == []

    def test_applied_returns_ordered(self, file_db):
        _ensure_migrations_table(file_db)
        file_db.execute(
            "INSERT INTO _migrations (name, applied_at) VALUES (?, ?)",
            ("001_a", "2024-01-01"),
        )
        file_db.execute(
            "INSERT INTO _migrations (name, applied_at) VALUES (?, ?)",
            ("002_b", "2024-01-02"),
        )
        file_db.commit()
        assert _applied_migrations(file_db) == ["001_a", "002_b"]


# =========================================================================== #
# make_migration
# =========================================================================== #

class TestMakeMigration:

    def test_generates_file_for_new_table(self, file_db):
        path = make_migration(file_db, name="initial", models=ALL_MIG_MODELS)
        assert path is not None
        assert os.path.isfile(path)
        assert "001_initial.py" in path
        with open(path) as f:
            content = f.read()
        assert "def forward(db):" in content
        assert "CREATE TABLE" in content

    def test_returns_none_when_no_changes(self, file_db):
        MigUser.create_table()
        MigPost.create_table()
        MigTag.create_table()
        MigArticle.create_table()
        result = make_migration(file_db, name="noop", models=ALL_MIG_MODELS)
        assert result is None

    def test_sequential_numbering(self, file_db):
        p1 = make_migration(file_db, name="first", models=ALL_MIG_MODELS)
        assert "001_first.py" in p1
        migrate(file_db)
        file_db.execute('DROP TABLE IF EXISTS "mig_article_tags"')
        file_db.commit()
        p2 = make_migration(file_db, name="second", models=ALL_MIG_MODELS)
        assert p2 is not None
        assert "002_second.py" in p2

    def test_memory_db_raises(self, mem_db):
        with pytest.raises(ValueError):
            make_migration(mem_db)

    def test_creates_directory_if_missing(self, file_db):
        mdir = _migrations_dir(file_db)
        assert not os.path.isdir(mdir)
        make_migration(file_db, name="init", models=ALL_MIG_MODELS)
        assert os.path.isdir(mdir)


# =========================================================================== #
# migrate
# =========================================================================== #

class TestMigrate:

    def test_applies_pending_migrations(self, file_db):
        make_migration(file_db, name="init", models=ALL_MIG_MODELS)
        applied = migrate(file_db)
        assert len(applied) == 1
        assert applied[0] == "001_init"

    def test_skips_already_applied(self, file_db):
        make_migration(file_db, name="init", models=ALL_MIG_MODELS)
        migrate(file_db)
        applied_again = migrate(file_db)
        assert applied_again == []

    def test_tracks_in_migrations_table(self, file_db):
        make_migration(file_db, name="init", models=ALL_MIG_MODELS)
        migrate(file_db)
        tracked = _applied_migrations(file_db)
        assert "001_init" in tracked

    def test_applies_in_order(self, file_db, tmp_dir):
        mdir = _migrations_dir(file_db)
        os.makedirs(mdir, exist_ok=True)

        with open(os.path.join(mdir, "001_first.py"), "w") as f:
            f.write(
                'def forward(db):\n'
                '    db.execute("CREATE TABLE t1 (id INTEGER PRIMARY KEY)")\n'
                '    db.commit()\n'
            )
        with open(os.path.join(mdir, "002_second.py"), "w") as f:
            f.write(
                'def forward(db):\n'
                '    db.execute("CREATE TABLE t2 (id INTEGER PRIMARY KEY)")\n'
                '    db.commit()\n'
            )

        applied = migrate(file_db)
        assert applied == ["001_first", "002_second"]
        tables = {
            r["name"]
            for r in file_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "t1" in tables
        assert "t2" in tables

    def test_migration_without_forward_raises(self, file_db, tmp_dir):
        mdir = _migrations_dir(file_db)
        os.makedirs(mdir, exist_ok=True)
        with open(os.path.join(mdir, "001_bad.py"), "w") as f:
            f.write("x = 1\n")
        with pytest.raises(AttributeError, match="forward"):
            migrate(file_db)

    def test_memory_db_raises(self, mem_db):
        with pytest.raises(ValueError):
            migrate(mem_db)

    def test_tables_usable_after_migrate(self, file_db):
        make_migration(file_db, name="init", models=ALL_MIG_MODELS)
        migrate(file_db)
        schema = _get_db_schema(file_db)
        assert "mig_user" in schema

    def test_end_to_end_add_column(self, file_db):
        """Simulate adding a column: create table, then detect added column."""
        file_db.execute(
            'CREATE TABLE "mig_user" ('
            '"id" INTEGER PRIMARY KEY, "name" TEXT)'
        )
        file_db.commit()
        path = make_migration(file_db, name="add_cols", models=[MigUser])
        assert path is not None
        with open(path) as f:
            content = f.read()
        assert "email" in content or "age" in content
        migrate(file_db)
        cols = {
            r["name"]
            for r in file_db.execute('PRAGMA table_info("mig_user")').fetchall()
        }
        assert "email" in cols
        assert "age" in cols
