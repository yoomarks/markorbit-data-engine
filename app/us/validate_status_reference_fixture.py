from __future__ import annotations

from datetime import date
import json
import uuid

from app.db import clickhouse_client, postgres_conn
from app.us.ingest import _cleanup_package_outputs
from app.us.model import USCaseBundle, USCaseRecord
from app.us.publisher import USBatchPublisher
from app.us.status_reference import (
    AUTHORITY,
    CURRENT_OFFICIAL_DOCUMENT_DATE,
    CURRENT_OFFICIAL_DOCUMENT_NAME,
    CURRENT_OFFICIAL_DOCUMENT_URL,
    REFERENCE_KIND,
    REFERENCE_PAYLOAD_SCHEMA,
    active_reference_metadata,
    import_reference_payload,
    list_active_status_codes,
    lookup_active_status_codes,
    status_reference_schema_ready,
)
from app.us.status_reference_inventory import build_inventory


VERSION_1 = "USPTO_STATUS_CODES_CI_20250813_V1"
VERSION_2 = "USPTO_STATUS_CODES_CI_20250813_V2"
PACKAGE_700 = uuid.UUID("99999999-9999-9999-9999-999999999971")
PACKAGE_999 = uuid.UUID("99999999-9999-9999-9999-999999999972")


def _payload(version: str, source_sha: str, suffix: str) -> dict:
    return {
        "schema": REFERENCE_PAYLOAD_SCHEMA,
        "authority": AUTHORITY,
        "reference_kind": REFERENCE_KIND,
        "reference_version": version,
        "source": {
            "document_name": CURRENT_OFFICIAL_DOCUMENT_NAME,
            "document_date": CURRENT_OFFICIAL_DOCUMENT_DATE.isoformat(),
            "url": CURRENT_OFFICIAL_DOCUMENT_URL,
            "sha256": source_sha,
            "evidence_note": "CI synthetic status text; not a production USPTO mapping.",
        },
        "records": [
            {
                "code": "630",
                "official_description": f"CI fixture status 630 {suffix}",
                "official_definition": "",
                "official_category": "",
                "source_locator": "CI row 1",
            },
            {
                "code": "700",
                "official_description": f"CI fixture status 700 {suffix}",
                "official_definition": "",
                "official_category": "",
                "source_locator": "CI row 2",
            },
        ],
    }


def _cleanup_reference() -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM reference.us_trademark_status_code
                WHERE reference_version IN (%s, %s)
                """,
                (VERSION_1, VERSION_2),
            )
            cur.execute(
                """
                DELETE FROM reference.us_trademark_status_reference_version
                WHERE reference_version IN (%s, %s)
                """,
                (VERSION_1, VERSION_2),
            )
        conn.commit()


def _publish_case(package_id: uuid.UUID, serial: str, status_code: str, rank: int) -> None:
    publisher = USBatchPublisher(
        clickhouse_client(),
        package_id=package_id,
        package_kind="DAILY_APPLICATIONS",
        source_effective_date=date(2026, 8, 9),
        source_rank=rank,
        batch_size=100,
    )
    publisher.add(
        USCaseBundle(
            case=USCaseRecord(
                serial_number=serial,
                filing_date=date(2026, 8, 1),
                status_code=status_code,
                status_date=date(2026, 8, 9),
                mark_identification=f"STATUS REF FIXTURE {status_code}",
            )
        ),
        f"status-reference-{status_code}.xml",
    )
    publisher.close()


def main() -> None:
    if not status_reference_schema_ready():
        raise RuntimeError("USPTO status reference schema is not ready in live fixture")

    try:
        _cleanup_reference()
        _cleanup_package_outputs(PACKAGE_700)
        _cleanup_package_outputs(PACKAGE_999)

        first = import_reference_payload(
            _payload(VERSION_1, "a" * 64, "v1"), activate=True
        )
        if first["status"] != "IMPORTED" or first["active"] is not True:
            raise RuntimeError(f"Status reference first import failed: {first}")
        repeat = import_reference_payload(
            _payload(VERSION_1, "a" * 64, "v1"), activate=True
        )
        if repeat["status"] != "ALREADY_IMPORTED":
            raise RuntimeError(f"Status reference idempotent import failed: {repeat}")

        mismatch_rejected = False
        try:
            import_reference_payload(
                _payload(VERSION_1, "b" * 64, "changed-source"), activate=True
            )
        except RuntimeError as exc:
            mismatch_rejected = "different source/payload evidence" in str(exc)
        if not mismatch_rejected:
            raise RuntimeError("Status reference same-version evidence mismatch was not rejected")

        second_inactive = import_reference_payload(
            _payload(VERSION_2, "c" * 64, "v2"), activate=False
        )
        if second_inactive["status"] != "IMPORTED" or second_inactive["active"] is not False:
            raise RuntimeError(f"Status reference inactive import failed: {second_inactive}")
        metadata = active_reference_metadata()
        if not metadata or metadata["reference_version"] != VERSION_1:
            raise RuntimeError("Inactive reference import changed active version")

        activated = import_reference_payload(
            _payload(VERSION_2, "c" * 64, "v2"), activate=True
        )
        if activated["status"] != "ACTIVATED_EXISTING":
            raise RuntimeError(f"Status reference activation failed: {activated}")
        metadata = active_reference_metadata()
        if not metadata or metadata["reference_version"] != VERSION_2:
            raise RuntimeError("Status reference active version did not switch to V2")
        active_rows = list_active_status_codes()
        if len(active_rows["status_codes"]) != 2:
            raise RuntimeError(f"Status reference active row count mismatch: {active_rows}")

        lookup = lookup_active_status_codes(["700", "999"])
        if "700" not in lookup["mappings"] or "999" in lookup["mappings"]:
            raise RuntimeError(f"Status reference lookup mismatch: {lookup}")

        _publish_case(PACKAGE_700, "88991001", "700", 9_000_000_001)
        _publish_case(PACKAGE_999, "88991002", "999", 9_000_000_002)
        inventory = build_inventory()
        if inventory["mapped_code_count"] != 1:
            raise RuntimeError(f"Status reference mapped inventory mismatch: {inventory}")
        if inventory["unmapped_code_count"] != 1:
            raise RuntimeError(f"Status reference unmapped inventory mismatch: {inventory}")
        if inventory["unmapped_status_codes"] != [
            {"status_code": "999", "case_count": 1}
        ]:
            raise RuntimeError(f"Status reference unmapped code detail mismatch: {inventory}")

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "contract": "US_OFFICIAL_STATUS_REFERENCE_FIXTURE",
                    "schema_ready": "PASS",
                    "first_import": first["status"],
                    "idempotent_import": repeat["status"],
                    "same_version_different_evidence_rejected": "PASS",
                    "inactive_version_import": "PASS",
                    "active_version_switch": metadata["reference_version"],
                    "active_record_count": len(active_rows["status_codes"]),
                    "mapped_700": "PASS",
                    "unmapped_999": "PASS",
                    "semantics": inventory["semantics"],
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    finally:
        _cleanup_package_outputs(PACKAGE_700)
        _cleanup_package_outputs(PACKAGE_999)
        _cleanup_reference()


if __name__ == "__main__":
    main()
