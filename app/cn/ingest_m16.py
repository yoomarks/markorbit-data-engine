from __future__ import annotations

from pathlib import Path
import threading
import uuid
from typing import Any

from app.cn import goods_lifecycle as goods
from app.cn import ingest as legacy
from app.cn import party_publish as party
from app.cn.goods_lifecycle_sql import (
    INTRA_PACKAGE_STATUS_RESOLUTION_VERSION,
    incoming_goods_sql,
)


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
_LEGACY_PARTY_AGG = legacy._party_aggregate_sql


def _publish_m16(
    package_uuid: uuid.UUID,
    package_meta: dict[str, Any],
) -> dict[str, Any]:
    lifecycle_metrics = goods.publish_goods_lifecycle(package_uuid, package_meta)

    # PARTY aggregation is also materialized once, in bounded whole-application
    # ranges. Large yearly packages otherwise rebuild the OWNER/CO_OWNER/AGENT
    # UNION for every party event/history/current INSERT and can exhaust the
    # ClickHouse server before the join-spill controls can help.
    party_metrics = party.materialize_party_publish_stage(
        package_uuid,
        _LEGACY_PARTY_AGG,
    )

    # The bounded M1.6 publishers have already reconstructed every touched goods
    # scope and every party relation into compact package snapshots. Reuse those
    # snapshots for the proven legacy persistence logic instead of rebuilding
    # millions of raw stage rows inside each INSERT.
    original_scope = legacy._scope_aggregate_sql
    original_party = legacy._party_aggregate_sql
    original_clickhouse_client = legacy.clickhouse_client
    legacy._scope_aggregate_sql = lambda package: goods.scope_publish_stage_sql(package)
    legacy._party_aggregate_sql = lambda package: party.party_publish_stage_sql(package)

    # Storage V2 keeps the legacy current-state publisher intact while making
    # permanent party relation history delta-only. The adapter is installed only
    # for this serialized M1.6 publish call and fails closed if the legacy SQL
    # shape changes.
    delta_client = party.PartyHistoryDeltaClient(
        original_clickhouse_client(),
        source_rank=int(package_meta["source_rank"]),
    )
    legacy.clickhouse_client = lambda: delta_client
    try:
        metrics = _LEGACY_PUBLISH(package_uuid, package_meta)
        delta_client.assert_observed_current_rewritten()
    finally:
        legacy.clickhouse_client = original_clickhouse_client
        legacy._scope_aggregate_sql = original_scope
        legacy._party_aggregate_sql = original_party

    # Snapshots are transient. On publish failure the outer legacy retry cleanup
    # calls _cleanup_partial_outputs_m16, so only the successful path removes
    # them here.
    goods.cleanup_scope_publish_stage(package_uuid)
    party.cleanup_party_publish_stage(package_uuid)

    metrics.update(lifecycle_metrics)
    metrics.update(party_metrics)
    metrics["goods_status_model_version"] = "M1.6"
    metrics["goods_item_identity_version"] = goods.GOODS_ITEM_IDENTITY_VERSION
    metrics["intra_package_status_resolution_version"] = (
        INTRA_PACKAGE_STATUS_RESOLUTION_VERSION
    )
    metrics["recovery_mode"] = "PACKAGE_REPLAY"
    metrics["party_history_policy"] = "DELTA_ONLY_V1"
    return metrics


def _cleanup_partial_outputs_m16(package_uuid: uuid.UUID) -> None:
    _LEGACY_CLEANUP_PARTIAL(package_uuid)
    goods.cleanup_goods_outputs(package_uuid)
    party.cleanup_party_publish_stage(package_uuid)


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
        goods.ensure_m16_goods_schema()
        goods.ensure_m16_goods_replay_boundary()
        party.ensure_party_publish_schema()

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
