from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.us.event_reference import active_reference_metadata as active_event_reference
from app.us.event_reference_inventory import build_inventory as build_event_inventory
from app.us.reference_evidence import verify_source_evidence
from app.us.status_reference import active_reference_metadata as active_status_reference
from app.us.status_reference_inventory import build_inventory as build_status_inventory


ACCEPTANCE_VERSION = "US_OFFICIAL_REFERENCE_ACCEPTANCE_V1"


def _evaluate_family(
    *,
    family: str,
    metadata: dict[str, Any] | None,
    inventory: dict[str, Any],
    raw_root: Path,
) -> dict[str, Any]:
    evidence = verify_source_evidence(metadata, raw_root, family=family)
    unmapped_count = int(inventory.get("unmapped_code_count") or 0)

    if evidence["status"] == "FAIL":
        status = "FAIL"
        reasons = [str(evidence["reason"])]
    elif metadata is None:
        status = "NOT_READY"
        reasons = [f"active_{family}_reference_missing"]
    elif evidence["status"] != "PASS":
        status = "NOT_READY"
        reasons = [str(evidence["reason"])]
    elif unmapped_count:
        status = "NOT_READY"
        reasons = [f"observed_{family}_codes_unmapped"]
    else:
        status = "PASS"
        reasons = []

    return {
        "family": family,
        "status": status,
        "reason_codes": reasons,
        "reference": metadata,
        "evidence": evidence,
        "inventory": inventory,
    }


def evaluate_reference_acceptance(
    *,
    raw_root: Path,
    status_metadata: dict[str, Any] | None,
    status_inventory: dict[str, Any],
    event_metadata: dict[str, Any] | None,
    event_inventory: dict[str, Any],
) -> dict[str, Any]:
    status = _evaluate_family(
        family="status",
        metadata=status_metadata,
        inventory=status_inventory,
        raw_root=raw_root,
    )
    event = _evaluate_family(
        family="event",
        metadata=event_metadata,
        inventory=event_inventory,
        raw_root=raw_root,
    )
    statuses = {status["status"], event["status"]}
    if "FAIL" in statuses:
        overall = "FAIL"
    elif statuses == {"PASS"}:
        overall = "PASS"
    else:
        overall = "NOT_READY"

    return {
        "acceptance_version": ACCEPTANCE_VERSION,
        "status": overall,
        "semantics": "OFFICIAL_REFERENCE_EVIDENCE_NOT_LEGAL_INTERPRETATION",
        "status_reference": status,
        "event_reference": event,
    }


def build_reference_acceptance(raw_root: Path) -> dict[str, Any]:
    return evaluate_reference_acceptance(
        raw_root=raw_root,
        status_metadata=active_status_reference(),
        status_inventory=build_status_inventory(),
        event_metadata=active_event_reference(),
        event_inventory=build_event_inventory(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only source-backed USPTO status/event reference acceptance"
    )
    parser.add_argument("--raw-root", type=Path, default=None)
    args = parser.parse_args()
    raw_root = args.raw_root or get_settings().raw_data_root
    print(json.dumps(build_reference_acceptance(raw_root), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
