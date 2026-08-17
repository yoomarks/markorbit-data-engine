from __future__ import annotations

import json

from app.contact_ingest.country_inference import run_country_inference
from app.contact_ingest.country_inference_run_audit import audit_persisted_run
from app.contact_ingest.validate_country_inference_fixture import seed_fixture


def validate() -> dict[str, object]:
    seed_fixture()
    preview = run_country_inference(apply=False, batch_size=50)
    report = audit_persisted_run(str(preview["run_id"]))

    assert preview["status"] == "SUCCESS"
    assert preview["apply"] is False
    assert report["status"] == "SUCCESS"
    assert report["apply_mode"] is False
    assert report["persisted_rows"] == preview["evaluated"]
    assert report["status_counts"]["ACCEPTED"] == preview["accepted"]
    assert report["status_counts"]["CONFLICT"] == preview["conflict"]
    assert report["accepted_country_counts"]["GB"] == 2
    assert report["accepted_country_counts"]["AU"] == 2
    assert report["integrity_checks"]["persisted_rows_match_evaluated"] is True
    assert report["integrity_checks"]["accepted_rows_match_metrics"] is True
    assert report["integrity_checks"]["accepted_below_run_threshold"] == 0
    assert report["integrity_checks"]["already_applied_rows"] == 0
    assert report["integrity_checks"]["accepted_with_source_country_now"] == 0
    assert report["activation_candidate_rows"] == preview["accepted"]
    assert report["activation_integrity_ready"] is True
    assert "RAW_EXPLICIT_COUNTRY_FIELD" in report["accepted_evidence_kind_entities"]
    assert "INTERNATIONAL_PHONE" in report["accepted_evidence_kind_entities"]
    assert report["conflict_pairs_top30"]["GB>DE"] == 1

    return {
        "status": "PASS",
        "run_id": preview["run_id"],
        "persisted_rows": report["persisted_rows"],
        "status_counts": report["status_counts"],
        "activation_candidate_rows": report["activation_candidate_rows"],
        "activation_integrity_ready": report["activation_integrity_ready"],
    }


def main() -> int:
    print(json.dumps(validate(), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
