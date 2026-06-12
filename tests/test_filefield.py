"""Tests for FileField, FileRef, and _FileDescriptor."""

import json
import os

import pytest

from bitorm.models import Model, Field, FileField, FileRef, _FileDescriptor
from bitorm.db import connect
import bitorm.db as _db_module


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def file_db(tmp_path, monkeypatch):
    """File-backed database in a temp directory."""
    monkeypatch.chdir(tmp_path)
    db_path = str(tmp_path / "test.db")
    database = connect(db_path)
    yield database, tmp_path
    _db_module._default_db = None


# --------------------------------------------------------------------------- #
# Test models
# --------------------------------------------------------------------------- #

class DocModel(Model):
    __tablename__ = "ff_doc"
    name: str
    content: str = FileField(base_dir="docs")


class ImageModel(Model):
    __tablename__ = "ff_image"
    label: str
    image: str = FileField(base_dir="images")


class NoBaseDirModel(Model):
    __tablename__ = "ff_nobase"
    name: str
    data: str = FileField()


class NullableFileModel(Model):
    __tablename__ = "ff_nullable"
    name: str
    attachment: str = FileField(nullable=True)


class RequiredFileModel(Model):
    __tablename__ = "ff_required"
    name: str
    path: str = FileField(nullable=False)


# =========================================================================== #
# FileRef
# =========================================================================== #

class TestFileRef:

    def test_path_resolution(self, file_db, tmp_path):
        from pathlib import Path
        ref = FileRef("subdir/file.txt", Path(tmp_path / "docs"))
        assert ref.path == tmp_path / "docs" / "subdir" / "file.txt"

    def test_relative(self, file_db):
        from pathlib import Path
        ref = FileRef("hello.txt", Path("/base"))
        assert ref.relative == "hello.txt"

    def test_read_text(self, file_db, tmp_path):
        from pathlib import Path
        base = tmp_path / "docs"
        base.mkdir()
        (base / "note.txt").write_text("hello world")
        ref = FileRef("note.txt", base)
        assert ref.read_text() == "hello world"

    def test_read_bytes(self, file_db, tmp_path):
        from pathlib import Path
        base = tmp_path / "data"
        base.mkdir()
        (base / "blob.bin").write_bytes(b"\x00\x01\x02")
        ref = FileRef("blob.bin", base)
        assert ref.read_bytes() == b"\x00\x01\x02"

    def test_read_json(self, file_db, tmp_path):
        from pathlib import Path
        base = tmp_path / "data"
        base.mkdir()
        (base / "config.json").write_text('{"key": "value"}')
        ref = FileRef("config.json", base)
        assert ref.read_json() == {"key": "value"}

    def test_exists(self, file_db, tmp_path):
        from pathlib import Path
        base = tmp_path / "data"
        base.mkdir()
        ref_missing = FileRef("nope.txt", base)
        assert ref_missing.exists is False
        (base / "yes.txt").write_text("here")
        ref_found = FileRef("yes.txt", base)
        assert ref_found.exists is True

    def test_size(self, file_db, tmp_path):
        from pathlib import Path
        base = tmp_path / "data"
        base.mkdir()
        (base / "sized.txt").write_text("12345")
        ref = FileRef("sized.txt", base)
        assert ref.size == 5

    def test_ext(self, file_db):
        from pathlib import Path
        ref = FileRef("report.pdf", Path("/base"))
        assert ref.ext == ".pdf"

    def test_stem(self, file_db):
        from pathlib import Path
        ref = FileRef("report.pdf", Path("/base"))
        assert ref.stem == "report"

    def test_hash(self, file_db, tmp_path):
        from pathlib import Path
        import hashlib
        base = tmp_path / "data"
        base.mkdir()
        content = b"hash me"
        (base / "h.txt").write_bytes(content)
        ref = FileRef("h.txt", base)
        expected = hashlib.sha256(content).hexdigest()
        assert ref.hash == expected

    def test_hash_cached(self, file_db, tmp_path):
        from pathlib import Path
        base = tmp_path / "data"
        base.mkdir()
        (base / "c.txt").write_bytes(b"cached")
        ref = FileRef("c.txt", base)
        h1 = ref.hash
        h2 = ref.hash
        assert h1 == h2
        assert ref._hash_cache is not None

    def test_str(self, file_db):
        from pathlib import Path
        ref = FileRef("my/file.txt", Path("/base"))
        assert str(ref) == "my/file.txt"

    def test_repr(self, file_db):
        from pathlib import Path
        ref = FileRef("my/file.txt", Path("/base"))
        assert repr(ref) == "FileRef('my/file.txt')"

    def test_eq(self, file_db):
        from pathlib import Path
        a = FileRef("a.txt", Path("/base"))
        b = FileRef("a.txt", Path("/base"))
        c = FileRef("c.txt", Path("/base"))
        assert a == b
        assert a != c

    def test_eq_different_base(self, file_db):
        from pathlib import Path
        a = FileRef("a.txt", Path("/base1"))
        b = FileRef("a.txt", Path("/base2"))
        assert a != b


