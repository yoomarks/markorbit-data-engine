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
from app.global_trademarks.schema import ensure_country_trademark_schemas
from app.global_trademarks.tm_link_seed import (
    ingest_tm_link_applicants,
    ingest_tm_link_applications,
    ingest_tm_link_classes,
    ingest_tm_link_details,
)


EXPECTED_TABLES = (
    "trademark_gb.historical_record",
    "trademark_gb.weekly_observation",
    "trademark_gb.comparable_relationship",
    "trademark_eu.tm_link_seed",
    "trademark_eu.api_observation",
    "trademark_nz.tm_link_seed",
    "trademark_nz.api_observation",
    "trademark_au.application",
    "trademark_au.party_activity",
    "trademark_au.application_link",
    "trademark_au.application_event",
    "trademark_au.application_classification",
    "trademark_au.application_description",
    "trademark_ca.st96_record",
    "trademark_ca.party",
    "trademark_ca.goods_service",
    "trademark_ca.event",
    "trademark_ca.relationship",
    "trademark_ca.asset",
)


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]], *, delimiter: str = ",") -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def _build_gb(path: Path) -> None:
    fields = [
        "Trade Mark",
        "Hyperlink",
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
            "Postcode": "LU1 ",
            "Region": "East of England",
            "Country": "United Kingdom",
            "Status": "Registered",
            "Category of Mark": "Standard",
            "Mark Type": "Figurative",
            "Series": "No",
            "No of Marks in Series": "0",
            "Filed": "1876-01-01",
            "Published": "1876-05-03",
            "Registered": "1876-01-01",
            "Renewal Due Date": "2022-01-01",
            "Class32": "1",
        }
    )
    _write_csv(path, fields, [row], delimiter="|")


def _build_tm_link(root: Path, office: str) -> dict[str, Path]:
    files = {
        "applications": root / f"{office}-applications.csv",
        "applicants": root / f"{office}-applicants.csv",
        "details": root / f"{office}-details.csv",
        "classes": root / f"{office}-classes.csv",
    }
    app_number = "00001234" if office == "EM" else "567890"
    _write_csv(
        files["applications"],
        [
            "application_number",
            "application_country",
            "madrid_number",
            "current_status",
            "filing_date",
            "registration_date",
            "renewal_due_date",
        ],
        [
            {
                "application_number": app_number,
                "application_country": office,
                "madrid_number": "123456" if office == "NZ" else "",
                "current_status": "Registered",
                "filing_date": "2014-01-02",
                "registration_date": "2015-02-03",
                "renewal_due_date": "2025-02-03",
            }
        ],
    )
    _write_csv(
        files["applicants"],
        ["application_number", "application_country", "applicant_country", "applicant_name"],
        [
            {
                "application_number": app_number,
                "application_country": office,
                "applicant_country": "CN" if office == "EM" else "NZ",
                "applicant_name": f"{office} Example Owner",
            }
        ],
    )
    _write_csv(
        files["details"],
        ["application_number", "application_country", "trademark_text", "uid_trademark"],
        [
            {
                "application_number": app_number,
                "application_country": office,
                "trademark_text": f"{office} EXAMPLE MARK",
                "uid_trademark": "ignored-family-id",
            }
        ],
    )
    _write_csv(
        files["classes"],
        ["application_number", "application_country", "nice_class"],
        [
            {
                "application_number": app_number,
                "application_country": office,
                "nice_class": "9",
            },
            {
                "application_number": app_number,
                "application_country": office,
                "nice_class": "35",
            },
        ],
    )
    return files


