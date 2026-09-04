from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.us.migrations import US_SCHEMA_VERSION
from app.us.package_meta import infer_us_package_descriptor
from app.us.replay_executor import build_replay_plan
from app.us.source_preflight import build_preflight
from app.us.target_canary_review import (
    ACCEPTED_PILOT_EVIDENCE_REF,
    ACCEPTED_PILOT_REGISTRY_ID,
    PILOT_FILE_NAME,
    PILOT_SEQUENCE,
    PILOT_SHA256,
    STAGE1_REGISTRY_BASIS,
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def build_stage1_replay_plan(
    raw_root: Path,
    *,
    expected_history_parts: int,
    deep_source_test: bool = False,
) -> dict[str, Any]:
    """Build #526 Stage 1 continuity from accepted pilot evidence, never live registry state.

    Stage 1 is a read-only review lane. The already-accepted #340 pilot is the
    sole SUCCESS prefix authority; the current source corpus must independently
    reproduce that exact file/SHA identity before deterministic sequence 2 can
    be reviewed. This function deliberately performs no Postgres/Docker access.
    """
    preflight = build_preflight(
        raw_root,
        expected_history_parts=expected_history_parts,
        deep_source_test=deep_source_test,
    )
    _require(
        bool(preflight.get("safe_to_replay")),
        "Stage 1 source preflight is not safe to replay",
    )

    source_plan = preflight.get("replay_plan") or []
    _require(
        isinstance(source_plan, list) and len(source_plan) >= 2,
        "Stage 1 source plan must contain pilot and package 2",
    )
    pilot = source_plan[0]
    _require(isinstance(pilot, dict), "Stage 1 pilot source row is not an object")
    _require(
        int(pilot.get("sequence") or 0) == PILOT_SEQUENCE,
        "Accepted pilot sequence drifted from 1",
    )
    _require(
        str(pilot.get("file_name") or "") == PILOT_FILE_NAME,
        "Accepted pilot file identity drifted",
    )
    _require(
        str(pilot.get("sha256") or "").lower() == PILOT_SHA256,
        "Accepted pilot SHA-256 identity drifted",
    )

    pilot_path = Path(str(pilot.get("path") or ""))
    descriptor = infer_us_package_descriptor(pilot_path)
    _require(descriptor.package_kind != "UNKNOWN", "Accepted pilot descriptor is UNKNOWN")
    _require(
        descriptor.package_kind == str(pilot.get("package_kind") or ""),
        "Accepted pilot package kind drifted",
    )
    _require(
        descriptor.partition_value == str(pilot.get("partition_value") or ""),
        "Accepted pilot partition identity drifted",
    )

    accepted_pilot_row = {
        "package_id": ACCEPTED_PILOT_REGISTRY_ID,
        "package_sequence": PILOT_SEQUENCE,
        "file_name": PILOT_FILE_NAME,
        "sha256": PILOT_SHA256,
        "package_kind": descriptor.package_kind,
        "partition_dimension": descriptor.partition_dimension,
        "partition_value": descriptor.partition_value,
        "source_rank": descriptor.source_rank(PILOT_SEQUENCE),
        "status": "SUCCESS",
        "profile": {
            "source_sha256": PILOT_SHA256,
            "totals": {"schema_version": US_SCHEMA_VERSION},
        },
        "schema_version": US_SCHEMA_VERSION,
    }

    plan = build_replay_plan(
        raw_root,
        expected_history_parts=expected_history_parts,
        deep_source_test=deep_source_test,
        registry_rows=[accepted_pilot_row],
        source_preflight=preflight,
    )
    plan["mode"] = "DRY_RUN"
    plan["registry_basis"] = STAGE1_REGISTRY_BASIS
    plan["live_registry_read"] = False
    plan["accepted_pilot_evidence"] = {
        "reference": ACCEPTED_PILOT_EVIDENCE_REF,
        "sequence": PILOT_SEQUENCE,
        "file_name": PILOT_FILE_NAME,
        "sha256": PILOT_SHA256,
        "current_path": str(pilot_path),
    }
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build #526 Stage 1 read-only replay continuity from accepted pilot evidence"
    )
    parser.add_argument("--expected-history-parts", type=int, required=True)
    parser.add_argument("--deep-source-test", action="store_true")
    args = parser.parse_args()

    raw_root = Path(get_settings().raw_data_root)
    result = build_stage1_replay_plan(
        raw_root,
        expected_history_parts=args.expected_history_parts,
        deep_source_test=args.deep_source_test,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
