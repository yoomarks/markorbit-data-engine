from __future__ import annotations

from pathlib import Path
import threading
import uuid
from typing import Any

from app.cn import goods_lifecycle as goods
from app.cn import ingest as legacy
from app.cn import party_publish as party
from app.cn import storage_v2_events as events
from app.cn.goods_lifecycle_sql import (
    INTRA_PACKAGE_STATUS_RESOLUTION_VERSION,
    incoming_goods_sql,
)
from app.cn.resource_client import cn_resource_client
from app.cn.storage_v2_goods import GoodsObservationDeltaClient
from app.cn.storage_v2_party_history import PartyHistorySuppressionClient


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
    # Storage V2 keeps first-observation provenance on cn_goods_item_current and
    # reserves the wide observation table for true deltas. Install the adapter
    # only around the serialized M1.6 goods publisher so legacy paths remain
    # untouched and any future SQL-shape drift fails closed.
    original_goods_client = goods.clickhouse_client
    goods_delta_client = GoodsObservationDeltaClient(original_goods_client())
    goods.clickhouse_client = lambda: goods_delta_client
    try:
        lifecycle_metrics = goods.publish_goods_lifecycle(package_uuid, package_meta)
        goods_delta_client.assert_rewrite_count(
            int(lifecycle_metrics["goods_publish_chunk_count"])
        )
    finally:
        goods.clickhouse_client = original_goods_client

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
    original_case_events = legacy._insert_case_events
    original_clickhouse_client = legacy.clickhouse_client
    legacy._scope_aggregate_sql = lambda package: goods.scope_publish_stage_sql(package)
    legacy._party_aggregate_sql = lambda package: party.party_publish_stage_sql(package)
    legacy._insert_case_events = events.insert_case_delta_events

    # Storage V2 uses cn_observed_event as the canonical durable PARTY relation
    # history. The legacy publisher emits relation events before the parallel
    # cn_case_party_relation_history INSERTs, so the latter are duplicate wide
    # history and are suppressed completely. EventBaselineDeltaClient continues
    # to preserve every PARTY relation event while suppressing only reconstructible
    # non-PARTY baselines.
    party_history_client = PartyHistorySuppressionClient(original_clickhouse_client())
    event_delta_client = events.EventBaselineDeltaClient(party_history_client)
    legacy.clickhouse_client = lambda: event_delta_client
    try:
        metrics = _LEGACY_PUBLISH(package_uuid, package_meta)
        party_history_client.assert_suppression_complete()
        event_delta_client.assert_rewrite_counts()
    finally:
        legacy.clickhouse_client = original_clickhouse_client
        legacy._scope_aggregate_sql = original_scope
        legacy._party_aggregate_sql = original_party
        legacy._insert_case_events = original_case_events

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
    metrics["goods_observation_history_policy"] = "TRUE_DELTA_ONLY_V2"
    metrics["observed_event_history_policy"] = "TRUE_DELTA_PLUS_PARTY_V2"
    metrics["party_history_policy"] = "CANONICAL_IN_OBSERVED_EVENT_V2"
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
        # The Admin/worker path already opts into grace_hash for JOINs. Apply the
        # complementary low-parallelism/early-spill aggregation profile at the
        # client-operation layer so staging, M1.6 goods/party materialization,
        # legacy case aggregation, cleanup and metrics all inherit it. This is
        # scoped to this serialized CN package and restored before returning.
        original_legacy_clickhouse_client = legacy.clickhouse_client
        original_goods_clickhouse_client = goods.clickhouse_client
        original_party_clickhouse_client = party.clickhouse_client
        legacy.clickhouse_client = lambda: cn_resource_client(
            original_legacy_clickhouse_client
        )
        goods.clickhouse_client = lambda: cn_resource_client(
            original_goods_clickhouse_client
        )
        party.clickhouse_client = lambda: cn_resource_client(
            original_party_clickhouse_client
        )

        original_publish = legacy._publish
        original_cleanup_partial = legacy._cleanup_partial_outputs
        legacy._publish = _publish_m16
        legacy._cleanup_partial_outputs = _cleanup_partial_outputs_m16
        try:
            goods.ensure_m16_goods_schema()
            goods.ensure_m16_goods_replay_boundary()
            party.ensure_party_publish_schema()
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
            legacy.clickhouse_client = original_legacy_clickhouse_client
            goods.clickhouse_client = original_goods_clickhouse_client
            party.clickhouse_client = original_party_clickhouse_client
