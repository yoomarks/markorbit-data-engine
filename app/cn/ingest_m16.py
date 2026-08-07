from __future__ import annotations

from pathlib import Path
import threading
import uuid
from typing import Any

from app.cn import ingest as legacy
from app.cn.goods_lifecycle import (
    cleanup_goods_outputs,
    ensure_m16_goods_replay_boundary,
    ensure_m16_goods_schema,
    publish_goods_lifecycle,
    scope_from_current_items_sql,
)


# M1.6 intentionally wraps the proven M1.5 parser/case/party publisher instead
# of duplicating it. The hook is installed only while one package is being
# ingested in this process. Worker/manual orchestration already serializes CN
# package ingestion; the lock also prevents concurrent calls inside one process.
_LOCK = threading.RLock()
_LEGACY_PUBLISH = legacy._publish
_LEGACY_SCOPE_AGGREGATE = legacy._scope_aggregate_sql
_LEGACY_CLEANUP_PARTIAL = legacy._cleanup_partial_outputs


def _publish_m16(
    package_uuid: uuid.UUID,
    package_meta: dict[str, Any],
) -> dict[str, int]:
    lifecycle_metrics = publish_goods_lifecycle(package_uuid, package_meta)

    # The legacy publisher still owns case, party, relation, event and scope
    # persistence. For M1.6 only its scope source is replaced: touched scopes are
    # reconstructed from the complete durable goods-item current table rather
    # than from the rows present in this package.
    original_scope = legacy._scope_aggregate_sql
    legacy._scope_aggregate_sql = lambda package: scope_from_current_items_sql(package)
    try:
        metrics = _LEGACY_PUBLISH(package_uuid, package_meta)
    finally:
        legacy._scope_aggregate_sql = original_scope

    metrics.update(lifecycle_metrics)
    metrics["goods_status_model_version"] = "M1.6"
    return metrics


def _cleanup_partial_outputs_m16(package_uuid: uuid.UUID) -> None:
    _LEGACY_CLEANUP_PARTIAL(package_uuid)
    cleanup_goods_outputs(package_uuid)


def ingest_cn_package(
    package_id: str,
    path: Path,
    raw_root: Path,
    trigger_type: str = "SCHEDULED",
    retrying: bool = False,
) -> dict[str, Any]:
    """M1.6 entry point preserving M1.5 parsing with item-level goods deltas."""
    with _LOCK:
        ensure_m16_goods_schema()
        ensure_m16_goods_replay_boundary()

        original_publish = legacy._publish
        original_cleanup = legacy._cleanup_partial_outputs
        legacy._publish = _publish_m16
        legacy._cleanup_partial_outputs = _cleanup_partial_outputs_m16
        try:
            return legacy.ingest_cn_package(
                package_id,
                path,
                raw_root,
                trigger_type=trigger_type,
                retrying=retrying,
            )
        finally:
            legacy._publish = original_publish
            legacy._cleanup_partial_outputs = original_cleanup
