from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.us.pipeline_readiness import build_readiness
from app.us.reference_acceptance import build_reference_acceptance


SEMANTIC_READINESS_VERSION = "US_SEMANTIC_READINESS_V1"


def evaluate_semantic_readiness(
    *,
    pipeline: dict[str, Any],
    references: dict[str, Any],
) -> dict[str, Any]:
    if pipeline.get("state") != "ACCEPTED" or pipeline.get("ready") is not True:
        return {
            "semantic_readiness_version": SEMANTIC_READINESS_VERSION,
            "state": "DATA_CORPUS_NOT_ACCEPTED",
            "ready_for_rule_research": False,
            "reason_codes": [str(pipeline.get("state") or "pipeline_not_accepted")],
            "pipeline": pipeline,
            "references": references,
            "legal_interpretation_produced": False,
        }

    reference_status = str(references.get("status") or "")
    if reference_status == "FAIL":
        state = "REFERENCE_EVIDENCE_FAILED"
    elif reference_status != "PASS":
        state = "OFFICIAL_REFERENCES_NOT_READY"
    else:
        state = "READY_FOR_RULE_RESEARCH"

    return {
        "semantic_readiness_version": SEMANTIC_READINESS_VERSION,
        "state": state,
        "ready_for_rule_research": state == "READY_FOR_RULE_RESEARCH",
        "reason_codes": (
            []
            if state == "READY_FOR_RULE_RESEARCH"
            else [
                *references.get("status_reference", {}).get("reason_codes", []),
                *references.get("event_reference", {}).get("reason_codes", []),
            ]
        ),
        "pipeline": pipeline,
        "references": references,
        "legal_interpretation_produced": False,
    }


def build_semantic_readiness(
    raw_root: Path,
    *,
    expected_history_parts: int,
    deep_source_test: bool = False,
) -> dict[str, Any]:
    pipeline = build_readiness(
        raw_root,
        expected_history_parts=expected_history_parts,
        deep_source_test=deep_source_test,
        verify_source_files=True,
    )
    references = build_reference_acceptance(raw_root)
    return evaluate_semantic_readiness(pipeline=pipeline, references=references)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only US semantic readiness gate; produces no legal interpretation"
    )
    parser.add_argument("--expected-history-parts", type=int, required=True)
    parser.add_argument("--deep-source-test", action="store_true")
    args = parser.parse_args()
    if args.expected_history_parts < 1:
        parser.error("--expected-history-parts must be at least 1")
    report = build_semantic_readiness(
        get_settings().raw_data_root,
        expected_history_parts=args.expected_history_parts,
        deep_source_test=args.deep_source_test,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
