from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from app.us import reset_rebuild_core as _core


_core.ALL_TABLE_KEYS["us_case_observation_history"] = "observation_key"
_core.RESET_VERSION = "US_CLEAN_REBUILD_RESET_V2"
_core.RESET_CONFIRMATION = "RESET-US-M1.4"
_original_build_reset_plan = _core.build_reset_plan


def _m14_build_reset_plan(
    raw_root: Path,
    *,
    expected_history_parts: int,
    deep_source_test: bool = False,
    registry_rows: list[dict[str, Any]] | None = None,
    table_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    result = _original_build_reset_plan(
        raw_root,
        expected_history_parts=expected_history_parts,
        deep_source_test=deep_source_test,
        registry_rows=registry_rows,
        table_counts=table_counts,
    )
    result["reset_version"] = _core.RESET_VERSION
    result["policy_note"] = (
        "Clean rebuild reset preserves US package identities and truncates all current, "
        "event-history, and durable case-observation tables before deterministic replay. "
        "Registered source-plan rows return to REGISTERED; CN data is out of scope."
    )
    return result


_core.build_reset_plan = _m14_build_reset_plan

# Preserve module identity so monkeypatches and CLI globals hit the exact core module
# used by apply_reset/main, while the M1.4 table/version policy above remains active.
sys.modules[__name__] = _core

if __name__ == "__main__":
    _core.main()
