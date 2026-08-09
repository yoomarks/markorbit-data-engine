from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import uuid

from app.config import get_settings
from app.db import clickhouse_client, postgres_conn
from app.us.event_reference import (
    AUTHORITY as EVENT_AUTHORITY,
    CURRENT_OFFICIAL_SOURCE_PAGE_URL,
    REFERENCE_KIND as EVENT_REFERENCE_KIND,
    REFERENCE_PAYLOAD_SCHEMA as EVENT_REFERENCE_PAYLOAD_SCHEMA,
    event_reference_schema_ready,
    import_reference_payload as import_event_reference,
)
from app.us.ingest import _cleanup_package_outputs
from app.us.model import USCaseBundle, USCaseRecord, USEventRecord
from app.us.publisher import USBatchPublisher
from app.us.reference_acceptance import build_reference_acceptance
from app.us.reference_evidence import sha256_file
from app.us.status_interpretation import (
    RULESET_PAYLOAD_SCHEMA,
    import_ruleset,
    interpret_status,
)
from app.us.status_reference import (
    AUTHORITY as STATUS_AUTHORITY,
    CURRENT_XML_RESOURCES_URL,
    REFERENCE_KIND as STATUS_REFERENCE_KIND,
    REFERENCE_PAYLOAD_SCHEMA as STATUS_REFERENCE_PAYLOAD_SCHEMA,
    import_reference_payload as import_status_reference,
    status_reference_schema_ready,
)


STATUS_VERSION = "USPTO_STATUS_CODES_CI_SEMANTIC_V1"
EVENT_VERSION = "USPTO_EVENT_CODES_CI_SEMANTIC_V1"
RULESET_VERSION = "MARKORBIT_US_STATUS_RULES_CI_V1"
CONFLICT_RULESET_VERSION = "MARKORBIT_US_STATUS_RULES_CI_CONFLICT"
PACKAGE_ID = uuid.UUID("99999999-9999-9999-9999-999999999981")
SERIAL = "88992001"


