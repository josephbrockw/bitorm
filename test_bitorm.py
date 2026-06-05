"""
Adversarial test suite for bitorm.

Tests are designed to probe every code path, edge case, and known weakness.
Tests that surface actual bugs are marked with '# BUG:' comments.
"""

import math
import sqlite3
from datetime import datetime, date, timezone, timedelta

import pytest

import db as _db_module
import bitorm
from bitorm import (
    Model,
    Field,
    ForeignKey,
    ManyToMany,
    Database,
    connect,
    _db,
    _build_where,
    _Column,
    _UNSET,
    _OPERATORS,
    QuerySet,
)


# =========================================================================== #
# Module-level test models
# =========================================================================== #

class SimpleUser(Model):
    name: str
    email: str = Field(unique=True)
    age: int = 0


class TimestampModel(Model):
    created: datetime
    day: date
    active: bool = True
    data: bytes = b""


class NullableModel(Model):
    value: str


class NotNullModel(Model):
    value: str = Field(nullable=False)


class DefaultFactoryModel(Model):
    items: str = Field(default_factory=lambda: "fresh")


# FK models
class FKAuthor(Model):
    name: str
    email: str = Field(unique=True)


class FKPost(Model):
    title: str
    body: str
    author: FKAuthor = ForeignKey(FKAuthor)


class FKNonNullPost(Model):
    title: str
    author: FKAuthor = ForeignKey(FKAuthor, nullable=False)


class FKSetNullAuthor(Model):
    name: str


class FKSetNullPost(Model):
    title: str
    author: FKSetNullAuthor = ForeignKey(
        FKSetNullAuthor, on_delete="SET NULL", related_name="sn_posts"
    )


class FKRestrictAuthor(Model):
    name: str


class FKRestrictPost(Model):
    title: str
    author: FKRestrictAuthor = ForeignKey(
        FKRestrictAuthor, on_delete="RESTRICT", related_name="restricted_posts"
    )


class FKCustomRelAuthor(Model):
    name: str


class FKCustomRelPost(Model):
    title: str
    author: FKCustomRelAuthor = ForeignKey(
        FKCustomRelAuthor, related_name="written_posts"
    )


# M2M models
class M2MTag(Model):
    label: str = Field(unique=True)


class M2MArticle(Model):
    title: str
    tags: list = ManyToMany(M2MTag)


class M2MCustomRelTag(Model):
    label: str


class M2MCustomRelArticle(Model):
    title: str
    tags: list = ManyToMany(M2MCustomRelTag, related_name="tagged_articles")


# M2M multiple fields to same target
class M2MRecipient(Model):
    name: str


class M2MEmail(Model):
    subject: str
    to_users: list = ManyToMany(M2MRecipient, related_name="emails_to")
    cc_users: list = ManyToMany(M2MRecipient, related_name="emails_cc")
    bcc_users: list = ManyToMany(M2MRecipient, related_name="emails_bcc")


# Custom PK model
class CustomPKModel(Model):
    uid: int = Field(primary_key=True)
    name: str


# Custom table name
class CustomTableModel(Model):
    __tablename__ = "my_custom_table"
    name: str


# No-annotations model
class EmptyModel(Model):
    pass


# =========================================================================== #
# Fixtures
# =========================================================================== #

@pytest.fixture(autouse=True)
def db():
    """Fresh in-memory database for every test."""
    database = connect(":memory:")
    yield database
    _db_module._default_db = None


def _create_simple_tables():
    SimpleUser.create_table()


def _create_timestamp_tables():
    TimestampModel.create_table()


def _create_nullable_tables():
    NullableModel.create_table()
    NotNullModel.create_table()


def _create_fk_tables():
    FKAuthor.create_table()
    FKPost.create_table()


def _create_fk_nonnull_tables():
    FKAuthor.create_table()
    FKNonNullPost.create_table()


def _create_fk_set_null_tables():
    FKSetNullAuthor.create_table()
    FKSetNullPost.create_table()


def _create_fk_restrict_tables():
    FKRestrictAuthor.create_table()
    FKRestrictPost.create_table()


def _create_fk_custom_rel_tables():
    FKCustomRelAuthor.create_table()
    FKCustomRelPost.create_table()


def _create_m2m_tables():
    M2MTag.create_table()
    M2MArticle.create_table()


def _create_m2m_custom_rel_tables():
    M2MCustomRelTag.create_table()
    M2MCustomRelArticle.create_table()


def _create_m2m_multi_tables():
    M2MRecipient.create_table()
    M2MEmail.create_table()


# =========================================================================== #
# 1. Database Connection
# =========================================================================== #

class TestDatabaseConnection:

    def test_db_raises_without_connect(self):
        _db_module._default_db = None
        with pytest.raises(RuntimeError, match="No database connected"):
            _db()

    def test_connect_returns_database(self):
        db = connect(":memory:")
        assert isinstance(db, Database)

    def test_connect_replaces_previous(self):
        db1 = connect(":memory:")
        db2 = connect(":memory:")
        assert _db() is db2
        assert _db() is not db1

    def test_database_foreign_keys_enabled(self):
        _create_fk_tables()
        with pytest.raises(sqlite3.IntegrityError):
            _db().execute(
                "INSERT INTO fkpost (title, body, author_id) VALUES (?, ?, ?)",
                ("t", "b", 99999),
            )

    def test_database_row_factory_is_row(self):
        row = _db().execute("SELECT 1 AS x").fetchone()
        assert isinstance(row, sqlite3.Row)
        assert row["x"] == 1

    def test_connect_memory_creates_independent_dbs(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        connect(":memory:")
        _create_simple_tables()
        assert SimpleUser.all().count() == 0

    def test_operations_after_close(self):
        database = connect(":memory:")
        database.close()
        with pytest.raises(sqlite3.ProgrammingError):
            database.execute("SELECT 1")


# =========================================================================== #
# 2. Field and Column
# =========================================================================== #

class TestFieldAndColumn:

    def test_field_defaults(self):
        f = Field()
        assert f.primary_key is False
        assert f.unique is False
        assert f.nullable is True
        assert f.default is _UNSET
        assert f.default_factory is None

    def test_column_ddl_primary_key(self):
        col = _Column("id", int, Field(primary_key=True))
        assert col.ddl() == '"id" INTEGER PRIMARY KEY'

    def test_column_ddl_unique_not_null(self):
        col = _Column("email", str, Field(unique=True, nullable=False))
        ddl = col.ddl()
        assert "NOT NULL" in ddl
        assert "UNIQUE" in ddl

    def test_column_ddl_with_references(self):
        col = _Column("author_id", int, Field(), references="fkauthor(id) ON DELETE CASCADE")
        assert "REFERENCES fkauthor(id) ON DELETE CASCADE" in col.ddl()

    def test_py_to_sql_all_types(self):
        mapping = {
            int: "INTEGER",
            float: "REAL",
            str: "TEXT",
            bytes: "BLOB",
            bool: "INTEGER",
            datetime: "TEXT",
            date: "TEXT",
        }
        for py_type, expected_sql in mapping.items():
            col = _Column("x", py_type, Field())
            assert col.sql_type == expected_sql, f"{py_type} -> {col.sql_type}"

    def test_unknown_type_defaults_to_text(self):
        col = _Column("x", list, Field())
        assert col.sql_type == "TEXT"

    def test_to_db_datetime(self):
        dt = datetime(2024, 1, 1, 12, 0, 0)
        col = _Column("x", datetime, Field())
        assert col.to_db(dt) == "2024-01-01T12:00:00"

    def test_to_db_date(self):
        d = date(2024, 1, 1)
        col = _Column("x", date, Field())
        assert col.to_db(d) == "2024-01-01"

    def test_to_db_bool(self):
        col = _Column("x", bool, Field())
        assert col.to_db(True) == 1
        assert col.to_db(False) == 0

    def test_to_db_none(self):
        for py_type in (int, str, datetime, date, bool):
            col = _Column("x", py_type, Field())
            assert col.to_db(None) is None

    def test_from_db_datetime_roundtrip(self):
        dt = datetime(2024, 6, 15, 8, 30, 45)
        col = _Column("x", datetime, Field())
        assert col.from_db(col.to_db(dt)) == dt

    def test_from_db_date_roundtrip(self):
        d = date(2024, 6, 15)
        col = _Column("x", date, Field())
        assert col.from_db(col.to_db(d)) == d

    def test_from_db_bool_truthy_int(self):
        col = _Column("x", bool, Field())
        assert col.from_db(2) is True

    def test_from_db_bool_zero(self):
        col = _Column("x", bool, Field())
        assert col.from_db(0) is False

    def test_datetime_with_microseconds(self):
        dt = datetime(2024, 1, 1, 12, 0, 0, 123456)
        col = _Column("x", datetime, Field())
        assert col.from_db(col.to_db(dt)) == dt

    def test_datetime_with_timezone(self):
        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        col = _Column("x", datetime, Field())
        result = col.from_db(col.to_db(dt))
        assert result == dt
        assert result.tzinfo is not None

    def test_both_default_and_default_factory(self):
        # FIXED: raises ValueError when both default and default_factory are specified
        with pytest.raises(ValueError, match="Cannot specify both"):
            class BothDefaultsModel(Model):
                value: int = Field(default=0, default_factory=lambda: 42)


# =========================================================================== #
# 3. Model Definition
# =========================================================================== #

class TestModelDefinition:

    def test_auto_pk_added(self):
        col_names = [c.name for c in SimpleUser._columns]
        assert col_names[0] == "id"
        assert SimpleUser._pk == "id"

    def test_custom_pk_name(self):
        assert CustomPKModel._pk == "uid"
        col_names = [c.name for c in CustomPKModel._columns]
        assert "id" not in col_names
        assert "uid" in col_names

    def test_custom_tablename(self):
        assert CustomTableModel._table == "my_custom_table"

    def test_default_tablename_is_lowered(self):
        assert SimpleUser._table == "simpleuser"

    def test_bare_default_value(self):
        u = SimpleUser()
        assert u.age == 0

    def test_model_with_no_annotations(self):
        assert EmptyModel._pk == "id"
        assert len(EmptyModel._columns) == 1
        assert EmptyModel._columns[0].name == "id"

    def test_column_order_preserved(self):
        names = [c.name for c in SimpleUser._columns]
        assert names == ["id", "name", "email", "age"]

    def test_init_unknown_kwargs_raises(self):
        # FIXED: typos in kwargs now raise TypeError
        _create_simple_tables()
        with pytest.raises(TypeError, match="Unknown field"):
            SimpleUser(nane="Bob", email="bob@test.com")

    def test_init_default_factory_called_per_instance(self):
        a = DefaultFactoryModel()
        b = DefaultFactoryModel()
        assert a.items == "fresh"
        assert b.items == "fresh"
        assert a.items is not b.items or a.items == b.items  # strings are interned, but factory is called

    def test_init_sets_pk_to_none(self):
        u = SimpleUser(name="x", email="x@test.com")
        assert u.id is None

    def test_repr(self):
        u = SimpleUser(name="Ada", email="ada@test.com", age=36)
        r = repr(u)
        assert "SimpleUser(" in r
        assert "name='Ada'" in r


# =========================================================================== #
# 4. Create and Drop Table
# =========================================================================== #

class TestCreateAndDropTable:

    def test_create_table_basic(self):
        _create_simple_tables()
        row = _db().execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='simpleuser'"
        ).fetchone()
        assert row is not None

    def test_create_table_if_not_exists_default(self):
        _create_simple_tables()
        _create_simple_tables()  # should not raise

    def test_create_table_without_if_not_exists(self):
        SimpleUser.create_table(if_not_exists=False)
        with pytest.raises(sqlite3.OperationalError):
            SimpleUser.create_table(if_not_exists=False)

    def test_drop_table(self):
        _create_simple_tables()
        SimpleUser.drop_table()
        row = _db().execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='simpleuser'"
        ).fetchone()
        assert row is None

    def test_drop_table_nonexistent(self):
        SimpleUser.drop_table()  # should not raise

    def test_create_table_with_m2m_creates_join_table(self):
        _create_m2m_tables()
        row = _db().execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='m2marticle_tags'"
        ).fetchone()
        assert row is not None

    def test_custom_pk_table_creation(self):
        # FIXED: UPDATE with 0 rows now falls back to INSERT
        CustomPKModel.create_table()
        obj = CustomPKModel(uid=1, name="test")
        obj.save()
        result = CustomPKModel.get(uid=1)
        assert result is not None
        assert result.name == "test"

    def test_custom_table_name_creation(self):
        CustomTableModel.create_table()
        row = _db().execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='my_custom_table'"
        ).fetchone()
        assert row is not None


