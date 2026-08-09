from pathlib import Path
import zipfile

from app.us.ingest import _iter_package_bundles


FIXTURE = Path("tests/fixtures/us_m1_daily.xml")


def test_direct_xml_package_streams_cases() -> None:
    rows = list(_iter_package_bundles(FIXTURE))
    assert [bundle.case.serial_number for _source, bundle in rows] == [
        "97123456",
        "79345678",
    ]
    assert {source for source, _bundle in rows} == {"us_m1_daily.xml"}


def test_zip_package_streams_xml_without_extracting(tmp_path: Path) -> None:
    package = tmp_path / "apc260809.zip"
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("nested/apc260809.xml", FIXTURE.read_bytes())
        archive.writestr("README.txt", "ignored")

    rows = list(_iter_package_bundles(package))
    assert len(rows) == 2
    assert {source for source, _bundle in rows} == {"nested/apc260809.xml"}


def test_zip_without_xml_fails_closed(tmp_path: Path) -> None:
    package = tmp_path / "apc260809.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("README.txt", "no XML")

    try:
        list(_iter_package_bundles(package))
    except RuntimeError as exc:
        assert "contains no XML members" in str(exc)
    else:
        raise AssertionError("ZIP without XML must be rejected")
