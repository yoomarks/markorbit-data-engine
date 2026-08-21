from __future__ import annotations

import tempfile
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks.ca_st96 import ingest_cipo_st96_core


_RECORD_KEY = "123456:00"


def _baseline_xml() -> str:
    return """<root xmlns:tmk="urn:tmk" xmlns:com="urn:com" xmlns:catmk="urn:catmk">
<tmk:Trademark com:operationCategory="Update">
  <com:ST13ApplicationNumber>300000012345600</com:ST13ApplicationNumber>
  <com:RegistrationNumber>TMA123456</com:RegistrationNumber>
  <tmk:MarkSignificantVerbalElementText>RICH BASELINE</tmk:MarkSignificantVerbalElementText>
  <tmk:ApplicantBag>
    <tmk:Applicant>
      <com:LegalEntityName>Baseline Owner Inc.</com:LegalEntityName>
      <com:Contact com:languageCode="en">
        <com:Name><com:EntityName com:languageCode="en">Baseline Owner Inc.</com:EntityName></com:Name>
        <com:PostalAddressBag><com:PostalAddress><com:PostalStructuredAddress com:languageCode="en">
          <com:AddressLineText com:sequenceNumber="1">100 Baseline Street</com:AddressLineText>
          <com:AddressLineText com:sequenceNumber="2">Toronto</com:AddressLineText>
          <com:GeographicRegionName com:geographicRegionCategory="Province">ONTARIO</com:GeographicRegionName>
          <com:CountryCode>CA</com:CountryCode><com:PostalCode>M5V1A1</com:PostalCode>
        </com:PostalStructuredAddress></com:PostalAddress></com:PostalAddressBag>
      </com:Contact>
      <com:NationalLegalEntityCode>CA</com:NationalLegalEntityCode>
    </tmk:Applicant>
  </tmk:ApplicantBag>
  <tmk:NationalRepresentative>
    <com:CommentText>4779</com:CommentText>
    <com:Contact com:languageCode="en">
      <com:Name><com:EntityName com:languageCode="en">Baseline Agent LLP</com:EntityName></com:Name>
      <com:PostalAddressBag><com:PostalAddress><com:PostalStructuredAddress com:languageCode="en">
        <com:AddressLineText com:sequenceNumber="1">200 Agent Avenue</com:AddressLineText>
        <com:CountryCode>CA</com:CountryCode><com:PostalCode>K2P0R7</com:PostalCode>
      </com:PostalStructuredAddress></com:PostalAddress></com:PostalAddressBag>
    </com:Contact>
  </tmk:NationalRepresentative>
  <tmk:NationalCorrespondent>
    <com:CommentText>8811</com:CommentText>
    <com:Contact com:languageCode="fr">
      <com:Name><com:EntityName com:languageCode="fr">Baseline Service Rep</com:EntityName></com:Name>
      <com:PostalAddressBag><com:PostalAddress><com:PostalStructuredAddress com:languageCode="fr">
        <com:AddressLineText com:sequenceNumber="1">300 Service Road</com:AddressLineText>
        <com:CountryCode>CA</com:CountryCode><com:PostalCode>H2Y1C6</com:PostalCode>
      </com:PostalStructuredAddress></com:PostalAddress></com:PostalAddressBag>
    </com:Contact>
  </tmk:NationalCorrespondent>
  <tmk:GoodsServicesBag><tmk:GoodsServices><tmk:ClassDescriptionBag>
    <tmk:ClassDescription>
      <com:ClassificationVersion>12</com:ClassificationVersion><tmk:ClassNumber>9</tmk:ClassNumber>
      <tmk:GoodsServicesDescriptionText com:sequenceNumber="Goods1" com:languageCode="en">downloadable computer software</tmk:GoodsServicesDescriptionText>
      <tmk:ClassificationTermBag><tmk:ClassificationTerm><tmk:ClassificationTermText com:languageCode="fr">logiciels téléchargeables</tmk:ClassificationTermText></tmk:ClassificationTerm></tmk:ClassificationTermBag>
    </tmk:ClassDescription>
    <tmk:ClassDescription>
      <com:ClassificationVersion>12</com:ClassificationVersion><tmk:ClassNumber>42</tmk:ClassNumber>
      <tmk:GoodsServicesDescriptionText com:sequenceNumber="Services1" com:languageCode="en">software as a service</tmk:GoodsServicesDescriptionText>
    </tmk:ClassDescription>
  </tmk:ClassDescriptionBag></tmk:GoodsServices></tmk:GoodsServicesBag>
  <tmk:MarkEventBag>
    <tmk:MarkEvent><tmk:MarkEventCategory>National prosecution history entry</tmk:MarkEventCategory><tmk:MarkEventResponseDate>2026-02-01</tmk:MarkEventResponseDate><tmk:NationalMarkEvent><tmk:MarkEventCode>20</tmk:MarkEventCode><tmk:MarkEventDescriptionText>Examiner's First Report</tmk:MarkEventDescriptionText><tmk:MarkEventAdditionalText>First report issued</tmk:MarkEventAdditionalText></tmk:NationalMarkEvent><tmk:MarkEventDate>2026-01-01</tmk:MarkEventDate></tmk:MarkEvent>
    <tmk:MarkEvent><tmk:MarkEventCategory>National prosecution history entry</tmk:MarkEventCategory><tmk:NationalMarkEvent><tmk:MarkEventCode>42</tmk:MarkEventCode><tmk:MarkEventDescriptionText>Advertised</tmk:MarkEventDescriptionText><tmk:MarkEventAdditionalText>Vol. 73 Issue 100</tmk:MarkEventAdditionalText></tmk:NationalMarkEvent><tmk:MarkEventDate>2026-03-01</tmk:MarkEventDate></tmk:MarkEvent>
  </tmk:MarkEventBag>
  <tmk:AssociatedApplicationNumber><com:IPOfficeCode>CA</com:IPOfficeCode><com:ST13ApplicationNumber>300000010269700</com:ST13ApplicationNumber></tmk:AssociatedApplicationNumber>
  <tmk:DivisionalApplicationBag><tmk:InitialApplicationNumber><com:IPOfficeCode>CA</com:IPOfficeCode><com:ST13ApplicationNumber>300000023613800</com:ST13ApplicationNumber></tmk:InitialApplicationNumber><tmk:InitialApplicationDate>1956-06-07</tmk:InitialApplicationDate></tmk:DivisionalApplicationBag>
  <catmk:NationalAssociatedMarkBag><catmk:NationalAssociatedMark><com:ApplicationNumber><com:IPOfficeCode>CA</com:IPOfficeCode><com:ST13ApplicationNumber>300000017098410</com:ST13ApplicationNumber></com:ApplicationNumber><com:RegistrationNumber>TMA846132</com:RegistrationNumber><catmk:PerSeRegistration>false</catmk:PerSeRegistration></catmk:NationalAssociatedMark></catmk:NationalAssociatedMarkBag>
</tmk:Trademark>
</root>"""


