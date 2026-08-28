from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from app.cn.research_filing_to_prelim_duration import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_MAX_ROWS,
    DurationDatasetMaterialization,
    build_live_materialization,
)
from app.research_dataset import replay_matches


RECEIPT_VERSION = "CN_FILING_TO_PRELIM_RESEARCH_ACCEPTANCE_V1"
DEFAULT_REPLAY_BATCH_SIZE = 1_000
_GIT_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")


def _resolve_engine_sha(explicit: str | None = None) -> str:
    candidate = (
        explicit
        or os.getenv("MARKORBIT_DATA_ENGINE_SHA")
        or os.getenv("GITHUB_SHA")
        or ""
    ).strip()
    if candidate.startswith("git:"):
        candidate = candidate[4:]
    if not _GIT_SHA_RE.fullmatch(candidate):
        raise ValueError(
            "exact 40-character Data Engine git SHA is required via --engine-sha, "
            "MARKORBIT_DATA_ENGINE_SHA, or GITHUB_SHA"
        )
    return candidate.lower()


def _assert_materialization_contract(
    materialization: DurationDatasetMaterialization,
    *,
    engine_sha: str,
) -> None:
    dataset_ref = materialization.dataset_ref
    expected_engine_version = f"git:{engine_sha}"
    if dataset_ref.engine_version != expected_engine_version:
        raise RuntimeError(
            "research dataset engine identity mismatch: "
            f"expected={expected_engine_version} actual={dataset_ref.engine_version}"
        )
    if dataset_ref.row_count <= 0:
        raise RuntimeError("research acceptance requires at least one factual row")
    if materialization.valid_rows + materialization.invalid_date_order_rows != dataset_ref.row_count:
        raise RuntimeError("research quality counts do not reconcile to dataset row_count")
    if dataset_ref.query.get("legal_conclusion") is not False:
        raise RuntimeError("research acceptance rejects legal-conclusion output")
    if dataset_ref.query.get("actionability") != "SOURCE_FACT_ONLY":
        raise RuntimeError("research acceptance requires SOURCE_FACT_ONLY actionability")
    if dataset_ref.query.get("replay_scope") != "QUIESCENT_CURRENT_SERVING_EPOCH":
        raise RuntimeError("research acceptance requires quiescent serving-epoch replay scope")
    if dataset_ref.query.get("historic_as_of_reconstruction") is not False:
        raise RuntimeError("research acceptance must not claim historic as-of reconstruction")


def build_acceptance_receipt(
    first: DurationDatasetMaterialization,
    replay: DurationDatasetMaterialization,
    *,
    engine_sha: str,
    max_rows: int,
    first_batch_size: int,
    replay_batch_size: int,
) -> dict[str, Any]:
    normalized_sha = _resolve_engine_sha(engine_sha)
    if max_rows <= 0:
        raise ValueError("max_rows must be positive")
    if first_batch_size <= 0 or replay_batch_size <= 0:
        raise ValueError("batch sizes must be positive")

    _assert_materialization_contract(first, engine_sha=normalized_sha)
    _assert_materialization_contract(replay, engine_sha=normalized_sha)

    first_ref = first.dataset_ref
    replay_ref = replay.dataset_ref
    if not replay_matches(first_ref, replay_ref):
        raise RuntimeError(
            "research replay mismatch: dataset/query identity, row count, or integrity drifted"
        )

    return {
        "receipt_version": RECEIPT_VERSION,
        "status": "PASS",
        "redacted": True,
        "objective_only": True,
        "data_engine_sha": normalized_sha,
        "engine_version": first_ref.engine_version,
        "dataset_ref_id": first_ref.dataset_ref_id,
        "query_fingerprint_sha256": first_ref.query_fingerprint_sha256,
        "row_count": first_ref.row_count,
        "integrity_sha256": first_ref.integrity_sha256,
        "watermark": first_ref.watermark,
        "completeness": first_ref.completeness,
        "valid_rows": first.valid_rows,
        "invalid_date_order_rows": first.invalid_date_order_rows,
        "replay_match": True,
        "first_batch_size": first_batch_size,
        "replay_batch_size": replay_batch_size,
        "physical_batch_size_in_identity": False,
        "max_rows": max_rows,
        "population_scope": "DETERMINISTIC_ORDERED_PREFIX",
        "replay_scope": first_ref.query["replay_scope"],
        "historic_as_of_reconstruction": False,
        "legal_conclusion": False,
        "raw_population_rows_emitted": False,
    }


def run_acceptance(
    *,
    engine_sha: str | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    first_batch_size: int = DEFAULT_BATCH_SIZE,
    replay_batch_size: int = DEFAULT_REPLAY_BATCH_SIZE,
) -> dict[str, Any]:
    normalized_sha = _resolve_engine_sha(engine_sha)
    engine_version = f"git:{normalized_sha}"

    first = build_live_materialization(
        batch_size=first_batch_size,
        max_rows=max_rows,
        engine_version=engine_version,
    )
    replay = build_live_materialization(
        batch_size=replay_batch_size,
        max_rows=max_rows,
        engine_version=engine_version,
    )
    return build_acceptance_receipt(
        first,
        replay,
        engine_sha=normalized_sha,
        max_rows=max_rows,
        first_batch_size=first_batch_size,
        replay_batch_size=replay_batch_size,
    )


def _emit_receipt(receipt: dict[str, Any], output: str | None) -> None:
    encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2)
    print(encoded)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{encoded}\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the bounded target-host acceptance for the CN filing-to-preliminary-"
            "publication research dataset and emit a redacted replay receipt."
        )
    )
    parser.add_argument(
        "--engine-sha",
        default=None,
        help=(
            "Exact 40-character Data Engine git SHA. Defaults to "
            "MARKORBIT_DATA_ENGINE_SHA/GITHUB_SHA."
        ),
    )
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    parser.add_argument("--first-batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--replay-batch-size", type=int, default=DEFAULT_REPLAY_BATCH_SIZE
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path for the durable redacted JSON receipt.",
    )
    args = parser.parse_args()

    try:
        receipt = run_acceptance(
            engine_sha=args.engine_sha,
            max_rows=args.max_rows,
            first_batch_size=args.first_batch_size,
            replay_batch_size=args.replay_batch_size,
        )
    except Exception as exc:
        failure = {
            "receipt_version": RECEIPT_VERSION,
            "status": "BLOCKED",
            "redacted": True,
            "objective_only": True,
            "reason": str(exc),
            "raw_population_rows_emitted": False,
        }
        _emit_receipt(failure, args.output)
        return 2

    _emit_receipt(receipt, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
