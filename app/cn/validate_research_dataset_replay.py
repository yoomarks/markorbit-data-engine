from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import time
import uuid

from app.cn.ingest import _cleanup_partial_outputs, ingest_cn_package
from app.cn.validate_fixture import DIRECT_APP
from app.cn.validate_sample_package_e2e import (
    _base_members,
    _discovered,
    _patch_members,
    _sha256,
    _write_zip,
)
from app.db import clickhouse_client
from app.repository import get_package, register_package
from app.research_dataset import build_research_dataset_ref_v1, replay_matches


FIXTURE_VERSION = "CN_RESEARCH_DATASET_REPLAY_V1"


def _history_rows() -> list[tuple[str, str, str, str]]:
    rows = clickhouse_client().query(
        f"""
        SELECT application_number, role, raw_name, action
        FROM markorbit_facts.cn_case_party_relation_history FINAL
        WHERE application_number = '{DIRECT_APP}' AND role = 'OWNER'
        ORDER BY raw_name, action
        """
    ).result_rows
    return [tuple(str(value) for value in row) for row in rows]


def _result_digest(rows: list[tuple[str, str, str, str]]) -> str:
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _dataset_ref(
    rows: list[tuple[str, str, str, str]],
    *,
    watermark: str,
    generated_at: str,
):
    return build_research_dataset_ref_v1(
        engine_version="M1.7",
        fact_schema_version="cn-case-party-relation-history-v1",
        jurisdictions=["CN"],
        resource_kinds=["cn_case_party_relation_history"],
        query={
            "engine": "clickhouse",
            "table": "markorbit_facts.cn_case_party_relation_history",
            "filters": {"application_number": DIRECT_APP, "role": "OWNER"},
            "projection": ["application_number", "role", "raw_name", "action"],
            "order_by": ["raw_name", "action"],
        },
        watermark=watermark,
        completeness="COMPLETE_TO_WATERMARK",
        pagination={"mode": "bounded", "ordering": ["raw_name", "action"]},
        row_count=len(rows),
        generated_at=generated_at,
        integrity_sha256=_result_digest(rows),
    )


def main() -> None:
    started = time.perf_counter()
    package_ids: list[str] = []
    with tempfile.TemporaryDirectory(prefix="markorbit-cn-research-replay-") as temp_text:
        root = Path(temp_text)
        incoming = root / "incoming"
        raw_root = root / "raw"
        base_path = incoming / "2099.zip"
        patch_path = incoming / "2099_1.zip"
        _write_zip(base_path, _base_members())
        _write_zip(patch_path, _patch_members())
        base_sha = _sha256(base_path)
        patch_sha = _sha256(patch_path)

        base_id, base_inserted = register_package(_discovered(base_path))
        if not base_inserted:
            raise RuntimeError("research replay base package must register as new")
        package_ids.append(base_id)
        ingest_cn_package(base_id, base_path, raw_root, trigger_type="MANUAL")
        if get_package(base_id).get("status") != "SUCCESS":
            raise RuntimeError("research replay base package did not reach SUCCESS")

        patch_id, patch_inserted = register_package(_discovered(patch_path))
        if not patch_inserted:
            raise RuntimeError("research replay patch package must register as new")
        package_ids.append(patch_id)
        ingest_cn_package(patch_id, patch_path, raw_root, trigger_type="MANUAL")
        if get_package(patch_id).get("status") != "SUCCESS":
            raise RuntimeError("research replay patch package did not reach SUCCESS")

        watermark = f"cn-bounded-packages:{base_sha}:{patch_sha}"
        first_rows = _history_rows()
        second_rows = _history_rows()
        if not first_rows:
            raise RuntimeError("research history query returned no rows")
        first = _dataset_ref(
            first_rows,
            watermark=watermark,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
        second = _dataset_ref(
            second_rows,
            watermark=watermark,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
        if not replay_matches(first, second):
            raise RuntimeError("identical bounded history query did not replay deterministically")

        print(
            json.dumps(
                {
                    "version": FIXTURE_VERSION,
                    "status": "PASS",
                    "bounded": True,
                    "production_corpus_touched": False,
                    "full_corpus_scale_claimed": False,
                    "real_zip_ingest": True,
                    "query_executions": 2,
                    "row_count": first.row_count,
                    "dataset_ref_id": first.dataset_ref_id,
                    "query_fingerprint_sha256": first.query_fingerprint_sha256,
                    "integrity_sha256": first.integrity_sha256,
                    "replay_match": True,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )

    for package_id in reversed(package_ids):
        try:
            _cleanup_partial_outputs(uuid.UUID(package_id))
        except Exception:
            pass


if __name__ == "__main__":
    main()