def _write(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return sha256_file(path)


def _status_payload(document_name: str, source_sha: str) -> dict:
    return {
        "schema": STATUS_REFERENCE_PAYLOAD_SCHEMA,
        "authority": STATUS_AUTHORITY,
        "reference_kind": STATUS_REFERENCE_KIND,
        "reference_version": STATUS_VERSION,
        "source": {
            "document_name": document_name,
            "document_date": "2025-08-13",
            "url": CURRENT_XML_RESOURCES_URL,
            "sha256": source_sha,
            "evidence_note": "CI synthetic mapping text; evidence mechanics only.",
        },
        "records": [
            {
                "code": "700",
                "official_description": "CI synthetic official status 700",
                "official_definition": "",
                "official_category": "",
                "source_locator": "CI-status-row-1",
            }
        ],
    }


def _event_payload(document_name: str, source_sha: str) -> dict:
    return {
        "schema": EVENT_REFERENCE_PAYLOAD_SCHEMA,
        "authority": EVENT_AUTHORITY,
        "reference_kind": EVENT_REFERENCE_KIND,
        "reference_version": EVENT_VERSION,
        "source": {
            "document_name": document_name,
            "document_date": "2025-08-13",
            "url": CURRENT_OFFICIAL_SOURCE_PAGE_URL,
            "sha256": source_sha,
            "evidence_note": "CI synthetic mapping text; evidence mechanics only.",
        },
        "records": [
            {
                "code": "NEWAP",
                "official_description": "CI synthetic official event NEWAP",
                "official_definition": "",
                "official_category": "",
                "source_locator": "CI-event-row-1",
            }
        ],
    }


def _ruleset_payload(document_name: str, source_sha: str, *, conflict: bool = False) -> dict:
    rules = [
        {
            "rule_id": "CI_STATUS_EVENT_MATCH",
            "priority": 100,
            "status_codes": ["700"],
            "event_codes_any": ["NEWAP"],
            "event_codes_all": [],
            "result_label": "CI_SYNTHETIC_MATCH",
            "confidence": "HIGH",
            "rationale": "CI-only rule used to prove evidence-bound interpretation mechanics.",
            "source_refs": ["CI synthetic source reference"],
        }
    ]
    if conflict:
        rules.append(
            {
                "rule_id": "CI_STATUS_EVENT_CONFLICT",
                "priority": 100,
                "status_codes": ["700"],
                "event_codes_any": ["NEWAP"],
                "event_codes_all": [],
                "result_label": "CI_SYNTHETIC_CONFLICT",
                "confidence": "HIGH",
                "rationale": "CI-only conflicting rule used to prove UNKNOWN fail-closed behavior.",
                "source_refs": ["CI synthetic conflict source reference"],
            }
        )
    return {
        "schema": RULESET_PAYLOAD_SCHEMA,
        "ruleset_version": CONFLICT_RULESET_VERSION if conflict else RULESET_VERSION,
        "status_reference_version": STATUS_VERSION,
        "event_reference_version": EVENT_VERSION,
        "source": {
            "document_name": document_name,
            "sha256": source_sha,
            "evidence_note": "CI synthetic interpretation evidence; never production law.",
        },
        "rules": rules,
    }


def _publish_case() -> None:
    publisher = USBatchPublisher(
        clickhouse_client(),
        package_id=PACKAGE_ID,
        package_kind="DAILY_APPLICATIONS",
        source_effective_date=date(2026, 8, 9),
        source_rank=9_000_000_101,
        batch_size=100,
    )
    publisher.add(
        USCaseBundle(
            case=USCaseRecord(
                serial_number=SERIAL,
                filing_date=date(2026, 8, 1),
                status_code="700",
                status_date=date(2026, 8, 9),
                mark_identification="SEMANTIC REFERENCE FIXTURE",
            ),
            events=(
                USEventRecord(
                    serial_number=SERIAL,
                    event_code="NEWAP",
                    event_date=date(2026, 8, 9),
                    event_sequence=1,
                    description_text="CI synthetic event",
                ),
            ),
        ),
        "semantic-reference.xml",
    )
    publisher.close()


def _cleanup_db() -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM interpretation.us_status_rule WHERE ruleset_version IN (%s, %s)",
                (RULESET_VERSION, CONFLICT_RULESET_VERSION),
            )
            cur.execute(
                "DELETE FROM interpretation.us_status_ruleset_version WHERE ruleset_version IN (%s, %s)",
                (RULESET_VERSION, CONFLICT_RULESET_VERSION),
            )
            cur.execute(
                "DELETE FROM reference.us_trademark_event_code WHERE reference_version = %s",
                (EVENT_VERSION,),
            )
            cur.execute(
                "DELETE FROM reference.us_trademark_event_reference_version WHERE reference_version = %s",
                (EVENT_VERSION,),
            )
            cur.execute(
                "DELETE FROM reference.us_trademark_status_code WHERE reference_version = %s",
                (STATUS_VERSION,),
            )
            cur.execute(
                "DELETE FROM reference.us_trademark_status_reference_version WHERE reference_version = %s",
                (STATUS_VERSION,),
            )
        conn.commit()


