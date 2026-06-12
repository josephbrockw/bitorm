"""Tests for WAL mode and fork-safe connection handling."""

import os

import pytest

import bitorm.db as _db_module
from bitorm import Model, connect


class ConcUser(Model):
    name: str


@pytest.fixture(autouse=True)
def cleanup():
    yield
    _db_module._default_db = None


class TestWal:

    def test_file_db_uses_wal(self, tmp_path):
        db = connect(str(tmp_path / "w.db"))
        assert db.raw("PRAGMA journal_mode")[0][0] == "wal"

    def test_file_db_busy_timeout(self, tmp_path):
        db = connect(str(tmp_path / "w.db"))
        assert db.raw("PRAGMA busy_timeout")[0][0] == 5000

    def test_memory_db_skips_wal(self):
        db = connect(":memory:")
        assert db.raw("PRAGMA journal_mode")[0][0] == "memory"

    def test_second_connection_reads_during_write_tx(self, tmp_path):
        """WAL allows another connection to read while a write tx is open."""
        path = str(tmp_path / "w.db")
        db = connect(path)
        ConcUser.create_table()
        ConcUser(name="Ada").save()
        with db.atomic():
            ConcUser(name="Bob").save()  # uncommitted
            other = _db_module.Database(path)
            names = [r["name"] for r in other.raw('SELECT name FROM "concuser"')]
            other.close()
        assert names == ["Ada"]  # reader saw the committed snapshot only


class TestForkSafety:

    def test_reconnects_when_pid_changes(self, tmp_path, monkeypatch):
        db = connect(str(tmp_path / "f.db"))
        ConcUser.create_table()
        ConcUser(name="Ada").save()
        old_conn = db.conn

        fake_pid = os.getpid() + 99999
        monkeypatch.setattr(_db_module.os, "getpid", lambda: fake_pid)

        loaded = ConcUser.get(name="Ada")  # triggers lazy reconnect
        assert loaded is not None and loaded.name == "Ada"
        assert db.conn is not old_conn
        assert db._pid == fake_pid

    def test_atomic_depth_resets_on_reconnect(self, tmp_path, monkeypatch):
        db = connect(str(tmp_path / "f.db"))
        ConcUser.create_table()
        db._atomic_depth = 3  # simulate a transaction open at fork time
        fake_pid = os.getpid() + 99999
        monkeypatch.setattr(_db_module.os, "getpid", lambda: fake_pid)
        db.execute("SELECT 1")
        assert db._atomic_depth == 0

    def test_no_reconnect_in_same_process(self, tmp_path):
        db = connect(str(tmp_path / "f.db"))
        conn = db.conn
        db.execute("SELECT 1")
        db.execute("SELECT 1")
        assert db.conn is conn