def _weekly_update_xml() -> str:
    return """<root xmlns:tmk="urn:tmk" xmlns:com="urn:com" xmlns:catmk="urn:catmk">
<tmk:Trademark com:operationCategory="Update">
  <com:ST13ApplicationNumber>300000012345600</com:ST13ApplicationNumber>
  <com:RegistrationNumber>TMA123456</com:RegistrationNumber>
  <tmk:MarkSignificantVerbalElementText>RICH WEEKLY UPDATE</tmk:MarkSignificantVerbalElementText>
  <tmk:ApplicantBag><tmk:Applicant><com:LegalEntityName>Updated Owner Ltd.</com:LegalEntityName><com:Contact com:languageCode="en"><com:Name><com:EntityName com:languageCode="en">Updated Owner Ltd.</com:EntityName></com:Name><com:PostalAddressBag><com:PostalAddress><com:PostalStructuredAddress com:languageCode="en"><com:AddressLineText com:sequenceNumber="1">900 Updated Street</com:AddressLineText><com:CountryCode>GB</com:CountryCode></com:PostalStructuredAddress></com:PostalAddress></com:PostalAddressBag></com:Contact><com:NationalLegalEntityCode>GB</com:NationalLegalEntityCode></tmk:Applicant></tmk:ApplicantBag>
  <tmk:GoodsServicesBag><tmk:GoodsServices><tmk:ClassDescriptionBag><tmk:ClassDescription><com:ClassificationVersion>12</com:ClassificationVersion><tmk:ClassNumber>9</tmk:ClassNumber><tmk:GoodsServicesDescriptionText com:sequenceNumber="Goods1" com:languageCode="en">downloadable artificial intelligence software</tmk:GoodsServicesDescriptionText></tmk:ClassDescription></tmk:ClassDescriptionBag></tmk:GoodsServices></tmk:GoodsServicesBag>
  <tmk:MarkEventBag><tmk:MarkEvent><tmk:MarkEventCategory>National prosecution history entry</tmk:MarkEventCategory><tmk:NationalMarkEvent><tmk:MarkEventCode>57</tmk:MarkEventCode><tmk:MarkEventDescriptionText>Amendment to Registration</tmk:MarkEventDescriptionText></tmk:NationalMarkEvent><tmk:MarkEventDate>2026-07-15</tmk:MarkEventDate></tmk:MarkEvent></tmk:MarkEventBag>
  <catmk:NationalAssociatedMarkBag><catmk:NationalAssociatedMark><com:ApplicationNumber><com:IPOfficeCode>CA</com:IPOfficeCode><com:ST13ApplicationNumber>300000019999900</com:ST13ApplicationNumber></com:ApplicationNumber><com:RegistrationNumber>TMA999999</com:RegistrationNumber><catmk:PerSeRegistration>true</catmk:PerSeRegistration></catmk:NationalAssociatedMark></catmk:NationalAssociatedMarkBag>
</tmk:Trademark>
</root>"""


