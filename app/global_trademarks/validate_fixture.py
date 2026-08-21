from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks.au_ipgod import (
    ingest_application,
    ingest_application_classification,
    ingest_application_description,
    ingest_application_events,
    ingest_application_links,
    ingest_party_activity,
)
from app.global_trademarks.ca_st96 import ingest_cipo_st96_core
from app.global_trademarks.gb_open_data import ingest_ukipo_2018
from app.global_trademarks.ingest_schema import ensure_seed_ingest_schema
from app.global_trademarks.tm_link_seed import (
    ingest_tm_link_applicants,
    ingest_tm_link_applications,
    ingest_tm_link_classes,
    ingest_tm_link_details,
)


def _csv(path: Path, fields: list[str], rows: list[dict[str, str]], *, delimiter: str = ",") -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def _gb(root: Path) -> Path:
    path = root / "OpenDataDomestic2018.txt"
    fields = [
        "Trade Mark", "Mark Text", "Name", "Postcode", "Region", "Country", "Status",
        "Category of Mark", "Mark Type", "Series", "No of Marks in Series", "Filed",
        "Published", "Registered", "Expired", "Renewal Due Date",
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
    _csv(path, fields, [row], delimiter="|")
    return path


def _tm_link(root: Path, office: str) -> dict[str, Path]:
    app_number = "00001234" if office == "EM" else "567890"
    paths = {name: root / f"{office}-{name}.csv" for name in ("apps", "owners", "marks", "classes")}
    _csv(
        paths["apps"],
        [
            "application_number", "application_country", "madrid_number", "current_status",
            "filing_date", "registration_date", "renewal_due_date",
        ],
        [{
            "application_number": app_number,
            "application_country": office,
            "madrid_number": "123456" if office == "NZ" else "",
            "current_status": "Registered",
            "filing_date": "2014-01-02",
            "registration_date": "2015-02-03",
            "renewal_due_date": "2025-02-03",
        }],
    )
    _csv(
        paths["owners"],
        ["application_number", "application_country", "applicant_country", "applicant_name"],
        [{
            "application_number": app_number,
            "application_country": office,
            "applicant_country": "CN" if office == "EM" else "NZ",
            "applicant_name": f"{office} Example Owner",
        }],
    )
    _csv(
        paths["marks"],
        ["application_number", "application_country", "trademark_text"],
        [{
            "application_number": app_number,
            "application_country": office,
            "trademark_text": f"{office} EXAMPLE MARK",
        }],
    )
    _csv(
        paths["classes"],
        ["application_number", "application_country", "nice_class"],
        [
            {"application_number": app_number, "application_country": office, "nice_class": "9"},
            {"application_number": app_number, "application_country": office, "nice_class": "35"},
        ],
    )
    return paths


def _au(root: Path) -> dict[str, Path]:
    rows = {
        "application": ({
            "ip_right_type": "trade_mark", "application_number": "1234567",
            "ip_right_sub_type": "trade_mark", "status": "protected",
            "earliest_filed_date": "2010-01-02", "priority_date": "2010-01-02",
            "gained_registration_status_date": "2011-03-04",
            "gained_enforceable_status_date": "2011-03-04",
            "enforceable_from_date": "2010-01-02", "deemed_retired_date": "",
        }),
        "party-activity": ({
            "ip_right_type": "trade_mark", "application_number": "1234567", "party_id": "42",
            "party_role": "owner", "party_role_category": "applicant", "party_type": "Organisation",
            "party_name": "AU Example Pty Ltd", "abn": "12345678901", "country_code": "AU",
            "state_code": "NSW", "postcode": "2000", "effective_from_date": "2011-03-04",
            "effective_to_date": "", "is_current": "true",
        }),
        "application-links": ({
            "ip_right_type": "trade_mark", "application_number": "1234567",
            "link_type": "convention_priority", "linked_application_number": "CN-EXAMPLE",
            "link_date": "2010-01-02", "linked_application_country": "CN",
        }),
        "application-events": ({
            "ip_right_type": "trade_mark", "application_number": "1234567", "event_type": "registered",
            "event_category": "registration", "event_effective_date": "2011-03-04",
            "event_declared_date": "2011-03-04", "is_standing": "true",
        }),
        "application-classification": ({
            "ip_right_type": "trade_mark", "application_number": "1234567",
            "classification_system": "nice", "classification": "9",
            "classification_importance": "", "classification_inventiveness": "",
            "classification_source": "IP Australia", "classification_date": "2010-01-02",
            "classification_removal_date": "", "is_current": "true",
        }),
        "application-description": ({
            "ip_right_type": "trade_mark", "application_number": "1234567",
            "description_type": "mark_text", "description_value": "AU EXAMPLE",
        }),
    }
    paths: dict[str, Path] = {}
    for name, row in rows.items():
        path = root / f"au-{name}.csv"
        _csv(path, list(row), [row])
        paths[name] = path
    return paths


def _ca(root: Path) -> Path:
    path = root / "CA-TMK-GLOBAL-fixture.xml"
    path.write_text(
        """<root xmlns:tmk="urn:tmk" xmlns:com="urn:com">
<tmk:Trademark><com:ST13ApplicationNumber>300000010269700</com:ST13ApplicationNumber>
<com:RegistrationNumber>TMA123456</com:RegistrationNumber><com:ApplicationDate>2013-03-19</com:ApplicationDate>
<tmk:MarkCurrentStatusCode>Registration published</tmk:MarkCurrentStatusCode>
<tmk:MarkVerbalElementText>CA EXAMPLE</tmk:MarkVerbalElementText></tmk:Trademark>
<tmk:Trademark><com:ST13ApplicationNumber>300000010269701</com:ST13ApplicationNumber>
<com:ApplicationDate>2016-01-01</com:ApplicationDate>
<tmk:MarkCurrentStatusCode>Application filed</tmk:MarkCurrentStatusCode>
<tmk:MarkVerbalElementText>CA EXAMPLE EXTENSION</tmk:MarkVerbalElementText></tmk:Trademark>
</root>""",
        encoding="utf-8",
    )
    return path


def _run_ingest(root: Path) -> None:
    gb = _gb(root)
    assert ingest_ukipo_2018(gb, source_stream="DOMESTIC", batch_size=1) == 1
    assert ingest_ukipo_2018(gb, source_stream="DOMESTIC", batch_size=1) == 1

    for jurisdiction, office in (("EU", "EM"), ("NZ", "NZ")):
        source = _tm_link(root, office)
        assert ingest_tm_link_applications(source["apps"], jurisdiction=jurisdiction, batch_size=1) == 1
        assert ingest_tm_link_applicants(source["owners"], jurisdiction=jurisdiction, batch_size=1) == 1
        assert ingest_tm_link_details(source["marks"], jurisdiction=jurisdiction, batch_size=1) == 1
        assert ingest_tm_link_classes(source["classes"], jurisdiction=jurisdiction, batch_size=1) == 2
        assert ingest_tm_link_classes(source["classes"], jurisdiction=jurisdiction, batch_size=1) == 2

    au = _au(root)
    assert ingest_application(au["application"], batch_size=1) == 1
    assert ingest_party_activity(au["party-activity"], batch_size=1) == 1
    assert ingest_application_links(au["application-links"], batch_size=1) == 1
    assert ingest_application_events(au["application-events"], batch_size=1) == 1
    assert ingest_application_classification(au["application-classification"], batch_size=1) == 1
    assert ingest_application_description(au["application-description"], batch_size=1) == 1
    assert ingest_party_activity(au["party-activity"], batch_size=1) == 1

    ca = _ca(root)
    assert ingest_cipo_st96_core(ca, batch_size=1) == 2
    assert ingest_cipo_st96_core(ca, batch_size=1) == 2


def _verify() -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT application_number, nice_classes FROM trademark_gb.historical_record")
            assert cur.fetchone() == {"application_number": "UK00000000001", "nice_classes": [32]}

            for schema in ("trademark_eu", "trademark_nz"):
                cur.execute(
                    f"SELECT nice_classes, current_state_verified FROM {schema}.tm_link_seed"
                )
                row = cur.fetchone()
                assert row["nice_classes"] == [9, 35]
                assert row["current_state_verified"] is False

            cur.execute("SELECT COUNT(*) AS count FROM trademark_au.party_activity")
            assert cur.fetchone()["count"] == 1
            cur.execute("SELECT COUNT(*) AS count FROM trademark_au.application_link")
            assert cur.fetchone()["count"] == 1

            cur.execute(
                "SELECT application_number, extension_counter FROM trademark_ca.st96_record ORDER BY record_key"
            )
            ca = cur.fetchall()
            assert [row["extension_counter"] for row in ca] == ["00", "01"]
            assert {row["application_number"] for row in ca} == {"102697"}

            cur.execute(
                """SELECT COUNT(*) AS count FROM acquisition.global_trademark_record_source
                WHERE jurisdiction IN ('GB', 'EU', 'NZ', 'CA')"""
            )
            assert cur.fetchone()["count"] == 11


def main() -> int:
    ensure_seed_ingest_schema()
    ensure_seed_ingest_schema()
    with tempfile.TemporaryDirectory(prefix="global-trademark-fixture-") as temporary:
        _run_ingest(Path(temporary))
    _verify()
    print(
        {
            "status": "PASS",
            "country_native_schemas": ["GB", "EU", "NZ", "AU", "CA"],
            "idempotent_seed_ingest": True,
            "cipo_extension_identity_preserved": True,
            "tm_link_current_state_unverified": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