def _build_au(root: Path) -> dict[str, Path]:
    files = {
        name: root / f"au-{name}.csv"
        for name in (
            "application",
            "party-activity",
            "application-links",
            "application-events",
            "application-classification",
            "application-description",
        )
    }
    _write_csv(
        files["application"],
        [
            "ip_right_type",
            "application_number",
            "ip_right_sub_type",
            "status",
            "earliest_filed_date",
            "priority_date",
            "gained_registration_status_date",
            "gained_enforceable_status_date",
            "enforceable_from_date",
            "deemed_retired_date",
        ],
        [
            {
                "ip_right_type": "trade_mark",
                "application_number": "1234567",
                "ip_right_sub_type": "trade_mark",
                "status": "protected",
                "earliest_filed_date": "2010-01-02",
                "priority_date": "2010-01-02",
                "gained_registration_status_date": "2011-03-04",
                "gained_enforceable_status_date": "2011-03-04",
                "enforceable_from_date": "2010-01-02",
                "deemed_retired_date": "",
            }
        ],
    )
    _write_csv(
        files["party-activity"],
        [
            "ip_right_type",
            "application_number",
            "party_id",
            "party_role",
            "party_role_category",
            "party_type",
            "party_name",
            "abn",
            "country_code",
            "state_code",
            "postcode",
            "effective_from_date",
            "effective_to_date",
            "is_current",
        ],
        [
            {
                "ip_right_type": "trade_mark",
                "application_number": "1234567",
                "party_id": "42",
                "party_role": "owner",
                "party_role_category": "applicant",
                "party_type": "Organisation",
                "party_name": "AU Example Pty Ltd",
                "abn": "12345678901",
                "country_code": "AU",
                "state_code": "NSW",
                "postcode": "2000",
                "effective_from_date": "2011-03-04",
                "effective_to_date": "",
                "is_current": "true",
            }
        ],
    )
    _write_csv(
        files["application-links"],
        [
            "ip_right_type",
            "application_number",
            "link_type",
            "linked_application_number",
            "link_date",
            "linked_application_country",
        ],
        [
            {
                "ip_right_type": "trade_mark",
                "application_number": "1234567",
                "link_type": "convention_priority",
                "linked_application_number": "CN-EXAMPLE",
                "link_date": "2010-01-02",
                "linked_application_country": "CN",
            }
        ],
    )
    _write_csv(
        files["application-events"],
        [
            "ip_right_type",
            "application_number",
            "event_type",
            "event_category",
            "event_effective_date",
            "event_declared_date",
            "is_standing",
        ],
        [
            {
                "ip_right_type": "trade_mark",
                "application_number": "1234567",
                "event_type": "registered",
                "event_category": "registration",
                "event_effective_date": "2011-03-04",
                "event_declared_date": "2011-03-04",
                "is_standing": "true",
            }
        ],
    )
    _write_csv(
        files["application-classification"],
        [
            "ip_right_type",
            "application_number",
            "classification_system",
            "classification",
            "classification_importance",
            "classification_inventiveness",
            "classification_source",
            "classification_date",
            "classification_removal_date",
            "is_current",
        ],
        [
            {
                "ip_right_type": "trade_mark",
                "application_number": "1234567",
                "classification_system": "nice",
                "classification": "9",
                "classification_importance": "",
                "classification_inventiveness": "",
                "classification_source": "IP Australia",
                "classification_date": "2010-01-02",
                "classification_removal_date": "",
                "is_current": "true",
            }
        ],
    )
    _write_csv(
        files["application-description"],
        ["ip_right_type", "application_number", "description_type", "description_value"],
        [
            {
                "ip_right_type": "trade_mark",
                "application_number": "1234567",
                "description_type": "mark_text",
                "description_value": "AU EXAMPLE",
            }
        ],
    )
    return files


def _st13(application: str, extension: str) -> str:
    serial = f"{int(application):07d}{int(extension):02d}"
    return f"30{'0' * 4}{serial}"


def _build_ca(path: Path) -> None:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<root xmlns:tmk="urn:tmk" xmlns:com="urn:com">
  <tmk:Trademark>
    <com:ST13ApplicationNumber>{_st13('102697', '00')}</com:ST13ApplicationNumber>
    <com:RegistrationNumber>TMA123456</com:RegistrationNumber>
    <com:ApplicationDate>2013-03-19</com:ApplicationDate>
    <com:RegistrationDate>2015-04-20</com:RegistrationDate>
    <com:ExpiryDate>2025-04-20</com:ExpiryDate>
    <com:ApplicationLanguageCode>en</com:ApplicationLanguageCode>
    <tmk:MarkCurrentStatusCode>Registration published</tmk:MarkCurrentStatusCode>
    <tmk:MarkCurrentStatusDate>2015-04-20</tmk:MarkCurrentStatusDate>
    <tmk:MarkCategory>Trademark</tmk:MarkCategory>
    <tmk:MarkVerbalElementText>CA EXAMPLE</tmk:MarkVerbalElementText>
  </tmk:Trademark>
  <tmk:Trademark>
    <com:ST13ApplicationNumber>{_st13('102697', '01')}</com:ST13ApplicationNumber>
    <com:ApplicationDate>2016-01-01</com:ApplicationDate>
    <tmk:MarkCurrentStatusCode>Application filed</tmk:MarkCurrentStatusCode>
    <tmk:MarkVerbalElementText>CA EXAMPLE EXTENSION</tmk:MarkVerbalElementText>
  </tmk:Trademark>
