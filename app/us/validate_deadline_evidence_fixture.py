from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import uuid

from app.config import get_settings
from app.db import clickhouse_client, postgres_conn
from app.us.deadline_evidence import resolve_case_deadline_evidence
from app.us.deadline_portfolio import scan_deadline_candidate_page
from app.us.event_reference import (
    AUTHORITY as EVENT_AUTHORITY,
    CURRENT_OFFICIAL_SOURCE_PAGE_URL,
    REFERENCE_KIND as EVENT_REFERENCE_KIND,
    REFERENCE_PAYLOAD_SCHEMA as EVENT_REFERENCE_SCHEMA,
    import_reference_payload as import_event_reference,
)
from app.us.event_roles import EVENT_ROLE_PAYLOAD_SCHEMA, import_event_role_ruleset
from app.us.ingest import _cleanup_package_outputs
from app.us.model import USCaseBundle, USCaseRecord, USEventRecord
from app.us.publisher import USBatchPublisher
from app.us.reference_evidence import sha256_file


EVENT_REFERENCE_VERSION = "USPTO_EVENT_CODES_CI_DEADLINE_V1"
ROLE_RULESET_VERSION = "MARKORBIT_EVENT_ROLES_CI_DEADLINE_V1"
PACKAGE_ID = uuid.UUID("99999999-9999-9999-9999-999999999971")
SERIAL_A = "88993011"
SERIAL_B = "88993012"


def _write(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return sha256_file(path)


def _event_reference_payload(source_name: str, source_sha: str) -> dict:
    records = []
    for code in ("NRAP", "ROA", "NOA", "EXT", "SOU", "OP90"):
        records.append(
            {
                "code": code,
                "official_description": f"CI synthetic official event {code}",
                "official_definition": "",
                "official_category": "",
                "source_locator": f"CI-{code}",
            }
        )
    return {
        "schema": EVENT_REFERENCE_SCHEMA,
        "authority": EVENT_AUTHORITY,
        "reference_kind": EVENT_REFERENCE_KIND,
        "reference_version": EVENT_REFERENCE_VERSION,
        "source": {
            "document_name": source_name,
            "document_date": "2025-08-13",
            "url": CURRENT_OFFICIAL_SOURCE_PAGE_URL,
            "sha256": source_sha,
            "evidence_note": "CI synthetic event descriptions; mechanics only.",
        },
        "records": records,
    }


def _role_payload(source_name: str, source_sha: str) -> dict:
    mappings = (
        ("OA", "NRAP", "OFFICE_ACTION_NONFINAL_ISSUED"),
        ("ROA", "ROA", "OFFICE_ACTION_RESPONSE_FILED"),
        ("NOA", "NOA", "NOTICE_OF_ALLOWANCE_ISSUED"),
        ("EXT", "EXT", "ITU_EXTENSION_GRANTED"),
        ("SOU", "SOU", "STATEMENT_OF_USE_FILED"),
        ("OP90", "OP90", "OPPOSITION_EXTENSION_90_GRANTED"),
    )
    return {
        "schema": EVENT_ROLE_PAYLOAD_SCHEMA,
        "ruleset_version": ROLE_RULESET_VERSION,
        "event_reference_version": EVENT_REFERENCE_VERSION,
        "source": {
            "document_name": source_name,
            "sha256": source_sha,
            "evidence_note": "CI synthetic reviewed-role memo; never production law.",
        },
        "rules": [
            {
                "rule_id": rule_id,
                "event_code": code,
                "role": role,
                "rationale": "CI-only reviewed mapping used to prove evidence mechanics.",
                "source_refs": [f"CI reviewed row {rule_id}"],
            }
            for rule_id, code, role in mappings
        ],
    }


def _publish() -> None:
    publisher = USBatchPublisher(
        clickhouse_client(),
        package_id=PACKAGE_ID,
        package_kind="DAILY_APPLICATIONS",
        source_effective_date=date(2026, 8, 20),
        source_rank=9_000_000_201,
        batch_size=100,
    )
    publisher.add(
        USCaseBundle(
            case=USCaseRecord(
                serial_number=SERIAL_A,
                filing_date=date(2020, 1, 1),
                publication_date=date(2026, 8, 1),
                registration_number="7000011",
                registration_date=date(2020, 10, 15),
                status_code="700",
                status_date=date(2026, 8, 20),
                mark_identification="DEADLINE EVIDENCE A",
            ),
            events=(
                USEventRecord(
                    serial_number=SERIAL_A,
                    event_code="NRAP",
                    event_date=date(2026, 7, 20),
                    event_sequence=1,
                    description_text="CI synthetic OA issue",
                ),
            ),
        ),
        "deadline-evidence-a.xml",
    )
    publisher.add(
        USCaseBundle(
            case=USCaseRecord(
                serial_number=SERIAL_B,
                filing_date=date(2025, 1, 1),
                status_code="700",
                status_date=date(2026, 8, 20),
                mark_identification="DEADLINE EVIDENCE B",
                intent_to_use_1b=True,
                intent_to_use_1b_filed=True,
                intent_to_use_1b_current=True,
            ),
            events=(
                USEventRecord(
                    serial_number=SERIAL_B,
                    event_code="NOA",
                    event_date=date(2026, 3, 1),
                    event_sequence=1,
                    description_text="CI synthetic NOA",
                ),
                USEventRecord(
                    serial_number=SERIAL_B,
                    event_code="EXT",
                    event_date=date(2026, 8, 15),
                    event_sequence=2,
                    description_text="CI synthetic ITU extension grant",
                ),
            ),
        ),
        "deadline-evidence-b.xml",
    )
    publisher.close()


def _cleanup_reference_rows() -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM interpretation.us_event_role_rule WHERE ruleset_version = %s",
                (ROLE_RULESET_VERSION,),
            )
            cur.execute(
                "DELETE FROM interpretation.us_event_role_ruleset_version WHERE ruleset_version = %s",
                (ROLE_RULESET_VERSION,),
            )
            cur.execute(
                "DELETE FROM reference.us_trademark_event_code WHERE reference_version = %s",
                (EVENT_REFERENCE_VERSION,),
            )
            cur.execute(
                "DELETE FROM reference.us_trademark_event_reference_version WHERE reference_version = %s",
                (EVENT_REFERENCE_VERSION,),
            )
        conn.commit()