def main() -> None:
    if not status_reference_schema_ready():
        raise RuntimeError("USPTO status reference schema is not ready")
    if not event_reference_schema_ready():
        raise RuntimeError("USPTO event reference schema is not ready")

    raw_root = get_settings().raw_data_root
    status_path = raw_root / "reference" / "us" / "status" / "CI_STATUS_REFERENCE_SOURCE.bin"
    event_path = raw_root / "reference" / "us" / "event" / "CI_EVENT_REFERENCE_SOURCE.bin"
    rules_path = raw_root / "reference" / "us" / "interpretation" / "CI_STATUS_RULESET_EVIDENCE.txt"
    conflict_path = raw_root / "reference" / "us" / "interpretation" / "CI_STATUS_RULESET_CONFLICT.txt"
    fixture_paths = [status_path, event_path, rules_path, conflict_path]

    try:
        _cleanup_package_outputs(PACKAGE_ID)
        _cleanup_db()

        status_sha = _write(status_path, b"ci-status-reference-source-v1")
        event_sha = _write(event_path, b"ci-event-reference-source-v1")
        rules_sha = _write(rules_path, b"ci-status-ruleset-evidence-v1")
        conflict_sha = _write(conflict_path, b"ci-status-ruleset-conflict-v1")

        status_import = import_status_reference(
            _status_payload(status_path.name, status_sha), activate=True
        )
        event_import = import_event_reference(
            _event_payload(event_path.name, event_sha), activate=True
        )
        _publish_case()

        reference_acceptance = build_reference_acceptance(raw_root)
        if reference_acceptance["status"] != "PASS":
            raise RuntimeError(f"Reference acceptance did not pass: {reference_acceptance}")

        rules_import = import_ruleset(
            _ruleset_payload(rules_path.name, rules_sha), activate=True
        )
        matched = interpret_status(
            raw_root=raw_root,
            status_code="700",
            event_codes=["NEWAP"],
        )
        if matched["result"] != "CI_SYNTHETIC_MATCH":
            raise RuntimeError(f"Synthetic interpretation did not match: {matched}")

        no_match = interpret_status(raw_root=raw_root, status_code="700", event_codes=[])
        if no_match["result"] != "UNKNOWN" or no_match["reason"] != "no_matching_evidence_rule":
            raise RuntimeError(f"No-match interpretation did not fail closed: {no_match}")

        import_ruleset(
            _ruleset_payload(conflict_path.name, conflict_sha, conflict=True), activate=True
        )
        conflict = interpret_status(
            raw_root=raw_root,
            status_code="700",
            event_codes=["NEWAP"],
        )
        if conflict["result"] != "UNKNOWN" or conflict["reason"] != "conflicting_top_priority_rules":
            raise RuntimeError(f"Conflict did not fail closed: {conflict}")

        import_ruleset(_ruleset_payload(rules_path.name, rules_sha), activate=True)
        rules_path.write_bytes(b"tampered-ruleset-evidence")
        tampered_rules = interpret_status(
            raw_root=raw_root,
            status_code="700",
            event_codes=["NEWAP"],
        )
        if tampered_rules["result"] != "UNKNOWN" or tampered_rules["reason"] != "ruleset_evidence_not_verified":
            raise RuntimeError(f"Ruleset evidence tamper did not fail closed: {tampered_rules}")
        _write(rules_path, b"ci-status-ruleset-evidence-v1")

        status_path.write_bytes(b"tampered-status-reference")
        tampered_reference = build_reference_acceptance(raw_root)
        if tampered_reference["status"] != "FAIL":
            raise RuntimeError(f"Reference source tamper did not fail acceptance: {tampered_reference}")
        interpretation_with_tampered_reference = interpret_status(
            raw_root=raw_root,
            status_code="700",
            event_codes=["NEWAP"],
        )
        if (
            interpretation_with_tampered_reference["result"] != "UNKNOWN"
            or interpretation_with_tampered_reference["reason"]
            != "official_reference_evidence_not_verified"
        ):
            raise RuntimeError(
                "Official reference tamper did not fail interpretation closed: "
                f"{interpretation_with_tampered_reference}"
            )
        _write(status_path, b"ci-status-reference-source-v1")

        restored_acceptance = build_reference_acceptance(raw_root)
        if restored_acceptance["status"] != "PASS":
            raise RuntimeError(f"Restored reference evidence did not pass: {restored_acceptance}")

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "contract": "US_SEMANTIC_REFERENCE_AND_INTERPRETATION_FIXTURE",
                    "status_reference_import": status_import["status"],
                    "event_reference_import": event_import["status"],
                    "reference_acceptance": restored_acceptance["status"],
                    "ruleset_import": rules_import["status"],
                    "unique_rule_match": "PASS",
                    "no_match_unknown": "PASS",
                    "conflicting_rules_unknown": "PASS",
                    "ruleset_evidence_tamper_unknown": "PASS",
                    "official_reference_tamper_fail_closed": "PASS",
                    "production_legal_rules_shipped": 0,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    finally:
        _cleanup_package_outputs(PACKAGE_ID)
        _cleanup_db()
        for path in fixture_paths:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
