from __future__ import annotations

import sys
from typing import Any

from app.us import audit_real_data_v2_core as _core


_core.AUDIT_VERSION = "US_M14_REAL_DATA_ACCEPTANCE_V2_HISTORY_PARTS"
_original_augment_report = _core.augment_report


def _m14_augment_report(
    report: dict[str, Any],
    packages: list[dict[str, Any]],
    *,
    expected_history_parts: int | None = None,
) -> dict[str, Any]:
    result = _original_augment_report(
        report,
        packages,
        expected_history_parts=expected_history_parts,
    )
    result["audit_version"] = _core.AUDIT_VERSION
    result["acceptance_note"] = (
        "Strict US M1.4 acceptance checks historical coverage-part continuity from part 01, "
        "requires an explicitly pinned trailing part count, and retains the base M1.4 durable "
        "case-observation integrity/lineage gates. Any leading/interior or pinned-tail gap is "
        "NOT_READY rather than silently inferred complete."
    )
    return result


_core.augment_report = _m14_augment_report
if __name__ == "__main__":
    _core.main()
else:
    sys.modules[__name__] = _core