# =========================================================================== #
# FileField on Models
# =========================================================================== #

class TestFileFieldModel:

    def test_creates_text_column(self, file_db):
        db, tmp_path = file_db
        DocModel.create_table()
        cols = db.execute('PRAGMA table_info("ff_doc")').fetchall()
        col_map = {c["name"]: c["type"] for c in cols}
        assert "content" in col_map
        assert col_map["content"] == "TEXT"

    def test_save_and_retrieve(self, file_db):
        db, tmp_path = file_db
        DocModel.create_table()
        doc = DocModel(name="readme", content="notes/readme.md")
        doc.save()
        fetched = DocModel.get(id=doc.id)
        assert isinstance(fetched.content, FileRef)
        assert fetched.content.relative == "notes/readme.md"

    def test_path_resolves_to_base_dir(self, file_db):
        db, tmp_path = file_db
        DocModel.create_table()
        doc = DocModel(name="readme", content="readme.md")
        doc.save()
        fetched = DocModel.get(id=doc.id)
        expected = tmp_path / "docs" / "readme.md"
        assert fetched.content.path == expected

    def test_read_text_through_model(self, file_db):
        db, tmp_path = file_db
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "hello.txt").write_text("file content here")
        DocModel.create_table()
        doc = DocModel(name="hello", content="hello.txt")
        doc.save()
        fetched = DocModel.get(id=doc.id)
        assert fetched.content.read_text() == "file content here"

    def test_nullable_returns_none(self, file_db):
        db, tmp_path = file_db
        NullableFileModel.create_table()
        obj = NullableFileModel(name="no file")
        obj.save()
        fetched = NullableFileModel.get(id=obj.id)
        assert fetched.attachment is None

    def test_set_via_string(self, file_db):
        db, tmp_path = file_db
        DocModel.create_table()
        doc = DocModel(name="test")
        doc.content = "path/to/file.txt"
        doc.save()
        fetched = DocModel.get(id=doc.id)
        assert fetched.content.relative == "path/to/file.txt"

    def test_set_via_fileref(self, file_db):
        from pathlib import Path
        db, tmp_path = file_db
        DocModel.create_table()
        doc = DocModel(name="test")
        ref = FileRef("via_ref.txt", Path("/ignored"))
        doc.content = ref
        doc.save()
        fetched = DocModel.get(id=doc.id)
        assert fetched.content.relative == "via_ref.txt"

    def test_set_to_none(self, file_db):
        db, tmp_path = file_db
        NullableFileModel.create_table()
        obj = NullableFileModel(name="had file", attachment="old.txt")
        obj.save()
        obj.attachment = None
        obj.save()
        fetched = NullableFileModel.get(id=obj.id)
        assert fetched.attachment is None

    def test_no_base_dir_resolves_to_db_dir(self, file_db):
        db, tmp_path = file_db
        NoBaseDirModel.create_table()
        (tmp_path / "direct.txt").write_text("direct content")
        obj = NoBaseDirModel(name="direct", data="direct.txt")
        obj.save()
        fetched = NoBaseDirModel.get(id=obj.id)
        assert fetched.data.path == tmp_path / "direct.txt"
        assert fetched.data.read_text() == "direct content"

    def test_filter_by_relative_path(self, file_db):
        db, tmp_path = file_db
        DocModel.create_table()
        DocModel(name="a", content="path/a.txt").save()
        DocModel(name="b", content="path/b.txt").save()
        results = DocModel.filter(content="path/a.txt")
        assert results.count() == 1

    def test_file_exists_check(self, file_db):
        db, tmp_path = file_db
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "real.txt").write_text("exists")
        DocModel.create_table()
        DocModel(name="real", content="real.txt").save()
        DocModel(name="ghost", content="ghost.txt").save()
        all_docs = list(DocModel.all())
        existing = [d for d in all_docs if d.content.exists]
        missing = [d for d in all_docs if not d.content.exists]
        assert len(existing) == 1
        assert len(missing) == 1

    def test_hash_for_dedup(self, file_db):
        db, tmp_path = file_db
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "a.txt").write_text("same content")
        (docs_dir / "b.txt").write_text("same content")
        (docs_dir / "c.txt").write_text("different")
        DocModel.create_table()
        DocModel(name="a", content="a.txt").save()
        DocModel(name="b", content="b.txt").save()
        DocModel(name="c", content="c.txt").save()
        docs = list(DocModel.all())
        hashes = [d.content.hash for d in docs]
        assert hashes[0] == hashes[1]
        assert hashes[0] != hashes[2]

    def test_descriptor_on_class_returns_descriptor(self, file_db):
        assert isinstance(DocModel.content, _FileDescriptor)

    def test_init_with_filefield_kwarg(self, file_db):
        db, tmp_path = file_db
        DocModel.create_table()
        doc = DocModel(name="init", content="init.txt")
        assert doc.__dict__["content"] == "init.txt"


