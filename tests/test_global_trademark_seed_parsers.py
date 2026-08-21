import csv
from pathlib import Path

from app.global_trademarks.ca_st96 import iter_cipo_records
from app.global_trademarks.gb_open_data import iter_ukipo_2018


def _write_gb(path: Path) -> None:
    fields = [
        "Trade Mark",
        "Mark Text",
        "Name",
        "Postcode",
        "Region",
        "Country",
        "Status",
        "Category of Mark",
        "Mark Type",
        "Series",
        "No of Marks in Series",
        "Filed",
        "Published",
        "Registered",
        "Expired",
        "Renewal Due Date",
        *[f"Class{number}" for number in range(1, 46)],
    ]
    row = {field: "" for field in fields}
    row.update(
        {
            "Trade Mark": "UK00000000001   ",
            "Mark Text": "BASS & Co's PALE ALE",
            "Name": "Pioneer Brewing Company Limited",
            "Status": "Registered",
            "Filed": "1876-01-01",
            "Registered": "1876-01-01",
            "Renewal Due Date": "2022-01-01",
            "Class32": "1",
        }
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="|")
        writer.writeheader()
        writer.writerow(row)


def test_ukipo_parser_keeps_native_fields_and_classes(tmp_path: Path) -> None:
    path = tmp_path / "OpenDataDomestic2018.txt"
    _write_gb(path)

    rows = list(iter_ukipo_2018(path))

    assert len(rows) == 1
    assert rows[0]["application_number"] == "UK00000000001"
    assert rows[0]["mark_text"] == "BASS & Co's PALE ALE"
    assert rows[0]["nice_classes"] == [32]
    assert str(rows[0]["renewal_due_date"]) == "2022-01-01"


def test_cipo_parser_preserves_extension_counter_identity(tmp_path: Path) -> None:
    path = tmp_path / "cipo.xml"
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<root xmlns:tmk="urn:tmk" xmlns:com="urn:com">
  <tmk:Trademark>
    <com:ST13ApplicationNumber>300000010269700</com:ST13ApplicationNumber>
    <tmk:MarkVerbalElementText>BASE</tmk:MarkVerbalElementText>
  </tmk:Trademark>
  <tmk:Trademark>
    <com:ST13ApplicationNumber>300000010269701</com:ST13ApplicationNumber>
    <tmk:MarkVerbalElementText>EXTENSION</tmk:MarkVerbalElementText>
  </tmk:Trademark>
</root>
""",
        encoding="utf-8",
    )

    rows = list(iter_cipo_records(path))

    assert [row["record_key"] for row in rows] == ["102697:00", "102697:01"]
    assert {row["extension_counter"] for row in rows} == {"00", "01"}