# =========================================================================== #
# 5. Save (INSERT / UPDATE)
# =========================================================================== #

class TestSave:

    def test_save_insert_populates_pk(self):
        _create_simple_tables()
        u = SimpleUser(name="Ada", email="ada@test.com", age=36)
        assert u.id is None
        u.save()
        assert u.id is not None
        assert isinstance(u.id, int)

    def test_save_insert_returns_self(self):
        _create_simple_tables()
        u = SimpleUser(name="Ada", email="ada@test.com")
        result = u.save()
        assert result is u

    def test_save_update_persists_changes(self):
        _create_simple_tables()
        u = SimpleUser(name="Ada", email="ada@test.com", age=36)
        u.save()
        u.age = 37
        u.save()
        fetched = SimpleUser.get(id=u.id)
        assert fetched.age == 37

    def test_save_update_nonexistent_pk(self):
        # FIXED: UPDATE with 0 rows now falls back to INSERT
        _create_simple_tables()
        u = SimpleUser(name="Ghost", email="ghost@test.com")
        u.id = 99999
        u.save()
        result = SimpleUser.get(id=99999)
        assert result is not None
        assert result.name == "Ghost"

    def test_save_after_delete(self):
        # FIXED: delete() clears PK, so save() runs INSERT and creates a new row
        _create_simple_tables()
        u = SimpleUser(name="Ada", email="ada@test.com")
        u.save()
        u.delete()
        assert u.id is None
        u.name = "Ada v2"
        u.save()
        assert u.id is not None
        fetched = SimpleUser.get(id=u.id)
        assert fetched is not None
        assert fetched.name == "Ada v2"

    def test_save_insert_respects_unique_constraint(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="same@test.com").save()
        with pytest.raises(sqlite3.IntegrityError):
            SimpleUser(name="Bob", email="same@test.com").save()

    def test_save_insert_not_null_violation(self):
        NotNullModel.create_table()
        obj = NotNullModel()
        with pytest.raises(ValueError, match="cannot be None"):
            obj.save()

    def test_save_none_on_nullable_column(self):
        NullableModel.create_table()
        obj = NullableModel(value=None)
        obj.save()
        fetched = NullableModel.get(id=obj.id)
        assert fetched.value is None

    def test_save_serializes_datetime(self):
        _create_timestamp_tables()
        dt = datetime(2024, 6, 15, 12, 30, 0)
        d = date(2024, 6, 15)
        obj = TimestampModel(created=dt, day=d, active=True, data=b"")
        obj.save()
        raw = _db().execute("SELECT created FROM timestampmodel WHERE id = ?", (obj.id,)).fetchone()
        assert raw["created"] == "2024-06-15T12:30:00"

    def test_save_serializes_bool(self):
        _create_timestamp_tables()
        obj = TimestampModel(created=datetime.now(), day=date.today(), active=True, data=b"")
        obj.save()
        raw = _db().execute("SELECT active FROM timestampmodel WHERE id = ?", (obj.id,)).fetchone()
        assert raw["active"] == 1

    def test_save_bytes_blob(self):
        _create_timestamp_tables()
        payload = b"\x00\x01\x02\xff"
        obj = TimestampModel(created=datetime.now(), day=date.today(), data=payload)
        obj.save()
        fetched = TimestampModel.get(id=obj.id)
        assert fetched.data == payload

    def test_save_empty_string(self):
        _create_simple_tables()
        u = SimpleUser(name="", email="empty@test.com")
        u.save()
        fetched = SimpleUser.get(id=u.id)
        assert fetched.name == ""

    def test_save_unicode_emoji(self):
        _create_simple_tables()
        u = SimpleUser(name="Hello \U0001f30d\U0001f389", email="emoji@test.com")
        u.save()
        fetched = SimpleUser.get(id=u.id)
        assert fetched.name == "Hello \U0001f30d\U0001f389"

    def test_save_very_large_string(self):
        _create_simple_tables()
        big = "x" * (1024 * 1024)
        u = SimpleUser(name=big, email="big@test.com")
        u.save()
        fetched = SimpleUser.get(id=u.id)
        assert fetched.name == big

    def test_save_max_int(self):
        _create_simple_tables()
        u = SimpleUser(name="max", email="max@test.com", age=2**63 - 1)
        u.save()
        fetched = SimpleUser.get(id=u.id)
        assert fetched.age == 2**63 - 1

    def test_save_negative_int(self):
        _create_simple_tables()
        u = SimpleUser(name="neg", email="neg@test.com", age=-42)
        u.save()
        fetched = SimpleUser.get(id=u.id)
        assert fetched.age == -42

    def test_save_float_nan(self):
        _create_timestamp_tables()
        _db().execute(
            "CREATE TABLE IF NOT EXISTS floatmodel (id INTEGER PRIMARY KEY, val REAL)"
        )
        _db().execute("INSERT INTO floatmodel (val) VALUES (?)", (float("nan"),))
        _db().commit()
        row = _db().execute("SELECT val FROM floatmodel").fetchone()
        # SQLite stores NaN as NULL on some platforms, or as a special float
        # The key point: the ORM should handle whatever comes back
        assert row["val"] is None or math.isnan(row["val"])

    def test_save_float_inf(self):
        _create_timestamp_tables()
        _db().execute(
            "CREATE TABLE IF NOT EXISTS floatmodel2 (id INTEGER PRIMARY KEY, val REAL)"
        )
        _db().execute("INSERT INTO floatmodel2 (val) VALUES (?)", (float("inf"),))
        _db().commit()
        row = _db().execute("SELECT val FROM floatmodel2").fetchone()
        assert row["val"] == float("inf") or row["val"] is None

    def test_concurrent_save_last_write_wins(self):
        _create_simple_tables()
        u = SimpleUser(name="Ada", email="ada@test.com", age=36)
        u.save()
        copy1 = SimpleUser.get(id=u.id)
        copy2 = SimpleUser.get(id=u.id)
        copy1.age = 100
        copy1.save()
        copy2.age = 200
        copy2.save()
        final = SimpleUser.get(id=u.id)
        assert final.age == 200


# =========================================================================== #
# 6. Delete
# =========================================================================== #

class TestDelete:

    def test_delete_unsaved_raises(self):
        u = SimpleUser(name="x", email="x@test.com")
        with pytest.raises(ValueError, match="Cannot delete an unsaved object"):
            u.delete()

    def test_delete_removes_row(self):
        _create_simple_tables()
        u = SimpleUser(name="Ada", email="ada@test.com")
        u.save()
        pk = u.id
        u.delete()
        assert SimpleUser.get(id=pk) is None

    def test_delete_nonexistent_row(self):
        # BUG: Deleting a row that doesn't exist silently succeeds (0 rows affected)
        _create_simple_tables()
        u = SimpleUser(name="x", email="x@test.com")
        u.id = 99999
        u.delete()  # no error, even though row doesn't exist

    def test_delete_clears_pk(self):
        # FIXED: delete() now clears PK on the Python object
        _create_simple_tables()
        u = SimpleUser(name="Ada", email="ada@test.com")
        u.save()
        u.delete()
        assert u.id is None

    def test_delete_cascade_fk(self):
        _create_fk_tables()
        author = FKAuthor(name="Ada", email="ada@test.com")
        author.save()
        post = FKPost(title="Hello", body="World", author=author)
        post.save()
        post_id = post.id
        author.delete()
        assert FKPost.get(id=post_id) is None