# =========================================================================== #
# Edge Cases
# =========================================================================== #

class TestFileFieldEdgeCases:

    def test_subdirectory_paths(self, file_db):
        db, tmp_path = file_db
        docs_dir = tmp_path / "docs" / "deep" / "nested"
        docs_dir.mkdir(parents=True)
        (docs_dir / "file.txt").write_text("nested content")
        DocModel.create_table()
        doc = DocModel(name="nested", content="deep/nested/file.txt")
        doc.save()
        fetched = DocModel.get(id=doc.id)
        assert fetched.content.read_text() == "nested content"

    def test_multiple_filefield_models(self, file_db):
        db, tmp_path = file_db
        (tmp_path / "docs").mkdir()
        (tmp_path / "images").mkdir()
        (tmp_path / "docs" / "d.txt").write_text("doc")
        (tmp_path / "images" / "i.png").write_bytes(b"\x89PNG")
        DocModel.create_table()
        ImageModel.create_table()
        DocModel(name="d", content="d.txt").save()
        ImageModel(label="i", image="i.png").save()
        d = DocModel.get(name="d")
        i = ImageModel.get(label="i")
        assert d.content.read_text() == "doc"
        assert i.image.read_bytes() == b"\x89PNG"
        assert d.content.path.parent.name == "docs"
        assert i.image.path.parent.name == "images"

    def test_read_json_nested(self, file_db):
        db, tmp_path = file_db
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        data = {"emails": [{"from": "ada@x.com", "label": "important"}]}
        (docs_dir / "data.json").write_text(json.dumps(data))
        DocModel.create_table()
        DocModel(name="json", content="data.json").save()
        fetched = DocModel.get(name="json")
        assert fetched.content.read_json() == data
