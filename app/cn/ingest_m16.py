from __future__ import annotations

from pathlib import Path
import threading
import uuid
from typing import Any

from app.cn import goods_lifecycle as goods
from app.cn import ingest as legacy
from app.cn.goods_lifecycle_sql import incoming_goods_sql


# ClickHouse 24.8 resolves aliases aggressively inside aggregate queries. Keep
# the lifecycle orchestration in goods_lifecycle.py, but install the M1.6 query
# builder that enforces raw -> private aggregate aliases -> permanent aliases.
goods.incoming_goods_sql = incoming_goods_sql


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
) -> dict[str, Any]:
    lifecycle_metrics = goods.publish_goods_lifecycle(package_uuid, package_meta)

    # The legacy publisher still owns case, party, relation, event and scope
    # persistence. For M1.6 only its scope source is replaced: touched scopes are
    # reconstructed from the complete durable goods-item current table rather
    # than from the rows present in this package.
    original_scope = legacy._scope_aggregate_sql
    legacy._scope_aggregate_sql = lambda package: goods.scope_from_current_items_sql(package)
    try:
        metrics = _LEGACY_PUBLISH(package_uuid, package_meta)
    finally:
        legacy._scope_aggregate_sql = original_scope

    metrics.update(lifecycle_metrics)
    metrics["goods_status_model_version"] = "M1.6"
    # Emit the active identity contract in every real-package publish result so
    # stale local source / stale Docker images are visible immediately in logs.
    metrics["goods_item_identity_version"] = goods.GOODS_ITEM_IDENTITY_VERSION
    return metrics


def _cleanup_partial_outputs_m16(package_uuid: uuid.UUID) -> None:
    _LEGACY_CLEANUP_PARTIAL(package_uuid)
    goods.cleanup_goods_outputs(package_uuid)


def ingest_cn_package(
    package_id: str,
    path: Path,
    raw_root: Path,
    trigger_type: str = "SCHEDULED",
    retrying: bool = False,
) -> dict[str, Any]:
    """M1.6 entry point preserving M1.5 parsing with item-level goods deltas."""
    with _LOCK:
        goods.ensure_m16_goods_schema()
        goods.ensure_m16_goods_replay_boundary()

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