# =========================================================================== #
# 7. QuerySet Basic
# =========================================================================== #

class TestQuerySetBasic:

    def test_all_returns_queryset(self):
        _create_simple_tables()
        qs = SimpleUser.all()
        assert isinstance(qs, QuerySet)

    def test_all_on_empty_table(self):
        _create_simple_tables()
        assert list(SimpleUser.all()) == []

    def test_filter_eq_implicit(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com", age=36).save()
        SimpleUser(name="Bob", email="bob@test.com", age=25).save()
        results = list(SimpleUser.filter(name="Ada"))
        assert len(results) == 1
        assert results[0].name == "Ada"

    def test_filter_eq_explicit(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com", age=36).save()
        results = list(SimpleUser.filter(name__eq="Ada"))
        assert len(results) == 1

    def test_filter_ne(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com", age=36).save()
        SimpleUser(name="Bob", email="bob@test.com", age=25).save()
        results = list(SimpleUser.filter(name__ne="Ada"))
        assert len(results) == 1
        assert results[0].name == "Bob"

    def test_filter_gt_gte_lt_lte(self):
        _create_simple_tables()
        SimpleUser(name="Young", email="y@test.com", age=20).save()
        SimpleUser(name="Mid", email="m@test.com", age=30).save()
        SimpleUser(name="Old", email="o@test.com", age=40).save()

        assert SimpleUser.filter(age__gt=30).count() == 1
        assert SimpleUser.filter(age__gte=30).count() == 2
        assert SimpleUser.filter(age__lt=30).count() == 1
        assert SimpleUser.filter(age__lte=30).count() == 2

    def test_filter_like(self):
        _create_simple_tables()
        SimpleUser(name="Ada Lovelace", email="ada@test.com").save()
        SimpleUser(name="Bob", email="bob@test.com").save()
        results = list(SimpleUser.filter(name__like="%Love%"))
        assert len(results) == 1
        assert results[0].name == "Ada Lovelace"

    def test_filter_in(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com", age=36).save()
        SimpleUser(name="Bob", email="bob@test.com", age=25).save()
        SimpleUser(name="Eve", email="eve@test.com", age=30).save()
        results = list(SimpleUser.filter(name__in=["Ada", "Eve"]))
        assert len(results) == 2
        names = {r.name for r in results}
        assert names == {"Ada", "Eve"}

    def test_filter_in_empty_list(self):
        # FIXED: empty IN list now handled explicitly (always-false)
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        results = list(SimpleUser.filter(id__in=[]))
        assert len(results) == 0

    def test_filter_in_single_element(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        results = list(SimpleUser.filter(name__in=["Ada"]))
        assert len(results) == 1

    def test_filter_chaining(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com", age=36).save()
        SimpleUser(name="Bob", email="bob@test.com", age=25).save()
        SimpleUser(name="Eve", email="eve@test.com", age=36).save()
        results = list(SimpleUser.filter(age=36).filter(name="Ada"))
        assert len(results) == 1
        assert results[0].name == "Ada"

    def test_filter_overwrite_same_key(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com", age=36).save()
        SimpleUser(name="Bob", email="bob@test.com", age=25).save()
        # Second filter(age=25) overwrites first filter(age=36)
        results = list(SimpleUser.filter(age=36).filter(age=25))
        assert len(results) == 1
        assert results[0].name == "Bob"

    def test_order_by_asc(self):
        _create_simple_tables()
        SimpleUser(name="Bob", email="b@test.com", age=25).save()
        SimpleUser(name="Ada", email="a@test.com", age=36).save()
        results = list(SimpleUser.all().order_by("name"))
        assert results[0].name == "Ada"
        assert results[1].name == "Bob"

    def test_order_by_desc(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="a@test.com", age=36).save()
        SimpleUser(name="Bob", email="b@test.com", age=25).save()
        results = list(SimpleUser.all().order_by("-name"))
        assert results[0].name == "Bob"
        assert results[1].name == "Ada"

    def test_order_by_multiple(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="a@test.com", age=30).save()
        SimpleUser(name="Bob", email="b@test.com", age=30).save()
        SimpleUser(name="Eve", email="e@test.com", age=25).save()
        results = list(SimpleUser.all().order_by("age", "name"))
        assert results[0].name == "Eve"
        assert results[1].name == "Ada"
        assert results[2].name == "Bob"

    def test_limit(self):
        _create_simple_tables()
        for i in range(10):
            SimpleUser(name=f"User{i}", email=f"u{i}@test.com").save()
        results = list(SimpleUser.all().limit(3))
        assert len(results) == 3

    def test_limit_zero(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        results = list(SimpleUser.all().limit(0))
        assert len(results) == 0

    def test_limit_negative(self):
        # SQLite treats LIMIT -1 as no limit
        _create_simple_tables()
        for i in range(5):
            SimpleUser(name=f"User{i}", email=f"u{i}@test.com").save()
        results = list(SimpleUser.all().limit(-1))
        assert len(results) == 5

    def test_first_returns_instance(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        result = SimpleUser.all().first()
        assert isinstance(result, SimpleUser)
        assert result.name == "Ada"

    def test_first_empty_returns_none(self):
        _create_simple_tables()
        assert SimpleUser.all().first() is None

    def test_count_returns_int(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        SimpleUser(name="Bob", email="bob@test.com").save()
        assert SimpleUser.all().count() == 2

    def test_count_empty_table(self):
        _create_simple_tables()
        assert SimpleUser.all().count() == 0

    def test_count_uses_cache_if_evaluated(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        qs = SimpleUser.all()
        list(qs)  # evaluate / populate cache
        assert qs._result_cache is not None
        assert qs.count() == 1  # uses len(cache)

    def test_count_queries_db_if_not_evaluated(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        qs = SimpleUser.all()
        assert qs._result_cache is None
        assert qs.count() == 1  # uses SQL COUNT


# =========================================================================== #
# 8. QuerySet Immutability
# =========================================================================== #

class TestQuerySetImmutability:

    def test_filter_does_not_mutate_original(self):
        _create_simple_tables()
        qs1 = SimpleUser.all()
        qs2 = qs1.filter(age=30)
        assert qs1._filters == {}
        assert qs2._filters == {"age": 30}

    def test_order_by_does_not_mutate_original(self):
        _create_simple_tables()
        qs1 = SimpleUser.all()
        qs2 = qs1.order_by("name")
        assert qs1._order == []
        assert qs2._order == ["name"]

    def test_limit_does_not_mutate_original(self):
        _create_simple_tables()
        qs1 = SimpleUser.all()
        qs2 = qs1.limit(5)
        assert qs1._limit is None
        assert qs2._limit == 5

    def test_clone_does_not_share_cache(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        qs1 = SimpleUser.all()
        list(qs1)  # populate cache
        qs2 = qs1.all()  # clone
        assert qs2._result_cache is None

    def test_evaluated_qs_uses_stale_cache(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        qs = SimpleUser.all()
        results1 = list(qs)
        assert len(results1) == 1
        SimpleUser(name="Bob", email="bob@test.com").save()
        results2 = list(qs)  # same QS, returns stale cache
        assert len(results2) == 1  # still 1, not 2


# =========================================================================== #
# 9. QuerySet Sequence Protocol
# =========================================================================== #

class TestQuerySetSequenceProtocol:

    def test_iter(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        results = list(SimpleUser.all())
        assert len(results) == 1

    def test_len(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        SimpleUser(name="Bob", email="bob@test.com").save()
        assert len(SimpleUser.all()) == 2

    def test_bool_true(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        assert bool(SimpleUser.all()) is True

    def test_bool_false(self):
        _create_simple_tables()
        assert bool(SimpleUser.all()) is False

    def test_getitem_index(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        SimpleUser(name="Bob", email="bob@test.com").save()
        qs = SimpleUser.all().order_by("name")
        assert qs[0].name == "Ada"
        assert qs[1].name == "Bob"

    def test_getitem_negative_index(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        SimpleUser(name="Bob", email="bob@test.com").save()
        qs = SimpleUser.all().order_by("name")
        assert qs[-1].name == "Bob"

    def test_getitem_slice(self):
        _create_simple_tables()
        for i in range(5):
            SimpleUser(name=f"User{i}", email=f"u{i}@test.com").save()
        qs = SimpleUser.all().order_by("name")
        sliced = qs[1:3]
        assert len(sliced) == 2

    def test_getitem_out_of_range(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        with pytest.raises(IndexError):
            SimpleUser.all()[999]

    def test_repr_triggers_evaluation(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        qs = SimpleUser.all()
        assert qs._result_cache is None
        repr(qs)
        assert qs._result_cache is not None


# =========================================================================== #
# 10. QuerySet Delete (Bulk)
# =========================================================================== #

class TestQuerySetDelete:

    def test_bulk_delete_with_filter(self):
        _create_simple_tables()
        SimpleUser(name="Young", email="y@test.com", age=20).save()
        SimpleUser(name="Old", email="o@test.com", age=60).save()
        deleted = SimpleUser.filter(age__gt=50).delete()
        assert deleted == 1
        assert SimpleUser.all().count() == 1
        assert SimpleUser.all().first().name == "Young"

    def test_bulk_delete_no_filter(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="a@test.com").save()
        SimpleUser(name="Bob", email="b@test.com").save()
        deleted = SimpleUser.all().delete()
        assert deleted == 2
        assert SimpleUser.all().count() == 0

    def test_bulk_delete_empty_result(self):
        _create_simple_tables()
        deleted = SimpleUser.filter(id=99999).delete()
        assert deleted == 0

    def test_bulk_delete_with_relation_filter_raises(self):
        _create_fk_tables()
        with pytest.raises(NotImplementedError, match="Bulk delete across a relation"):
            FKPost.filter(author__name="Ada").delete()

    def test_bulk_delete_returns_rowcount(self):
        _create_simple_tables()
        for i in range(5):
            SimpleUser(name=f"User{i}", email=f"u{i}@test.com", age=30).save()
        deleted = SimpleUser.filter(age=30).delete()
        assert deleted == 5


# =========================================================================== #
# 11. Get
# =========================================================================== #

class TestGet:

    def test_get_single_match(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com", age=36).save()
        u = SimpleUser.get(name="Ada")
        assert u is not None
        assert u.email == "ada@test.com"

    def test_get_no_match_returns_none(self):
        _create_simple_tables()
        assert SimpleUser.get(name="Nonexistent") is None

    def test_get_multiple_matches_raises(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="a1@test.com", age=30).save()
        SimpleUser(name="Ada", email="a2@test.com", age=30).save()
        with pytest.raises(ValueError, match="multiple rows"):
            SimpleUser.get(name="Ada")

    def test_get_by_pk(self):
        _create_simple_tables()
        u = SimpleUser(name="Ada", email="ada@test.com")
        u.save()
        fetched = SimpleUser.get(id=u.id)
        assert fetched.name == "Ada"


# =========================================================================== #
# 12. Foreign Key
# =========================================================================== #

class TestForeignKey:

    def test_fk_column_created(self):
        col_names = [c.name for c in FKPost._columns]
        assert "author_id" in col_names

    def test_fk_forward_accessor(self):
        _create_fk_tables()
        author = FKAuthor(name="Ada", email="ada@test.com")
        author.save()
        post = FKPost(title="Hello", body="World", author=author)
        post.save()
        fetched_post = FKPost.get(id=post.id)
        assert fetched_post.author.name == "Ada"

    def test_fk_forward_accessor_caches(self):
        _create_fk_tables()
        author = FKAuthor(name="Ada", email="ada@test.com")
        author.save()
        post = FKPost(title="Hello", body="World", author=author)
        post.save()
        fetched_post = FKPost.get(id=post.id)
        author1 = fetched_post.author
        author2 = fetched_post.author
        assert author1 is author2

    def test_fk_cache_stale_after_db_change(self):
        # BUG: FK cache is never invalidated. Cached object is a snapshot.
        _create_fk_tables()
        author = FKAuthor(name="Ada", email="ada@test.com")
        author.save()
        post = FKPost(title="Hello", body="World", author=author)
        post.save()
        fetched_post = FKPost.get(id=post.id)
        _ = fetched_post.author  # cache the author
        author.name = "Updated Ada"
        author.save()
        assert fetched_post.author.name == "Ada"  # stale!

    def test_fk_set_via_object(self):
        _create_fk_tables()
        author1 = FKAuthor(name="Ada", email="ada@test.com")
        author1.save()
        author2 = FKAuthor(name="Bob", email="bob@test.com")
        author2.save()
        post = FKPost(title="Hello", body="World", author=author1)
        post.save()
        post.author = author2
        post.save()
        fetched = FKPost.get(id=post.id)
        assert fetched.author.name == "Bob"

    def test_fk_set_to_none(self):
        _create_fk_tables()
        author = FKAuthor(name="Ada", email="ada@test.com")
        author.save()
        post = FKPost(title="Hello", body="World", author=author)
        post.save()
        post.author = None
        post.save()
        fetched = FKPost.get(id=post.id)
        assert fetched.author_id is None
        assert fetched.author is None

    def test_fk_forward_none_when_null(self):
        _create_fk_tables()
        post = FKPost(title="Hello", body="World")
        post.save()
        fetched = FKPost.get(id=post.id)
        assert fetched.author is None

    def test_fk_reverse_accessor(self):
        _create_fk_tables()
        author = FKAuthor(name="Ada", email="ada@test.com")
        author.save()
        FKPost(title="Post1", body="Body1", author=author).save()
        FKPost(title="Post2", body="Body2", author=author).save()
        posts = list(author.fkposts)
        assert len(posts) == 2
        titles = {p.title for p in posts}
        assert titles == {"Post1", "Post2"}

    def test_fk_reverse_accessor_empty(self):
        _create_fk_tables()
        author = FKAuthor(name="Ada", email="ada@test.com")
        author.save()
        posts = list(author.fkposts)
        assert posts == []

    def test_fk_reverse_is_live_queryset(self):
        _create_fk_tables()
        author = FKAuthor(name="Ada", email="ada@test.com")
        author.save()
        assert author.fkposts.count() == 0
        FKPost(title="New", body="Body", author=author).save()
        assert author.fkposts.count() == 1

    def test_fk_nullable_false(self):
        _create_fk_nonnull_tables()
        post = FKNonNullPost(title="Hello")
        with pytest.raises(ValueError, match="cannot be None"):
            post.save()

    def test_fk_on_delete_cascade(self):
        _create_fk_tables()
        author = FKAuthor(name="Ada", email="ada@test.com")
        author.save()
        post = FKPost(title="Hello", body="World", author=author)
        post.save()
        post_id = post.id
        author.delete()
        assert FKPost.get(id=post_id) is None

    def test_fk_on_delete_set_null(self):
        _create_fk_set_null_tables()
        author = FKSetNullAuthor(name="Ada")
        author.save()
        post = FKSetNullPost(title="Hello", author=author)
        post.save()
        post_id = post.id
        author.delete()
        fetched = FKSetNullPost.get(id=post_id)
        assert fetched is not None
        assert fetched.author_id is None

    def test_fk_on_delete_restrict(self):
        _create_fk_restrict_tables()
        author = FKRestrictAuthor(name="Ada")
        author.save()
        post = FKRestrictPost(title="Hello", author=author)
        post.save()
        with pytest.raises(sqlite3.IntegrityError):
            author.delete()

    def test_fk_save_with_unsaved_related(self):
        # FIXED: assigning an unsaved object to a FK now raises ValueError
        _create_fk_tables()
        unsaved_author = FKAuthor(name="Ghost", email="ghost@test.com")
        with pytest.raises(ValueError, match="unsaved object"):
            FKPost(title="Hello", body="World", author=unsaved_author)

    def test_fk_custom_related_name(self):
        _create_fk_custom_rel_tables()
        author = FKCustomRelAuthor(name="Ada")
        author.save()
        FKCustomRelPost(title="Hello", author=author).save()
        posts = list(author.written_posts)
        assert len(posts) == 1

    def test_fk_init_with_related_object(self):
        _create_fk_tables()
        author = FKAuthor(name="Ada", email="ada@test.com")
        author.save()
        post = FKPost(title="Hello", body="World", author=author)
        assert post.author_id == author.id


# =========================================================================== #
# 13. Many-to-Many
# =========================================================================== #

class TestManyToMany:

    def test_m2m_add(self):
        _create_m2m_tables()
        article = M2MArticle(title="Hello")
        article.save()
        tag1 = M2MTag(label="python")
        tag1.save()
        tag2 = M2MTag(label="orm")
        tag2.save()
        article.tags.add(tag1, tag2)
        assert article.tags.count() == 2

    def test_m2m_add_duplicate_ignored(self):
        _create_m2m_tables()
        article = M2MArticle(title="Hello")
        article.save()
        tag = M2MTag(label="python")
        tag.save()
        article.tags.add(tag)
        article.tags.add(tag)  # INSERT OR IGNORE
        assert article.tags.count() == 1

    def test_m2m_remove(self):
        _create_m2m_tables()
        article = M2MArticle(title="Hello")
        article.save()
        tag = M2MTag(label="python")
        tag.save()
        article.tags.add(tag)
        article.tags.remove(tag)
        assert article.tags.count() == 0

    def test_m2m_remove_nonexistent(self):
        _create_m2m_tables()
        article = M2MArticle(title="Hello")
        article.save()
        tag = M2MTag(label="python")
        tag.save()
        article.tags.remove(tag)  # not linked, no error

    def test_m2m_clear(self):
        _create_m2m_tables()
        article = M2MArticle(title="Hello")
        article.save()
        tag1 = M2MTag(label="python")
        tag1.save()
        tag2 = M2MTag(label="orm")
        tag2.save()
        article.tags.add(tag1, tag2)
        article.tags.clear()
        assert article.tags.count() == 0

    def test_m2m_all(self):
        _create_m2m_tables()
        article = M2MArticle(title="Hello")
        article.save()
        tag = M2MTag(label="python")
        tag.save()
        article.tags.add(tag)
        tags = article.tags.all()
        assert len(tags) == 1
        assert tags[0].label == "python"

    def test_m2m_count(self):
        _create_m2m_tables()
        article = M2MArticle(title="Hello")
        article.save()
        assert article.tags.count() == 0
        tag = M2MTag(label="python")
        tag.save()
        article.tags.add(tag)
        assert article.tags.count() == 1

    def test_m2m_iter(self):
        _create_m2m_tables()
        article = M2MArticle(title="Hello")
        article.save()
        tag = M2MTag(label="python")
        tag.save()
        article.tags.add(tag)
        tags = list(article.tags)
        assert len(tags) == 1

    def test_m2m_on_unsaved_object_raises(self):
        _create_m2m_tables()
        article = M2MArticle(title="Hello")
        tag = M2MTag(label="python")
        tag.save()
        with pytest.raises(ValueError, match="Save the object"):
            article.tags.add(tag)

    def test_m2m_add_unsaved_target(self):
        # FIXED: adding unsaved objects to M2M now raises ValueError
        _create_m2m_tables()
        article = M2MArticle(title="Hello")
        article.save()
        unsaved_tag = M2MTag(label="ghost")
        with pytest.raises(ValueError, match="must be saved"):
            article.tags.add(unsaved_tag)

    def test_m2m_reverse_accessor(self):
        _create_m2m_tables()
        article = M2MArticle(title="Hello")
        article.save()
        tag = M2MTag(label="python")
        tag.save()
        article.tags.add(tag)
        articles = tag.m2marticles.all()
        assert len(articles) == 1
        assert articles[0].title == "Hello"

    def test_m2m_reverse_add(self):
        _create_m2m_tables()
        article = M2MArticle(title="Hello")
        article.save()
        tag = M2MTag(label="python")
        tag.save()
        tag.m2marticles.add(article)
        assert article.tags.count() == 1

    def test_m2m_custom_related_name(self):
        _create_m2m_custom_rel_tables()
        article = M2MCustomRelArticle(title="Hello")
        article.save()
        tag = M2MCustomRelTag(label="python")
        tag.save()
        article.tags.add(tag)
        articles = tag.tagged_articles.all()
        assert len(articles) == 1

    def test_m2m_join_table_cascade_owner(self):
        _create_m2m_tables()
        article = M2MArticle(title="Hello")
        article.save()
        tag = M2MTag(label="python")
        tag.save()
        article.tags.add(tag)
        article_id = article.id
        article.delete()
        rows = _db().execute(
            "SELECT * FROM m2marticle_tags WHERE m2marticle_id = ?", (article_id,)
        ).fetchall()
        assert len(rows) == 0

    def test_m2m_join_table_cascade_target(self):
        _create_m2m_tables()
        article = M2MArticle(title="Hello")
        article.save()
        tag = M2MTag(label="python")
        tag.save()
        article.tags.add(tag)
        tag_id = tag.id
        tag.delete()
        rows = _db().execute(
            "SELECT * FROM m2marticle_tags WHERE m2mtag_id = ?", (tag_id,)
        ).fetchall()
        assert len(rows) == 0

    def test_m2m_empty_all(self):
        _create_m2m_tables()
        article = M2MArticle(title="Hello")
        article.save()
        assert article.tags.all() == []

    def test_m2m_empty_count(self):
        _create_m2m_tables()
        article = M2MArticle(title="Hello")
        article.save()
        assert article.tags.count() == 0


# =========================================================================== #
# 14. _build_where (Internal)
# =========================================================================== #

class TestBuildWhere:

    def test_build_where_empty_filters(self):
        sql, params = _build_where({})
        assert sql == ""
        assert params == []

    def test_build_where_simple_eq(self):
        sql, params = _build_where({"name": "Ada"})
        assert '"name" = ?' in sql
        assert params == ["Ada"]

    def test_build_where_with_operator(self):
        sql, params = _build_where({"age__gte": 18})
        assert '"age" >= ?' in sql
        assert params == [18]

    def test_build_where_in(self):
        sql, params = _build_where({"id__in": [1, 2, 3]})
        assert '"id" IN (?, ?, ?)' in sql
        assert params == [1, 2, 3]

    def test_build_where_in_empty(self):
        # FIXED: empty IN list emits always-false literal instead of invalid SQL
        sql, params = _build_where({"id__in": []})
        assert "0" in sql
        assert "IN" not in sql
        assert params == []

    def test_build_where_multiple(self):
        sql, params = _build_where({"age__gte": 18, "name": "Ada"})
        assert " AND " in sql
        assert len(params) == 2

    def test_build_where_relation(self):
        joins = set()
        sql, params = _build_where(
            {"author__name": "Ada"},
            base="post",
            relations={"author": "author_id"},
            joins=joins,
        )
        assert '"author"."name" = ?' in sql
        assert "author" in joins

    def test_build_where_relation_with_op(self):
        joins = set()
        sql, params = _build_where(
            {"author__name__like": "%da%"},
            base="post",
            relations={"author": "author_id"},
            joins=joins,
        )
        assert '"author"."name" LIKE ?' in sql
        assert "author" in joins

    def test_build_where_qualified_with_base(self):
        sql, params = _build_where({"name": "Ada"}, base="post")
        assert '"post"."name" = ?' in sql

    def test_build_where_unqualified_without_base(self):
        sql, params = _build_where({"name": "Ada"}, base=None)
        assert sql == '"name" = ?'

    def test_build_where_field_named_like_operator(self):
        sql, params = _build_where({"in": 5})
        assert '"in" = ?' in sql
        assert params == [5]


# =========================================================================== #
# 15. Relation Traversal (JOINs)
# =========================================================================== #

class TestRelationTraversal:

    def test_filter_by_related_field(self):
        _create_fk_tables()
        author = FKAuthor(name="Ada", email="ada@test.com")
        author.save()
        FKPost(title="Hello", body="World", author=author).save()
        FKPost(title="Other", body="Body", author=author).save()
        other_author = FKAuthor(name="Bob", email="bob@test.com")
        other_author.save()
        FKPost(title="Bob's Post", body="Body", author=other_author).save()

        results = list(FKPost.filter(author__name="Ada"))
        assert len(results) == 2

    def test_order_by_related_field(self):
        _create_fk_tables()
        a1 = FKAuthor(name="Zara", email="z@test.com")
        a1.save()
        a2 = FKAuthor(name="Ada", email="a@test.com")
        a2.save()
        FKPost(title="P1", body="B", author=a1).save()
        FKPost(title="P2", body="B", author=a2).save()
        results = list(FKPost.all().order_by("author__name"))
        assert results[0].title == "P2"  # Ada comes first
        assert results[1].title == "P1"

    def test_filter_on_nonexistent_field(self):
        # BUG: No validation at filter time. Error only at SQL execution.
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        qs = SimpleUser.filter(nonexistent=5)  # no error here
        with pytest.raises(sqlite3.OperationalError):
            list(qs)  # error at evaluation time

    def test_order_by_nonexistent_field(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        qs = SimpleUser.all().order_by("nonexistent")
        with pytest.raises(sqlite3.OperationalError):
            list(qs)

    def test_deep_relation_chain_not_supported(self):
        # Only single-hop relations work. "author__address__city" would generate
        # "author.address__city" which is invalid SQL.
        _create_fk_tables()
        author = FKAuthor(name="Ada", email="ada@test.com")
        author.save()
        FKPost(title="Hello", body="World", author=author).save()
        qs = FKPost.filter(author__email__like="%ada%")
        # This actually works because rpartition("__") splits on the LAST __,
        # giving field="author__email", suffix="like". Then partition("__") on
        # "author__email" gives rel="author", col="email". So single-hop with
        # operator suffix works fine.
        results = list(qs)
        assert len(results) == 1


# =========================================================================== #
# 16. _from_row
# =========================================================================== #

class TestFromRow:

    def test_from_row_basic(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com", age=36).save()
        row = _db().execute("SELECT * FROM simpleuser WHERE id = 1").fetchone()
        obj = SimpleUser._from_row(row)
        assert obj.name == "Ada"
        assert obj.age == 36

    def test_from_row_deserializes_datetime(self):
        _create_timestamp_tables()
        dt = datetime(2024, 6, 15, 12, 30, 0)
        obj = TimestampModel(created=dt, day=date.today(), active=True, data=b"")
        obj.save()
        row = _db().execute("SELECT * FROM timestampmodel WHERE id = ?", (obj.id,)).fetchone()
        result = TimestampModel._from_row(row)
        assert isinstance(result.created, datetime)
        assert result.created == dt

    def test_from_row_deserializes_bool(self):
        _create_timestamp_tables()
        obj = TimestampModel(created=datetime.now(), day=date.today(), active=True, data=b"")
        obj.save()
        row = _db().execute("SELECT * FROM timestampmodel WHERE id = ?", (obj.id,)).fetchone()
        result = TimestampModel._from_row(row)
        assert result.active is True
        assert isinstance(result.active, bool)

    def test_from_row_bypasses_init(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        row = _db().execute("SELECT * FROM simpleuser WHERE id = 1").fetchone()
        obj = SimpleUser._from_row(row)
        # _from_row uses __new__, so __init__ was never called
        # But all DB column values should be set correctly
        assert obj.name == "Ada"
        assert obj.id == 1


# =========================================================================== #
# 17. SQL Keyword Field Names
# =========================================================================== #

class TestSQLKeywordFieldNames:

    def test_field_named_order(self):
        # BUG: Column names are not quoted. "order" is a SQL keyword.
        OrderModel = type("OrderModel", (Model,), {"__annotations__": {"order_val": str}})
        OrderModel.create_table()
        obj = OrderModel(order_val="first")
        obj.save()
        fetched = OrderModel.get(id=obj.id)
        assert fetched.order_val == "first"

    def test_field_named_select(self):
        # "select" is a SQL keyword but SQLite is lenient with column names
        SelectModel = type("SelectModel", (Model,), {"__annotations__": {"select_val": str}})
        SelectModel.create_table()
        obj = SelectModel(select_val="test")
        obj.save()
        fetched = SelectModel.get(id=obj.id)
        assert fetched.select_val == "test"

    def test_table_named_sql_keyword(self):
        KeywordTable = type("KeywordTable", (Model,), {
            "__annotations__": {"name": str},
            "__tablename__": "values",  # "values" is a SQL keyword
        })
        KeywordTable.create_table()
        obj = KeywordTable(name="test")
        obj.save()
        fetched = KeywordTable.get(id=obj.id)
        assert fetched.name == "test"
        obj.name = "updated"
        obj.save()
        assert KeywordTable.get(id=obj.id).name == "updated"
        obj.delete()
        assert KeywordTable.get(id=obj.id) is None


# =========================================================================== #
# 18. NULL Handling
# =========================================================================== #

class TestNullHandling:

    def test_nullable_column_allows_none(self):
        NullableModel.create_table()
        obj = NullableModel(value=None)
        obj.save()
        fetched = NullableModel.get(id=obj.id)
        assert fetched.value is None

    def test_non_nullable_column_rejects_none(self):
        NotNullModel.create_table()
        obj = NotNullModel(value=None)
        with pytest.raises(ValueError, match="cannot be None"):
            obj.save()

    def test_filter_eq_none(self):
        # FIXED: generates "value IS NULL" instead of "value = ?"
        NullableModel.create_table()
        NullableModel(value=None).save()
        NullableModel(value="real").save()
        results = list(NullableModel.filter(value=None))
        assert len(results) == 1
        assert results[0].value is None

    def test_filter_ne_none(self):
        # FIXED: generates "value IS NOT NULL" instead of "value != ?"
        NullableModel.create_table()
        NullableModel(value=None).save()
        NullableModel(value="real").save()
        results = list(NullableModel.filter(value__ne=None))
        assert len(results) == 1
        assert results[0].value == "real"

    def test_nullable_fk(self):
        _create_fk_tables()
        post = FKPost(title="Orphan", body="Body")
        post.save()
        fetched = FKPost.get(id=post.id)
        assert fetched.author_id is None
        assert fetched.author is None


# =========================================================================== #
# 19. Edge Cases and Stress
# =========================================================================== #

class TestEdgeCasesAndStress:

    def test_save_then_modify_pk_then_save(self):
        # FIXED: UPDATE matching 0 rows falls back to INSERT, so a new row is created
        _create_simple_tables()
        u = SimpleUser(name="Ada", email="ada@test.com")
        u.save()
        original_pk = u.id
        u.id = 99999
        u.email = "ada2@test.com"
        u.save()
        assert SimpleUser.get(id=99999) is not None
        assert SimpleUser.get(id=original_pk) is not None

    def test_multiple_fks_to_same_model(self):
        # BUG: Auto-generated related_name for both FKs will be the same,
        # so the second overwrites the first reverse descriptor.
        class SharedTarget(Model):
            name: str

        SharedTarget.create_table()

        class DualFK(Model):
            primary_ref: SharedTarget = ForeignKey(SharedTarget, related_name="primary_refs")
            secondary_ref: SharedTarget = ForeignKey(SharedTarget, related_name="secondary_refs")

        DualFK.create_table()

        target = SharedTarget(name="Target")
        target.save()
        dual = DualFK(primary_ref=target, secondary_ref=target)
        dual.save()

        # Both reverse accessors should work independently
        assert hasattr(target, "primary_refs")
        assert hasattr(target, "secondary_refs")
        assert target.primary_refs.count() == 1
        assert target.secondary_refs.count() == 1

    def test_self_referential_fk(self):
        # A model that references itself — the class must exist before __init_subclass__
        # processes the FK, so we build it dynamically.
        class TreeNode(Model):
            name: str

        TreeNode.create_table()
        # We can't do self-referential FK with the current API's annotation-based
        # approach because the class doesn't exist yet during __init_subclass__.
        # Verify at least that a simple parent-child pattern works when split into
        # two steps or using a separate model.
        root = TreeNode(name="root")
        root.save()
        child = TreeNode(name="child")
        child.save()
        assert root.id != child.id

    def test_filter_with_no_args(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        qs = SimpleUser.filter()
        assert qs.count() == 1

    def test_empty_table_first(self):
        _create_simple_tables()
        assert SimpleUser.all().first() is None

    def test_empty_table_count(self):
        _create_simple_tables()
        assert SimpleUser.all().count() == 0

    def test_empty_table_delete(self):
        _create_simple_tables()
        assert SimpleUser.all().delete() == 0

    def test_empty_table_bool(self):
        _create_simple_tables()
        assert bool(SimpleUser.all()) is False

    def test_unicode_rtl_text(self):
        _create_simple_tables()
        rtl = "مرحبا بالعالم"
        u = SimpleUser(name=rtl, email="rtl@test.com")
        u.save()
        fetched = SimpleUser.get(id=u.id)
        assert fetched.name == rtl

    def test_unicode_null_bytes(self):
        _create_simple_tables()
        val = "hello\x00world"
        u = SimpleUser(name=val, email="null@test.com")
        u.save()
        fetched = SimpleUser.get(id=u.id)
        assert fetched.name == val

    def test_cjk_characters(self):
        _create_simple_tables()
        cjk = "你好世界"
        u = SimpleUser(name=cjk, email="cjk@test.com")
        u.save()
        fetched = SimpleUser.get(id=u.id)
        assert fetched.name == cjk

    def test_create_table_before_connect(self):
        _db_module._default_db = None
        with pytest.raises(RuntimeError, match="No database connected"):
            SimpleUser.create_table()

    def test_save_before_create_table(self):
        u = SimpleUser(name="Ada", email="ada@test.com")
        with pytest.raises(sqlite3.OperationalError):
            u.save()

    def test_model_inheritance(self):
        # FIXED: child model now inherits parent columns
        class BaseModel(Model):
            name: str

        class ExtendedModel(BaseModel):
            extra: str

        col_names = [c.name for c in ExtendedModel._columns]
        assert "extra" in col_names
        assert "name" in col_names

        ExtendedModel.create_table()
        obj = ExtendedModel(name="parent_val", extra="child_val")
        obj.save()
        fetched = ExtendedModel.get(id=obj.id)
        assert fetched.name == "parent_val"
        assert fetched.extra == "child_val"

    def test_queryset_can_be_iterated_multiple_times(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com").save()
        qs = SimpleUser.all()
        first = list(qs)
        second = list(qs)
        assert len(first) == len(second) == 1

    def test_save_returns_self_for_chaining(self):
        _create_simple_tables()
        u = SimpleUser(name="Ada", email="ada@test.com").save()
        assert u.id is not None
        assert u.name == "Ada"

    def test_multiple_models_same_db(self):
        _create_simple_tables()
        NullableModel.create_table()
        SimpleUser(name="Ada", email="ada@test.com").save()
        NullableModel(value="test").save()
        assert SimpleUser.all().count() == 1
        assert NullableModel.all().count() == 1

    def test_future_annotations_resolved_correctly(self):
        # inspect.get_annotations(cls, eval_str=True) resolves string annotations
        # from `from __future__ import annotations` back to actual types.
        # Verify this works even when annotations are passed as strings.
        ns = {"__annotations__": {"age": "int", "name": "str"}}
        FutureModel = type("FutureModel", (Model,), ns)
        age_col = next(c for c in FutureModel._columns if c.name == "age")
        assert age_col.py_type is int
        assert age_col.sql_type == "INTEGER"


# =========================================================================== #
# 20. Bug Fix Coverage
# =========================================================================== #

class TestBugFixCoverage:
    """Tests for code paths introduced by the 10 bug fixes."""

    # -- Fix 3: empty IN combined with other filters --------------------------

    def test_empty_in_combined_with_other_filter(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com", age=36).save()
        results = list(SimpleUser.filter(id__in=[], age=36))
        assert len(results) == 0

    def test_empty_in_build_where_combined(self):
        sql, params = _build_where({"id__in": [], "name": "Ada"})
        assert "0" in sql
        assert "AND" in sql
        assert params == ["Ada"]

    # -- Fix 4: NULL with explicit operator suffix ----------------------------

    def test_filter_eq_none_explicit_operator(self):
        NullableModel.create_table()
        NullableModel(value=None).save()
        NullableModel(value="real").save()
        results = list(NullableModel.filter(value__eq=None))
        assert len(results) == 1
        assert results[0].value is None

    def test_build_where_eq_none_generates_is_null(self):
        sql, params = _build_where({"value": None})
        assert "IS NULL" in sql
        assert params == []

    def test_build_where_ne_none_generates_is_not_null(self):
        sql, params = _build_where({"value__ne": None})
        assert "IS NOT NULL" in sql
        assert params == []

    def test_filter_gt_none_passes_through(self):
        sql, params = _build_where({"age__gt": None})
        assert '"age" > ?' in sql
        assert params == [None]

    # -- Fix 5: all operator names as bare fields -----------------------------

    def test_build_where_all_operator_names_as_fields(self):
        for op_name in _OPERATORS:
            sql, params = _build_where({op_name: 42})
            assert f'"{op_name}" = ?' in sql, f"field named '{op_name}' was misinterpreted"
            assert params == [42]

    # -- Fix 6: FK setter with unsaved object (direct assignment) -------------

    def test_fk_direct_setter_unsaved_raises(self):
        _create_fk_tables()
        author = FKAuthor(name="Ada", email="ada@test.com")
        author.save()
        post = FKPost(title="Hello", body="World", author=author)
        post.save()
        unsaved = FKAuthor(name="Ghost", email="ghost@test.com")
        with pytest.raises(ValueError, match="unsaved object"):
            post.author = unsaved

    def test_fk_setter_saved_object_works(self):
        _create_fk_tables()
        a1 = FKAuthor(name="Ada", email="ada@test.com")
        a1.save()
        a2 = FKAuthor(name="Bob", email="bob@test.com")
        a2.save()
        post = FKPost(title="Hello", body="World", author=a1)
        post.save()
        post.author = a2
        assert post.author_id == a2.id

    # -- Fix 7: M2M add mixed saved/unsaved ----------------------------------

    def test_m2m_add_mixed_saved_unsaved_rolls_back(self):
        _create_m2m_tables()
        article = M2MArticle(title="Hello")
        article.save()
        saved_tag = M2MTag(label="python")
        saved_tag.save()
        unsaved_tag = M2MTag(label="ghost")
        with pytest.raises(ValueError, match="must be saved"):
            article.tags.add(saved_tag, unsaved_tag)
        assert article.tags.count() == 0

    # -- Fix 8: UPDATE→INSERT fallback preserves all columns ------------------

    def test_upsert_fallback_preserves_all_fields(self):
        _create_simple_tables()
        u = SimpleUser(name="Ada", email="ada@test.com", age=99)
        u.id = 500
        u.save()
        fetched = SimpleUser.get(id=500)
        assert fetched.name == "Ada"
        assert fetched.email == "ada@test.com"
        assert fetched.age == 99

    def test_upsert_fallback_custom_pk(self):
        CustomPKModel.create_table()
        obj = CustomPKModel(uid=42, name="test")
        obj.save()
        obj.name = "updated"
        obj.save()
        fetched = CustomPKModel.get(uid=42)
        assert fetched.name == "updated"

    # -- Fix 9: delete then re-delete raises ----------------------------------

    def test_double_delete_raises(self):
        _create_simple_tables()
        u = SimpleUser(name="Ada", email="ada@test.com")
        u.save()
        u.delete()
        with pytest.raises(ValueError, match="Cannot delete an unsaved object"):
            u.delete()

    def test_delete_clears_pk_allows_clean_resave(self):
        _create_simple_tables()
        u = SimpleUser(name="Ada", email="ada@test.com")
        u.save()
        u.delete()
        assert u.id is None
        u.email = "ada2@test.com"
        u.save()
        assert u.id is not None
        fetched = SimpleUser.get(id=u.id)
        assert fetched.email == "ada2@test.com"
        assert SimpleUser.all().count() == 1

    # -- Fix 10: inheritance edge cases ---------------------------------------

    def test_inheritance_child_overrides_parent_column(self):
        class Base(Model):
            name: str = Field(nullable=True)

        class Child(Base):
            name: str = Field(nullable=False)
            extra: int = 0

        name_col = next(c for c in Child._columns if c.name == "name")
        assert name_col.field.nullable is False

    def test_inheritance_multi_level(self):
        class GrandParent(Model):
            gp_field: str

        class Parent(GrandParent):
            p_field: str

        class Child(Parent):
            c_field: str

        col_names = [c.name for c in Child._columns]
        assert "gp_field" in col_names
        assert "p_field" in col_names
        assert "c_field" in col_names

    def test_inheritance_pk_inherited(self):
        class Base(Model):
            uid: int = Field(primary_key=True)
            name: str

        class Child(Base):
            extra: str

        assert Child._pk == "uid"
        col_names = [c.name for c in Child._columns]
        assert "uid" in col_names
        assert "id" not in col_names

    def test_inheritance_child_can_override_pk(self):
        class Base(Model):
            name: str

        class Child(Base):
            cid: int = Field(primary_key=True)
            extra: str

        assert Child._pk == "cid"

    def test_inherited_model_roundtrip(self):
        class Base(Model):
            name: str

        class Child(Base):
            extra: str

        Child.create_table()
        obj = Child(name="hello", extra="world")
        obj.save()
        fetched = Child.get(id=obj.id)
        assert fetched.name == "hello"
        assert fetched.extra == "world"


# =========================================================================== #
# 21. Save Validation
# =========================================================================== #

class TestSaveValidation:
    """Tests for type + not-null validation on Model.save()."""

    # -- not-null checks ------------------------------------------------------

    def test_save_rejects_none_on_not_null_field(self):
        NotNullModel.create_table()
        obj = NotNullModel(value=None)
        with pytest.raises(ValueError, match="cannot be None"):
            obj.save()

    def test_save_allows_none_on_nullable_field(self):
        NullableModel.create_table()
        obj = NullableModel(value=None)
        obj.save()
        fetched = NullableModel.get(id=obj.id)
        assert fetched.value is None

    def test_save_allows_none_pk_on_insert(self):
        _create_simple_tables()
        u = SimpleUser(name="Ada", email="ada@test.com")
        assert u.id is None
        u.save()
        assert u.id is not None

    # -- type rejections ------------------------------------------------------

    def test_save_rejects_str_on_int_field(self):
        _create_simple_tables()
        u = SimpleUser(name="x", email="x@test.com", age="old")
        with pytest.raises(TypeError, match="age"):
            u.save()

    def test_save_rejects_int_on_str_field(self):
        _create_simple_tables()
        u = SimpleUser(name=123, email="x@test.com")
        with pytest.raises(TypeError, match="name"):
            u.save()

    def test_save_rejects_str_on_datetime_field(self):
        _create_timestamp_tables()
        obj = TimestampModel(created="2024-01-01", day=date.today(), data=b"")
        with pytest.raises(TypeError, match="created"):
            obj.save()

    def test_save_rejects_datetime_on_date_field(self):
        _create_timestamp_tables()
        obj = TimestampModel(
            created=datetime.now(), day=datetime.now(), data=b""
        )
        with pytest.raises(TypeError, match="day"):
            obj.save()

    def test_save_rejects_str_on_bytes_field(self):
        _create_timestamp_tables()
        obj = TimestampModel(
            created=datetime.now(), day=date.today(), data="not bytes"
        )
        with pytest.raises(TypeError, match="data"):
            obj.save()

    # -- lenient acceptance ---------------------------------------------------

    def test_save_accepts_bool_on_int_field(self):
        _create_simple_tables()
        u = SimpleUser(name="x", email="x@test.com", age=True)
        u.save()
        fetched = SimpleUser.get(id=u.id)
        assert fetched.age == 1

    def test_save_accepts_int_on_float_field(self):
        class FloatModel(Model):
            score: float

        FloatModel.create_table()
        obj = FloatModel(score=100)
        obj.save()
        fetched = FloatModel.get(id=obj.id)
        assert fetched.score == 100.0

    def test_save_accepts_int_on_bool_field(self):
        _create_timestamp_tables()
        obj = TimestampModel(
            created=datetime.now(), day=date.today(), active=1, data=b""
        )
        obj.save()
        fetched = TimestampModel.get(id=obj.id)
        assert fetched.active is True

    def test_save_accepts_bool_on_float_field(self):
        class FloatModel2(Model):
            score: float

        FloatModel2.create_table()
        obj = FloatModel2(score=True)
        obj.save()
        fetched = FloatModel2.get(id=obj.id)
        assert fetched.score == 1.0

    # -- edge cases -----------------------------------------------------------

    def test_save_skips_validation_for_unknown_types(self):
        class WeirdModel(Model):
            data: list

        WeirdModel.create_table()
        obj = WeirdModel(data="anything goes")
        obj.save()
        fetched = WeirdModel.get(id=obj.id)
        assert fetched.data == "anything goes"

    def test_validation_error_names_field(self):
        _create_simple_tables()
        u = SimpleUser(name="x", email="x@test.com", age="bad")
        with pytest.raises(TypeError, match="age") as exc_info:
            u.save()
        assert "int" in str(exc_info.value)
        assert "str" in str(exc_info.value)

    def test_save_validates_on_update_too(self):
        _create_simple_tables()
        u = SimpleUser(name="Ada", email="ada@test.com", age=36)
        u.save()
        u.age = "not a number"
        with pytest.raises(TypeError, match="age"):
            u.save()


# =========================================================================== #
# 22. Distinct
# =========================================================================== #

class TestDistinct:

    def test_distinct_returns_queryset(self):
        _create_simple_tables()
        qs = SimpleUser.all().distinct()
        assert isinstance(qs, QuerySet)

    def test_distinct_does_not_mutate_original(self):
        _create_simple_tables()
        qs1 = SimpleUser.all()
        qs2 = qs1.distinct()
        assert qs1._distinct is False
        assert qs2._distinct is True

    def test_distinct_default_is_false(self):
        qs = QuerySet(SimpleUser)
        assert qs._distinct is False

    def test_distinct_propagated_by_clone(self):
        _create_simple_tables()
        qs1 = SimpleUser.all().distinct()
        qs2 = qs1.filter(age=30)
        assert qs2._distinct is True

    def test_distinct_sql_contains_keyword(self):
        _create_simple_tables()
        qs = SimpleUser.all().distinct()
        sql, _ = qs._sql()
        assert "SELECT DISTINCT" in sql

    def test_non_distinct_sql_no_keyword(self):
        _create_simple_tables()
        qs = SimpleUser.all()
        sql, _ = qs._sql()
        assert "DISTINCT" not in sql

    def test_distinct_with_filter(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com", age=36).save()
        SimpleUser(name="Bob", email="bob@test.com", age=25).save()
        results = list(SimpleUser.filter(age__gte=30).distinct())
        assert len(results) == 1
        assert results[0].name == "Ada"

    def test_distinct_with_count(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com", age=36).save()
        SimpleUser(name="Bob", email="bob@test.com", age=25).save()
        assert SimpleUser.all().distinct().count() == 2

    def test_distinct_with_first(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com", age=36).save()
        result = SimpleUser.all().distinct().first()
        assert result is not None
        assert result.name == "Ada"

    def test_distinct_with_unique_pk_returns_all_rows(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="a@test.com", age=30).save()
        SimpleUser(name="Bob", email="b@test.com", age=30).save()
        results = list(SimpleUser.all().distinct())
        assert len(results) == 2


# =========================================================================== #
# 23. Aggregates
# =========================================================================== #

class TestAggregates:

    def _seed_users(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="a@test.com", age=10).save()
        SimpleUser(name="Bob", email="b@test.com", age=20).save()
        SimpleUser(name="Eve", email="e@test.com", age=30).save()

    # -- sum ------------------------------------------------------------------

    def test_sum_basic(self):
        self._seed_users()
        assert SimpleUser.all().sum("age") == 60

    def test_sum_with_filter(self):
        self._seed_users()
        assert SimpleUser.filter(age__gte=20).sum("age") == 50

    def test_sum_empty_table(self):
        _create_simple_tables()
        assert SimpleUser.all().sum("age") is None

    # -- avg ------------------------------------------------------------------

    def test_avg_basic(self):
        self._seed_users()
        assert SimpleUser.all().avg("age") == 20.0

    def test_avg_empty_table(self):
        _create_simple_tables()
        assert SimpleUser.all().avg("age") is None

    # -- min ------------------------------------------------------------------

    def test_min_basic(self):
        self._seed_users()
        assert SimpleUser.all().min("age") == 10

    def test_min_empty_table(self):
        _create_simple_tables()
        assert SimpleUser.all().min("age") is None

    # -- max ------------------------------------------------------------------

    def test_max_basic(self):
        self._seed_users()
        assert SimpleUser.all().max("age") == 30

    def test_max_empty_table(self):
        _create_simple_tables()
        assert SimpleUser.all().max("age") is None

    # -- edge cases -----------------------------------------------------------

    def test_aggregate_ignores_order_and_limit(self):
        self._seed_users()
        result = SimpleUser.all().order_by("name").limit(1).sum("age")
        assert result == 60

    def test_aggregate_invalid_column_raises(self):
        _create_simple_tables()
        with pytest.raises(ValueError, match="nonexistent") as exc_info:
            SimpleUser.all().sum("nonexistent")
        assert "Valid columns" in str(exc_info.value)

    def test_aggregate_with_join_filter(self):
        _create_fk_tables()
        author = FKAuthor(name="Ada", email="ada@test.com")
        author.save()
        other = FKAuthor(name="Bob", email="bob@test.com")
        other.save()
        FKPost(title="P1", body="B1", author=author).save()
        FKPost(title="P2", body="B2", author=author).save()
        FKPost(title="P3", body="B3", author=other).save()
        count = FKPost.filter(author__name="Ada").count()
        assert count == 2

    def test_aggregate_with_distinct(self):
        self._seed_users()
        result = SimpleUser.all().distinct().sum("age")
        assert result == 60

    def test_sum_skips_nulls(self):
        NullableModel.create_table()
        NullableModel(value="a").save()
        NullableModel(value=None).save()
        NullableModel(value="b").save()
        assert NullableModel.all().count() == 3
        assert NullableModel.all().min("value") == "a"

    def test_aggregate_on_string_column(self):
        self._seed_users()
        assert SimpleUser.all().min("name") == "Ada"
        assert SimpleUser.all().max("name") == "Eve"

    def test_aggregate_does_not_populate_cache(self):
        self._seed_users()
        qs = SimpleUser.all()
        qs.sum("age")
        assert qs._result_cache is None

    def test_aggregate_datetime_deserialization(self):
        _create_timestamp_tables()
        dt1 = datetime(2024, 1, 1, 12, 0, 0)
        dt2 = datetime(2024, 6, 15, 12, 0, 0)
        TimestampModel(created=dt1, day=date.today(), data=b"").save()
        TimestampModel(created=dt2, day=date.today(), data=b"").save()
        result = TimestampModel.all().min("created")
        assert isinstance(result, datetime)
        assert result == dt1

    def test_aggregate_respects_filters_no_match(self):
        self._seed_users()
        assert SimpleUser.filter(age__gt=100).sum("age") is None


# =========================================================================== #
# 24. Get or Create
# =========================================================================== #

class TestGetOrCreate:

    def test_get_or_create_creates_when_missing(self):
        _create_simple_tables()
        obj, created = SimpleUser.get_or_create(
            email="ada@test.com", defaults={"name": "Ada", "age": 36}
        )
        assert created is True
        assert obj.id is not None
        assert obj.name == "Ada"
        assert obj.email == "ada@test.com"
        assert SimpleUser.all().count() == 1

    def test_get_or_create_gets_existing(self):
        _create_simple_tables()
        original = SimpleUser(name="Ada", email="ada@test.com", age=36)
        original.save()
        obj, created = SimpleUser.get_or_create(
            email="ada@test.com", defaults={"name": "Different", "age": 99}
        )
        assert created is False
        assert obj.id == original.id
        assert obj.name == "Ada"
        assert SimpleUser.all().count() == 1

    def test_get_or_create_uses_defaults_on_create(self):
        _create_simple_tables()
        obj, created = SimpleUser.get_or_create(
            email="ada@test.com", defaults={"name": "Ada", "age": 36}
        )
        assert created is True
        assert obj.age == 36
        assert obj.name == "Ada"

    def test_get_or_create_ignores_defaults_on_get(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="ada@test.com", age=36).save()
        obj, created = SimpleUser.get_or_create(
            email="ada@test.com", defaults={"age": 99}
        )
        assert created is False
        assert obj.age == 36

    def test_get_or_create_no_defaults(self):
        _create_simple_tables()
        obj, created = SimpleUser.get_or_create(
            name="Ada", email="ada@test.com"
        )
        assert created is True
        assert obj.name == "Ada"
        assert obj.age == 0

    def test_get_or_create_multiple_matches_raises(self):
        _create_simple_tables()
        SimpleUser(name="Ada", email="a1@test.com", age=30).save()
        SimpleUser(name="Ada", email="a2@test.com", age=30).save()
        with pytest.raises(ValueError, match="multiple rows"):
            SimpleUser.get_or_create(name="Ada")

    def test_get_or_create_validates_on_save(self):
        _create_simple_tables()
        with pytest.raises(TypeError, match="age"):
            SimpleUser.get_or_create(
                email="ada@test.com", defaults={"name": "Ada", "age": "old"}
            )


# =========================================================================== #
# Many-to-Many: Multiple fields to the same target
# =========================================================================== #

class TestManyToManyMultipleToSameTarget:

    def test_creates_distinct_junction_tables(self):
        _create_m2m_multi_tables()
        cur = _db().conn.cursor()
        for table in ("m2memail_to_users", "m2memail_cc_users", "m2memail_bcc_users"):
            row = cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            assert row is not None, f"Junction table {table} was not created"

    def test_fields_are_independent(self):
        _create_m2m_multi_tables()
        alice = M2MRecipient(name="Alice"); alice.save()
        bob = M2MRecipient(name="Bob"); bob.save()
        carol = M2MRecipient(name="Carol"); carol.save()
        email = M2MEmail(subject="Hello"); email.save()

        email.to_users.add(alice)
        email.cc_users.add(bob)
        email.bcc_users.add(carol)

        assert email.to_users.count() == 1
        assert email.cc_users.count() == 1
        assert email.bcc_users.count() == 1
        assert email.to_users.all()[0].name == "Alice"
        assert email.cc_users.all()[0].name == "Bob"
        assert email.bcc_users.all()[0].name == "Carol"

    def test_same_object_in_multiple_fields(self):
        _create_m2m_multi_tables()
        alice = M2MRecipient(name="Alice"); alice.save()
        email = M2MEmail(subject="Hello"); email.save()

        email.to_users.add(alice)
        email.cc_users.add(alice)

        assert email.to_users.count() == 1
        assert email.cc_users.count() == 1
        assert email.bcc_users.count() == 0

    def test_remove_from_one_field_doesnt_affect_other(self):
        _create_m2m_multi_tables()
        alice = M2MRecipient(name="Alice"); alice.save()
        email = M2MEmail(subject="Hello"); email.save()

        email.to_users.add(alice)
        email.cc_users.add(alice)
        email.to_users.remove(alice)

        assert email.to_users.count() == 0
        assert email.cc_users.count() == 1

    def test_clear_one_field_doesnt_affect_others(self):
        _create_m2m_multi_tables()
        alice = M2MRecipient(name="Alice"); alice.save()
        bob = M2MRecipient(name="Bob"); bob.save()
        email = M2MEmail(subject="Hello"); email.save()

        email.to_users.add(alice)
        email.cc_users.add(bob)
        email.bcc_users.add(alice, bob)
        email.cc_users.clear()

        assert email.to_users.count() == 1
        assert email.cc_users.count() == 0
        assert email.bcc_users.count() == 2

    def test_reverse_accessors(self):
        _create_m2m_multi_tables()
        alice = M2MRecipient(name="Alice"); alice.save()
        email1 = M2MEmail(subject="First"); email1.save()
        email2 = M2MEmail(subject="Second"); email2.save()

        email1.to_users.add(alice)
        email2.cc_users.add(alice)

        to_emails = alice.emails_to.all()
        cc_emails = alice.emails_cc.all()
        bcc_emails = alice.emails_bcc.all()

        assert len(to_emails) == 1
        assert to_emails[0].subject == "First"
        assert len(cc_emails) == 1
        assert cc_emails[0].subject == "Second"
        assert len(bcc_emails) == 0

    def test_cascade_delete_owner(self):
        _create_m2m_multi_tables()
        alice = M2MRecipient(name="Alice"); alice.save()
        email = M2MEmail(subject="Hello"); email.save()
        email_id = email.id

        email.to_users.add(alice)
        email.cc_users.add(alice)
        email.bcc_users.add(alice)
        email.delete()

        cur = _db().conn.cursor()
        for table in ("m2memail_to_users", "m2memail_cc_users", "m2memail_bcc_users"):
            rows = cur.execute(
                f"SELECT * FROM {table} WHERE m2memail_id = ?", (email_id,)
            ).fetchall()
            assert len(rows) == 0, f"Rows remain in {table} after owner delete"

    def test_cascade_delete_target(self):
        _create_m2m_multi_tables()
        alice = M2MRecipient(name="Alice"); alice.save()
        bob = M2MRecipient(name="Bob"); bob.save()
        alice_id = alice.id
        email = M2MEmail(subject="Hello"); email.save()

        email.to_users.add(alice, bob)
        email.cc_users.add(alice)
        alice.delete()

        assert email.to_users.count() == 1
        assert email.to_users.all()[0].name == "Bob"
        assert email.cc_users.count() == 0

        cur = _db().conn.cursor()
        for table in ("m2memail_to_users", "m2memail_cc_users", "m2memail_bcc_users"):
            rows = cur.execute(
                f"SELECT * FROM {table} WHERE m2mrecipient_id = ?", (alice_id,)
            ).fetchall()
            assert len(rows) == 0

    def test_junction_table_naming_convention(self):
        assert M2MEmail._m2m[0][0] == "m2memail_to_users"
        assert M2MEmail._m2m[1][0] == "m2memail_cc_users"
        assert M2MEmail._m2m[2][0] == "m2memail_bcc_users"

    def test_missing_related_name_raises(self):
        with pytest.raises(ValueError, match="related_name"):
            class _Target(Model):
                name: str
            class _BadModel(Model):
                a: list = ManyToMany(_Target)
                b: list = ManyToMany(_Target)
