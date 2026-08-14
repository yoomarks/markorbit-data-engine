from __future__ import annotations

from pathlib import Path
import threading
import uuid
from typing import Any

from app.cn import case_publish as case
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


goods.incoming_goods_sql = incoming_goods_sql
_LOCK = threading.RLock()
_LEGACY_PUBLISH = legacy._publish
_LEGACY_CLEANUP_PARTIAL = legacy._cleanup_partial_outputs
_LEGACY_CASE_AGG = legacy._case_aggregate_sql
_LEGACY_PARTY_AGG = legacy._party_aggregate_sql


def _publish_m16(package_uuid: uuid.UUID, package_meta: dict[str, Any]) -> dict[str, Any]:
    original_goods_client = goods.clickhouse_client
    goods_delta_client = GoodsObservationDeltaClient(original_goods_client())
    goods.clickhouse_client = lambda: goods_delta_client
    try:
        lifecycle_metrics = goods.publish_goods_lifecycle(package_uuid, package_meta)
        goods_delta_client.assert_rewrite_count(int(lifecycle_metrics["goods_publish_chunk_count"]))
    finally:
        goods.clickhouse_client = original_goods_client

    case_metrics = case.materialize_case_publish_stage(package_uuid, _LEGACY_CASE_AGG)
    party_metrics = party.materialize_party_publish_stage(package_uuid, _LEGACY_PARTY_AGG)

    original_case = legacy._case_aggregate_sql
    original_scope = legacy._scope_aggregate_sql
    original_party = legacy._party_aggregate_sql
    original_case_events = legacy._insert_case_events
    original_clickhouse_client = legacy.clickhouse_client
    legacy._case_aggregate_sql = lambda package: case.case_publish_stage_sql(package)
    legacy._scope_aggregate_sql = lambda package: goods.scope_publish_stage_sql(package)
    legacy._party_aggregate_sql = lambda package: party.party_publish_stage_sql(package)
    legacy._insert_case_events = events.insert_case_delta_events

    party_history_client = PartyHistorySuppressionClient(original_clickhouse_client())
    event_delta_client = events.EventBaselineDeltaClient(party_history_client)
    legacy.clickhouse_client = lambda: event_delta_client
    try:
        metrics = _LEGACY_PUBLISH(package_uuid, package_meta)
        party_history_client.assert_suppression_complete()
        event_delta_client.assert_rewrite_counts()
    finally:
        legacy.clickhouse_client = original_clickhouse_client
        legacy._case_aggregate_sql = original_case
        legacy._scope_aggregate_sql = original_scope
        legacy._party_aggregate_sql = original_party
        legacy._insert_case_events = original_case_events

    case.cleanup_case_publish_stage(package_uuid)
    goods.cleanup_scope_publish_stage(package_uuid)
    party.cleanup_party_publish_stage(package_uuid)
    metrics.update(lifecycle_metrics)
    metrics.update(case_metrics)
    metrics.update(party_metrics)
    metrics["goods_status_model_version"] = "M1.6"
    metrics["goods_item_identity_version"] = goods.GOODS_ITEM_IDENTITY_VERSION
    metrics["intra_package_status_resolution_version"] = INTRA_PACKAGE_STATUS_RESOLUTION_VERSION
    metrics["recovery_mode"] = "PACKAGE_REPLAY"
    metrics["goods_observation_history_policy"] = "TRUE_DELTA_ONLY_V2"
    metrics["observed_event_history_policy"] = "TRUE_DELTA_PLUS_PARTY_V2"
    metrics["party_history_policy"] = "CANONICAL_IN_OBSERVED_EVENT_V2"
    return metrics


def _cleanup_partial_outputs_m16(package_uuid: uuid.UUID) -> None:
    _LEGACY_CLEANUP_PARTIAL(package_uuid)
    goods.cleanup_goods_outputs(package_uuid)
    case.cleanup_case_publish_stage(package_uuid)
    party.cleanup_party_publish_stage(package_uuid)


def ingest_cn_package(
    package_id: str,
    path: Path,
    raw_root: Path,
    trigger_type: str = "SCHEDULED",
    retrying: bool = False,
) -> dict[str, Any]:
    with _LOCK:
        original_legacy_client = legacy.clickhouse_client
        original_case_client = case.clickhouse_client
        original_goods_client = goods.clickhouse_client
        original_party_client = party.clickhouse_client
        legacy.clickhouse_client = lambda: cn_resource_client(original_legacy_client)
        case.clickhouse_client = lambda: cn_resource_client(original_case_client)
        goods.clickhouse_client = lambda: cn_resource_client(original_goods_client)
        party.clickhouse_client = lambda: cn_resource_client(original_party_client)

        original_publish = legacy._publish
        original_cleanup = legacy._cleanup_partial_outputs
        legacy._publish = _publish_m16
        legacy._cleanup_partial_outputs = _cleanup_partial_outputs_m16
        try:
            case.ensure_case_publish_schema()
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
            legacy._cleanup_partial_outputs = original_cleanup
            legacy.clickhouse_client = original_legacy_client
            case.clickhouse_client = original_case_client
            goods.clickhouse_client = original_goods_client
            party.clickhouse_client = original_party_client
