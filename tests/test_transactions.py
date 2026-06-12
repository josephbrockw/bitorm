"""Tests for db.atomic() transactions and atomic migrations."""

import pytest

import bitorm.db as _db_module
from bitorm import Model, Field, ForeignKey, ManyToMany, connect, migrate


# =========================================================================== #
# Module-level test models
# =========================================================================== #

class TxUser(Model):
    name: str
    email: str = Field(unique=True)


class TxAuthor(Model):
    name: str


class TxPost(Model):
    title: str
    author: TxAuthor = ForeignKey(TxAuthor, related_name="tx_posts")


class TxTag(Model):
    label: str


class TxArticle(Model):
    title: str
    tags: list = ManyToMany(TxTag, related_name="tx_articles")


# =========================================================================== #
# Fixtures
# =========================================================================== #

@pytest.fixture(autouse=True)
def db():
    """Fresh in-memory database for every test."""
    database = connect(":memory:")
    yield database
    _db_module._default_db = None


# =========================================================================== #
# Basic atomic behavior
# =========================================================================== #

class TestAtomic:

    def test_commits_on_success(self, db):
        TxUser.create_table()
        with db.atomic():
            TxUser(name="Ada", email="ada@x.com").save()
            TxUser(name="Bob", email="bob@x.com").save()
        assert TxUser.all().count() == 2

    def test_rolls_back_on_exception(self, db):
        TxUser.create_table()
        with pytest.raises(RuntimeError, match="boom"):
            with db.atomic():
                TxUser(name="Ada", email="ada@x.com").save()
                raise RuntimeError("boom")
        assert TxUser.all().count() == 0

    def test_enter_returns_database(self, db):
        with db.atomic() as inner:
            assert inner is db

    def test_commit_is_noop_inside_atomic(self, db):
        TxUser.create_table()
        with pytest.raises(RuntimeError):
            with db.atomic():
                TxUser(name="Ada", email="ada@x.com").save()
                db.commit()  # suppressed: must not make the insert durable
                raise RuntimeError("boom")
        assert TxUser.all().count() == 0

    def test_commit_works_again_after_atomic(self, db):
        TxUser.create_table()
        with db.atomic():
            TxUser(name="Ada", email="ada@x.com").save()
        TxUser(name="Bob", email="bob@x.com").save()  # plain autocommit save
        assert TxUser.all().count() == 2

    def test_exception_propagates(self, db):
        with pytest.raises(ValueError, match="custom"):
            with db.atomic():
                raise ValueError("custom")

    def test_rollback_on_integrity_error(self, db):
        TxUser.create_table()
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            with db.atomic():
                TxUser(name="Ada", email="dup@x.com").save()
                TxUser(name="Bob", email="dup@x.com").save()  # UNIQUE violation
        assert TxUser.all().count() == 0


# =========================================================================== #
# Nesting (SAVEPOINTs)
# =========================================================================== #

class TestNestedAtomic:

    def test_inner_rollback_preserves_outer(self, db):
        TxUser.create_table()
        with db.atomic():
            TxUser(name="Ada", email="ada@x.com").save()
            with pytest.raises(RuntimeError):
                with db.atomic():
                    TxUser(name="Bob", email="bob@x.com").save()
                    raise RuntimeError("inner")
            TxUser(name="Cleo", email="cleo@x.com").save()
        names = {u.name for u in TxUser.all()}
        assert names == {"Ada", "Cleo"}

    def test_outer_rollback_discards_inner_commit(self, db):
        TxUser.create_table()
        with pytest.raises(RuntimeError):
            with db.atomic():
                with db.atomic():
                    TxUser(name="Bob", email="bob@x.com").save()
                raise RuntimeError("outer")
        assert TxUser.all().count() == 0

    def test_three_levels(self, db):
        TxUser.create_table()
        with db.atomic():
            TxUser(name="L1", email="1@x.com").save()
            with db.atomic():
                TxUser(name="L2", email="2@x.com").save()
                with pytest.raises(RuntimeError):
                    with db.atomic():
                        TxUser(name="L3", email="3@x.com").save()
                        raise RuntimeError("deepest")
        names = {u.name for u in TxUser.all()}
        assert names == {"L1", "L2"}

    def test_depth_resets_after_use(self, db):
        with db.atomic():
            with db.atomic():
                pass
        assert db._atomic_depth == 0
        with pytest.raises(RuntimeError):
            with db.atomic():
                raise RuntimeError()
        assert db._atomic_depth == 0


# =========================================================================== #
# All ORM write operations participate
# =========================================================================== #

class TestOrmOpsParticipate:

    def test_delete_rolls_back(self, db):
        TxUser.create_table()
        u = TxUser(name="Ada", email="ada@x.com").save()
        with pytest.raises(RuntimeError):
            with db.atomic():
                u.delete()
                raise RuntimeError("boom")
        assert TxUser.all().count() == 1

    def test_queryset_delete_rolls_back(self, db):
        TxUser.create_table()
        TxUser(name="Ada", email="ada@x.com").save()
        TxUser(name="Bob", email="bob@x.com").save()
        with pytest.raises(RuntimeError):
            with db.atomic():
                TxUser.all().delete()
                raise RuntimeError("boom")
        assert TxUser.all().count() == 2

    def test_m2m_add_and_clear_roll_back(self, db):
        TxTag.create_table()
        TxArticle.create_table()
        article = TxArticle(title="A").save()
        kept = TxTag(label="kept").save()
        article.tags.add(kept)
        with pytest.raises(RuntimeError):
            with db.atomic():
                article.tags.add(TxTag(label="new").save())
                article.tags.clear()
                raise RuntimeError("boom")
        assert [t.label for t in article.tags.all()] == ["kept"]

    def test_fk_objects_roll_back_together(self, db):
        TxAuthor.create_table()
        TxPost.create_table()
        with pytest.raises(RuntimeError):
            with db.atomic():
                author = TxAuthor(name="Ada").save()
                TxPost(title="Hello", author=author).save()
                raise RuntimeError("boom")
        assert TxAuthor.all().count() == 0
        assert TxPost.all().count() == 0


# =========================================================================== #
# Atomic migrations
# =========================================================================== #

class TestAtomicMigrations:

    def test_failing_migration_rolls_back(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        database = connect(str(tmp_path / "app.db"))
        mdir = tmp_path / "app_migrations"
        mdir.mkdir()
        (mdir / "001_bad.py").write_text(
            "def forward(db):\n"
            "    db.execute('CREATE TABLE t1 (id INTEGER PRIMARY KEY)')\n"
            "    db.commit()\n"  # no-op inside the migration's transaction
            "    raise RuntimeError('migration boom')\n"
        )
        with pytest.raises(RuntimeError, match="migration boom"):
            migrate(database)
        tables = {
            r["name"]
            for r in database.raw(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "t1" not in tables
        assert database.raw("SELECT * FROM _migrations") == []

    def test_earlier_migrations_stay_applied(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        database = connect(str(tmp_path / "app.db"))
        mdir = tmp_path / "app_migrations"
        mdir.mkdir()
        (mdir / "001_good.py").write_text(
            "def forward(db):\n"
            "    db.execute('CREATE TABLE good (id INTEGER PRIMARY KEY)')\n"
        )
        (mdir / "002_bad.py").write_text(
            "def forward(db):\n"
            "    raise RuntimeError('boom')\n"
        )
        with pytest.raises(RuntimeError):
            migrate(database)
        tables = {
            r["name"]
            for r in database.raw(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "good" in tables
        applied = [r["name"] for r in database.raw("SELECT name FROM _migrations")]
        assert applied == ["001_good"]