def _delete_xml() -> str:
    return """<root xmlns:tmk="urn:tmk" xmlns:com="urn:com">
<tmk:Trademark com:operationCategory="Delete">
  <com:ST13ApplicationNumber>300000012345600</com:ST13ApplicationNumber>
</tmk:Trademark>
</root>"""


def _write(path: Path, payload: str) -> None:
    path.write_text(payload, encoding="utf-8")


def _count(cur, table: str) -> int:
    cur.execute(
        f"SELECT COUNT(*) AS count FROM trademark_ca.{table} WHERE record_key = %s",
        (_RECORD_KEY,),
    )
    return int(cur.fetchone()["count"])


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="cipo-rich-observation-") as temporary:
        root = Path(temporary)
        baseline = root / "CA-TMK-GLOBAL-rich-fixture.xml"
        weekly = root / "CA-TMK-UPDATE-rich-fixture.xml"
        deletion = root / "CA-TMK-DELETE-rich-fixture.xml"
        _write(baseline, _baseline_xml())
        _write(weekly, _weekly_update_xml())
        _write(deletion, _delete_xml())

        assert ingest_cipo_st96_core(
            baseline,
            source_id="CIPO_GLOBAL_2025_06_14",
            batch_size=1,
        ) == 1

        with postgres_conn() as conn:
            with conn.cursor() as cur:
                assert _count(cur, "party") == 3
                assert _count(cur, "goods_service") == 3
                assert _count(cur, "event") == 2
                assert _count(cur, "relationship") == 3

                cur.execute(
                    """
                    SELECT party_role, party_name, party_code, language_code,
                           address_country, address_lines, national_legal_entity_code
                    FROM trademark_ca.party
                    WHERE record_key = %s
                    ORDER BY source_index
                    """,
                    (_RECORD_KEY,),
                )
                parties = cur.fetchall()
                assert parties[0]["party_role"] == "CURRENT_OWNER"
                assert parties[0]["party_name"] == "Baseline Owner Inc."
                assert parties[0]["address_country"] == "CA"
                assert parties[0]["address_lines"] == ["100 Baseline Street", "Toronto"]
                assert parties[0]["national_legal_entity_code"] == "CA"
                assert parties[1]["party_role"] == "TRADEMARK_AGENT"
                assert parties[1]["party_code"] == "4779"
                assert parties[2]["party_role"] == "REPRESENTATIVE_FOR_SERVICE"
                assert parties[2]["language_code"] == "fr"

                cur.execute(
                    """
                    SELECT class_number, text_kind, text_value, language_code,
                           sequence_number, classification_version
                    FROM trademark_ca.goods_service
                    WHERE record_key = %s
                    ORDER BY source_index
                    """,
                    (_RECORD_KEY,),
                )
                goods = cur.fetchall()
                assert {(row["class_number"], row["text_kind"]) for row in goods} == {
                    (9, "GOODS_SERVICES_DESCRIPTION"),
                    (9, "CLASSIFICATION_TERM"),
                    (42, "GOODS_SERVICES_DESCRIPTION"),
                }
                assert goods[0]["sequence_number"] == "Goods1"
                assert all(row["classification_version"] == "12" for row in goods)

                cur.execute(
                    """
                    SELECT event_code, event_text, event_date, response_date,
                           event_category, additional_text
                    FROM trademark_ca.event
                    WHERE record_key = %s
                    ORDER BY source_index
                    """,
                    (_RECORD_KEY,),
                )
                events = cur.fetchall()
                assert events[0]["event_code"] == "20"
                assert events[0]["response_date"].isoformat() == "2026-02-01"
                assert events[1]["event_code"] == "42"
                assert events[1]["additional_text"] == "Vol. 73 Issue 100"

                cur.execute(
                    """
                    SELECT relationship_type, related_application_number,
                           related_extension_counter, related_registration_number,
                           related_office_code, per_se_registration,
                           initial_application_date
                    FROM trademark_ca.relationship
                    WHERE record_key = %s
                    ORDER BY source_index
                    """,
                    (_RECORD_KEY,),
                )
                relationships = cur.fetchall()
                assert relationships[0]["relationship_type"] == "PREVIOUS_ASSOCIATED_APPLICATION"
                assert relationships[0]["related_application_number"] == "102697"
                assert relationships[1]["relationship_type"] == "DIVISIONAL_FROM"
                assert relationships[1]["related_application_number"] == "236138"
                assert relationships[1]["initial_application_date"].isoformat() == "1956-06-07"
                assert relationships[2]["relationship_type"] == "NATIONAL_ASSOCIATED_MARK"
                assert relationships[2]["related_registration_number"] == "TMA846132"
                assert relationships[2]["per_se_registration"] is False

                cur.execute(
                    """
                    SELECT source_record_role
                    FROM acquisition.global_trademark_record_source
                    WHERE jurisdiction = 'CA' AND source_record_key = %s
                    """,
                    (_RECORD_KEY,),
                )
                assert {row["source_record_role"] for row in cur.fetchall()} >= {
                    "CIPO_ST96_UPDATE",
                    "CIPO_ST96_PARTY",
                    "CIPO_ST96_GOODS_SERVICE",
                    "CIPO_ST96_EVENT",
                    "CIPO_ST96_RELATIONSHIP",
                }

        assert ingest_cipo_st96_core(
            weekly,
            source_id="CIPO_WEEKLY",
            batch_size=1,
        ) == 1

        with postgres_conn() as conn:
            with conn.cursor() as cur:
                assert _count(cur, "party") == 4
                assert _count(cur, "goods_service") == 4
                assert _count(cur, "event") == 3
                assert _count(cur, "relationship") == 4

                cur.execute(
                    "SELECT COUNT(DISTINCT source_object_id) AS count FROM trademark_ca.party WHERE record_key = %s",
                    (_RECORD_KEY,),
                )
                assert cur.fetchone()["count"] == 2
                cur.execute(
                    "SELECT mark_text FROM trademark_ca.st96_record WHERE record_key = %s",
                    (_RECORD_KEY,),
                )
                assert cur.fetchone()["mark_text"] == "RICH WEEKLY UPDATE"

        counts_before_delete: dict[str, int] = {}
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                for table in ("party", "goods_service", "event", "relationship"):
                    counts_before_delete[table] = _count(cur, table)

        assert ingest_cipo_st96_core(
            deletion,
            source_id="CIPO_WEEKLY",
            batch_size=1,
        ) == 1

        with postgres_conn() as conn:
            with conn.cursor() as cur:
                for table, expected in counts_before_delete.items():
                    assert _count(cur, table) == expected
                cur.execute(
                    """
                    SELECT source_present, last_operation_category
                    FROM trademark_ca.record_state WHERE record_key = %s
                    """,
                    (_RECORD_KEY,),
                )
                assert cur.fetchone() == {
                    "source_present": False,
                    "last_operation_category": "Delete",
                }
                cur.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM acquisition.global_trademark_record_source
                    WHERE jurisdiction = 'CA' AND source_record_key = %s
                      AND source_record_role = 'CIPO_ST96_DELETE'
                    """,
                    (_RECORD_KEY,),
                )
                assert cur.fetchone()["count"] == 1

        # Completed source objects are idempotent no-ops and do not duplicate observations.
        assert ingest_cipo_st96_core(
            weekly,
            source_id="CIPO_WEEKLY",
            batch_size=1,
        ) == 1
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                for table, expected in counts_before_delete.items():
                    assert _count(cur, table) == expected

    print(
        {
            "status": "PASS",
            "current_owner_agent_service_rep_observed": True,
            "goods_services_observed": True,
            "office_events_observed": True,
            "source_declared_relationships_observed": True,
            "weekly_update_appends_source_snapshot": True,
            "delete_preserves_child_history": True,
            "child_observations_are_not_current_projection": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
