from __future__ import annotations

from pathlib import Path
import threading
import uuid
from typing import Any

from app.cn import goods_lifecycle as goods
from app.cn import ingest as legacy
from app.cn.goods_lifecycle_sql import INTRA_PACKAGE_STATUS_RESOLUTION_VERSION
from app.cn.goods_lifecycle_sql import incoming_goods_sql
from app.db import clickhouse_client


# ClickHouse 24.8 resolves aliases aggressively inside aggregate queries. Keep
# the lifecycle orchestration in goods_lifecycle.py, but install the M1.6 query
# builder that enforces raw -> private aggregate aliases -> permanent aliases.
goods.incoming_goods_sql = incoming_goods_sql


# M1.6 intentionally wraps the proven M1.5 parser/case/party publisher instead
# of duplicating it. CN package orchestration is serialized by the PostgreSQL
# session advisory lock in app.cn.run_guard; this process-local lock protects the
# temporary publisher hooks below.
_LOCK = threading.RLock()
_LEGACY_PUBLISH = legacy._publish
_LEGACY_CLEANUP_PARTIAL = legacy._cleanup_partial_outputs


def _publish_m16(
    package_uuid: uuid.UUID,
    package_meta: dict[str, Any],
) -> dict[str, Any]:
    client = clickhouse_client()
    lifecycle_metrics = goods.publish_goods_lifecycle(
        package_uuid, package_meta, client=client
    )

    # The legacy publisher still owns case, party, relation, event and scope
    # persistence. For M1.6 only its scope source is replaced: touched scopes are
    # reconstructed from the complete durable goods-item current table rather
    # than from the rows present in this package.
    original_scope = legacy._scope_aggregate_sql
    legacy._scope_aggregate_sql = lambda package: goods.scope_from_current_items_sql(package)
    try:
        metrics = _LEGACY_PUBLISH(package_uuid, package_meta, client=client)
    finally:
        legacy._scope_aggregate_sql = original_scope

    metrics.update(lifecycle_metrics)
    metrics["goods_status_model_version"] = "M1.6"
    metrics["goods_item_identity_version"] = goods.GOODS_ITEM_IDENTITY_VERSION
    metrics["intra_package_status_resolution_version"] = (
        INTRA_PACKAGE_STATUS_RESOLUTION_VERSION
    )
    metrics["recovery_mode"] = "PACKAGE_REPLAY"
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
    """M1.6 package ingest with deterministic full-package retry.

    Interrupted work is deliberately replayed from the authoritative ZIP rather
    than resumed from internal checkpoints. The legacy ingest path already
    performs synchronous stage/output cleanup when retrying=True. This keeps the
    runtime contract simple, avoids checkpoint validation/flush overhead, and
    makes recovery behavior identical for every source role.
    """
    with _LOCK:
        client = clickhouse_client()
        goods.ensure_m16_goods_schema(client)
        goods.ensure_m16_goods_replay_boundary(client)

        original_publish = legacy._publish
        original_cleanup_partial = legacy._cleanup_partial_outputs
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
            legacy._cleanup_partial_outputs = original_cleanup_partial
