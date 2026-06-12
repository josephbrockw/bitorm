"""Tests for JSONField."""

import pytest

import bitorm.db as _db_module
from bitorm import Model, JSONField, connect, make_migration, migrate


# =========================================================================== #
# Module-level test models
# =========================================================================== #

class JsonRun(Model):
    name: str
    metrics: dict = JSONField()
    history: list = JSONField(default_factory=list)


class JsonStrict(Model):
    config: dict = JSONField(nullable=False)


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
# Round trips
# =========================================================================== #

class TestJSONRoundTrip:

    def test_dict_round_trip(self):
        JsonRun.create_table()
        JsonRun(name="exp1", metrics={"loss": 0.3, "acc": 0.91}).save()
        loaded = JsonRun.get(name="exp1")
        assert loaded.metrics == {"loss": 0.3, "acc": 0.91}
        assert isinstance(loaded.metrics, dict)

    def test_nested_structures(self):
        JsonRun.create_table()
        metrics = {"layers": [64, 32], "opt": {"name": "adam", "lr": 1e-3}}
        JsonRun(name="exp1", metrics=metrics).save()
        assert JsonRun.get(name="exp1").metrics == metrics

    def test_list_round_trip(self):
        JsonRun.create_table()
        JsonRun(name="exp1", metrics={}, history=[1, 2, 3]).save()
        assert JsonRun.get(name="exp1").history == [1, 2, 3]

    def test_none_round_trip(self):
        JsonRun.create_table()
        JsonRun(name="exp1", metrics=None).save()
        assert JsonRun.get(name="exp1").metrics is None

    def test_update_round_trip(self):
        JsonRun.create_table()
        run = JsonRun(name="exp1", metrics={"loss": 1.0}).save()
        run.metrics = {"loss": 0.1, "acc": 0.99}
        run.save()
        assert JsonRun.get(name="exp1").metrics == {"loss": 0.1, "acc": 0.99}

    def test_default_factory(self):
        JsonRun.create_table()
        run = JsonRun(name="exp1", metrics={})
        assert run.history == []
        run.history.append("step1")
        run.save()
        assert JsonRun.get(name="exp1").history == ["step1"]

    def test_default_factory_not_shared(self):
        a = JsonRun(name="a", metrics={})
        b = JsonRun(name="b", metrics={})
        a.history.append("x")
        assert b.history == []

    def test_stored_as_json_text(self, db):
        JsonRun.create_table()
        JsonRun(name="exp1", metrics={"loss": 0.3}).save()
        raw = db.raw("SELECT metrics FROM jsonrun")[0]["metrics"]
        assert raw == '{"loss": 0.3}'


# =========================================================================== #
# Validation and errors
# =========================================================================== #

class TestJSONValidation:

    def test_not_nullable_rejects_none(self):
        JsonStrict.create_table()
        with pytest.raises(ValueError, match="cannot be None"):
            JsonStrict(config=None).save()

    def test_non_serializable_raises_with_field_name(self):
        JsonRun.create_table()
        with pytest.raises(TypeError, match="metrics.*not JSON serializable"):
            JsonRun(name="exp1", metrics={"bad": object()}).save()

    def test_non_serializable_does_not_insert(self):
        JsonRun.create_table()
        with pytest.raises(TypeError):
            JsonRun(name="exp1", metrics={"bad": object()}).save()
        assert JsonRun.all().count() == 0


# =========================================================================== #
# Schema / migrations
# =========================================================================== #

class TestJSONSchema:

    def test_column_sql_type_is_text(self):
        col = next(c for c in JsonRun._columns if c.name == "metrics")
        assert col.sql_type == "TEXT"
        assert "TEXT" in col.ddl()

    def test_make_migration_and_migrate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        database = connect(str(tmp_path / "app.db"))
        path = make_migration(database, name="init", models=[JsonRun])
        assert path is not None
        migrate(database)
        JsonRun(name="exp1", metrics={"loss": 0.5}).save()
        assert JsonRun.get(name="exp1").metrics == {"loss": 0.5}
        # schema now matches the model: no further changes detected
        assert make_migration(database, name="again", models=[JsonRun]) is None
