from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from app.global_trademarks.preflight import (
    inspect_au_ipgod,
    inspect_ca_st96,
    inspect_gb_2018,
    inspect_tm_link,
)


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]], *, delimiter: str = ",") -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def _gb(root: Path) -> Path:
    path = root / "ukipo.txt"
    fields = [
        "Trade Mark", "Mark Text", "Name", "Postcode", "Region", "Country", "Status",
        "Category of Mark", "Mark Type", "Series", "No of Marks in Series", "Filed",
        "Published", "Registered", "Expired", "Renewal Due Date",
    ]
    _write_csv(
        path,
        fields,
        [{
            "Trade Mark": "UK00000000001",
            "Mark Text": "PREFLIGHT",
            "Name": "Example Ltd",
            "Status": "Registered",
            "Filed": "2010-01-01",
        }],
        delimiter="|",
    )
    return path


def _tm(root: Path) -> Path:
    path = root / "tm-link.csv"
    fields = ["application_number", "application_country", "trademark_text"]
    _write_csv(
        path,
        fields,
        [{"application_number": "123", "application_country": "EM", "trademark_text": "PREFLIGHT"}],
    )
    return path


def _au(root: Path) -> Path:
    path = root / "ipgod.csv"
    fields = ["ip_right_type", "application_number", "event_type"]
    _write_csv(
        path,
        fields,
        [{"ip_right_type": "trade_mark", "application_number": "456", "event_type": "registered"}],
    )
    return path


def _ca(root: Path) -> Path:
    path = root / "cipo.xml"
    path.write_text(
        """<root xmlns:tmk="urn:tmk" xmlns:com="urn:com">
<tmk:Trademark com:operationCategory="Update">
<com:ST13ApplicationNumber>300000010269700</com:ST13ApplicationNumber>
<tmk:MarkVerbalElementText>PREFLIGHT</tmk:MarkVerbalElementText>
</tmk:Trademark>
</root>""",
        encoding="utf-8",
    )
    return path


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="global-trademark-preflight-") as temporary:
        root = Path(temporary)

        gb = inspect_gb_2018(_gb(root), sample_limit=10)
        assert gb.schema_valid is True
        assert gb.sampled_rows == 1
        assert gb.usable_rows == 1
        assert len(gb.sha256) == 64

        tm = inspect_tm_link(_tm(root), jurisdiction="EU", table="details", sample_limit=10)
        assert tm.schema_valid is True
        assert tm.usable_rows == 1

        au = inspect_au_ipgod(_au(root), table="application-events", sample_limit=10)
        assert au.schema_valid is True
        assert au.usable_rows == 1

        ca = inspect_ca_st96(_ca(root), sample_limit=10)
        assert ca.schema_valid is True
        assert ca.usable_rows == 1
        assert ca.warnings == ()

        invalid = root / "invalid.csv"
        _write_csv(invalid, ["application_number"], [{"application_number": "999"}])
        bad = inspect_tm_link(invalid, jurisdiction="NZ", table="classes", sample_limit=10)
        assert bad.schema_valid is False
        assert set(bad.missing_columns) == {"application_country", "nice_class"}

    print({"status": "PASS", "database_writes": False, "source_preflight": True})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