def main() -> None:
    raw_root = get_settings().raw_data_root
    event_source = raw_root / "reference" / "us" / "event" / "CI_DEADLINE_EVENT_SOURCE.bin"
    role_source = (
        raw_root
        / "reference"
        / "us"
        / "interpretation"
        / "CI_DEADLINE_EVENT_ROLE_REVIEW.md"
    )
    paths = [event_source, role_source]
    try:
        _cleanup_package_outputs(PACKAGE_ID)
        _cleanup_reference_rows()
        event_sha = _write(event_source, b"ci-deadline-event-reference-source")
        role_sha = _write(role_source, b"ci-reviewed-event-role-evidence")
        event_import = import_event_reference(
            _event_reference_payload(event_source.name, event_sha), activate=True
        )
        role_import = import_event_role_ruleset(
            _role_payload(role_source.name, role_sha), activate=True
        )
        _publish()

        evidence_a = resolve_case_deadline_evidence(
            serial_number=SERIAL_A,
            raw_root=raw_root,
        )
        if evidence_a["status"] != "PASS":
            raise RuntimeError(f"Event-role evidence did not pass: {evidence_a}")
        if evidence_a["automatic_inputs"]["office_action_issue_date"] != date(
            2026, 7, 20
        ):
            raise RuntimeError(f"OA date was not resolved: {evidence_a}")

        evidence_b = resolve_case_deadline_evidence(
            serial_number=SERIAL_B,
            raw_root=raw_root,
        )
        if evidence_b["automatic_inputs"]["notice_of_allowance_date"] != date(
            2026, 3, 1
        ):
            raise RuntimeError(f"NOA date was not resolved: {evidence_b}")
        if evidence_b["automatic_inputs"]["itu_extensions_granted"] != 1:
            raise RuntimeError(f"ITU extension count was not resolved: {evidence_b}")

        page = scan_deadline_candidate_page(
            raw_root=raw_root,
            as_of=date(2026, 8, 20),
            after_serial="88993010",
            scan_limit=10,
            result_limit=5000,
            horizon_days=365,
            recent_past_days=30,
        )
        candidate_codes = {row["code"] for row in page["candidates"]}
        expected = {
            "SECTION_8_FIRST",
            "OPPOSITION_PERIOD",
            "NONFINAL_OFFICE_ACTION_RESPONSE",
            "ITU_SOU_OR_EXTENSION",
        }
        if not expected.issubset(candidate_codes):
            raise RuntimeError(
                f"Portfolio deadline candidates missing {sorted(expected - candidate_codes)}: {page}"
            )
        if any(row["legal_status_inference"] for row in page["candidates"]):
            raise RuntimeError("Deadline portfolio fixture produced legal-status inference")

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "contract": "US_REVIEWED_EVENT_DEADLINE_EVIDENCE_FIXTURE",
                    "event_reference_import": event_import["status"],
                    "event_role_ruleset_import": role_import["status"],
                    "office_action_date_auto_resolved": "PASS",
                    "notice_of_allowance_date_auto_resolved": "PASS",
                    "itu_extension_grant_count_auto_resolved": "PASS",
                    "portfolio_candidates": sorted(candidate_codes),
                    "legal_status_inference": "NONE",
                    "unknown_event_code_guessing": "NONE",
                    "production_event_role_mappings_shipped": 0,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    finally:
        _cleanup_package_outputs(PACKAGE_ID)
        _cleanup_reference_rows()
        for path in paths:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
