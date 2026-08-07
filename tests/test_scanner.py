from pathlib import Path

from app.scanner import discover_packages, sha256_file


def test_sha256_is_stable(tmp_path: Path):
    path = tmp_path / "sample.zip"
    path.write_bytes(b"markorbit")
    assert sha256_file(path) == sha256_file(path)


def test_discover_supported_packages(tmp_path: Path):
    (tmp_path / "a.zip").write_bytes(b"a")
    (tmp_path / "b.xml").write_bytes(b"b")
    (tmp_path / "ignore.txt").write_text("x", encoding="utf-8")
    found = list(discover_packages(tmp_path, jurisdiction="CN"))
    assert [item.file_name for item in found] == ["a.zip", "b.xml"]