</root>
"""
    path.write_text(xml, encoding="utf-8")


def _ingest_fixture_data(root: Path) -> None:
    gb = root / "OpenDataDomestic2018.txt"
    _build_gb(gb)
    assert ingest_ukipo_2018(gb, source_stream="DOMESTIC", batch_size=1) == 1
    assert ingest_ukipo_2018(gb, source_stream="DOMESTIC", batch_size=1) == 1

    eu = _build_tm_link(root, "EM")
    nz = _build_tm_link(root, "NZ")
    for jurisdiction, files in (("EU", eu), ("NZ", nz)):
        assert ingest_tm_link_applications(files["applications"], jurisdiction=jurisdiction, batch_size=1) == 1
        assert ingest_tm_link_applicants(files["applicants"], jurisdiction=jurisdiction, batch_size=1) == 1
        assert ingest_tm_link_details(files["details"], jurisdiction=jurisdiction, batch_size=1) == 1
        assert ingest_tm_link_classes(files["classes"], jurisdiction=jurisdiction, batch_size=1) == 2
        assert ingest_tm_link_classes(files["classes"], jurisdiction=jurisdiction, batch_size=1) == 2

    au = _build_au(root)
    assert ingest_application(au["application"], batch_size=1) == 1
    assert ingest_party_activity(au["party-activity"], batch_size=1) == 1
    assert ingest_application_links(au["application-links"], batch_size=1) == 1
    assert ingest_application_events(au["application-events"], batch_size=1) == 1
    assert ingest_application_classification(au["application-classification"], batch_size=1) == 1
    assert ingest_application_description(au["application-description"], batch_size=1) == 1
    assert ingest_party_activity(au["party-activity"], batch_size=1) == 1

    ca = root / "CA-TMK-GLOBAL-fixture.xml"
    _build_ca(ca)
    assert ingest_cipo_st96_core(ca, batch_size=1) == 2
    assert ingest_cipo_st96_core(ca, batch_size=1) == 2


def _assert_fixture_data() -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT application_number, nice_classes FROM trademark_gb.historical_record"
            )
            gb = cur.fetchone()
            assert gb == {"application_number": "UK00000000001", "nice_classes": [32]}

            cur.execute(
                """
                SELECT application_number, applicant_name, nice_classes, current_state_verified
                FROM trademark_eu.tm_link_seed
                """
            )
            eu = cur.fetchone()
            assert eu["applicant_name"] == "EM Example Owner"
            assert eu["nice_classes"] == [9, 35]
            assert eu["current_state_verified"] is False

            cur.execute(
                """
                SELECT application_number, madrid_number, applicant_name, nice_classes,
                       current_state_verified
                FROM trademark_nz.tm_link_seed
                """
            )
            nz = cur.fetchone()
            assert nz["madrid_number"] == "123456"
            assert nz["applicant_name"] == "NZ Example Owner"
            assert nz["nice_classes"] == [9, 35]
            assert nz["current_state_verified"] is False

            cur.execute("SELECT COUNT(*) AS count FROM trademark_au.party_activity")
            assert cur.fetchone()["count"] == 1
            cur.execute("SELECT COUNT(*) AS count FROM trademark_au.application_link")
            assert cur.fetchone()["count"] == 1

            cur.execute(
                """
                SELECT record_key, application_number, extension_counter, mark_text
                FROM trademark_ca.st96_record
                ORDER BY record_key
                """
            )
            ca = cur.fetchall()
            assert len(ca) == 2
            assert {row["extension_counter"] for row in ca} == {"00", "01"}
            assert {row["application_number"] for row in ca} == {"102697"}

            cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM acquisition.global_trademark_record_source
                WHERE jurisdiction IN ('GB', 'EU', 'NZ', 'CA')
                """
            )
            assert cur.fetchone()["count"] >= 12


def main() -> int:
    ensure_country_trademark_schemas()
    ensure_seed_ingest_schema()
    ensure_country_trademark_schemas()
    ensure_seed_ingest_schema()

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for table in EXPECTED_TABLES:
                cur.execute("SELECT to_regclass(%s) AS table_name", (table,))
                row = cur.fetchone()
                if not row or row["table_name"] is None:
                    raise RuntimeError(f"missing country trademark table: {table}")

            cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM information_schema.tables
                WHERE table_schema IN (
                    'trademark_gb', 'trademark_eu', 'trademark_nz',
                    'trademark_au', 'trademark_ca'
                )
                """
            )
            table_count = int(cur.fetchone()["count"])

    if table_count != len(EXPECTED_TABLES):
        raise RuntimeError(
            f"unexpected country trademark table count: {table_count} != {len(EXPECTED_TABLES)}"
        )

    with tempfile.TemporaryDirectory(prefix="global-trademark-fixture-") as temporary:
        _ingest_fixture_data(Path(temporary))
    _assert_fixture_data()

    print(
        {
            "status": "PASS",
            "country_native_schemas": ["GB", "EU", "NZ", "AU", "CA"],
            "table_count": table_count,
            "idempotent_migration": True,
            "idempotent_seed_ingest": True,
            "cipo_extension_identity_preserved": True,
            "tm_link_current_state_unverified": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
