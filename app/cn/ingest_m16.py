from __future__ import annotations

from pathlib import Path
import threading
import uuid
from typing import Any, Callable

from app.cn import case_publish as case
from app.cn import goods_lifecycle as goods
from app.cn import ingest as legacy
from app.cn import party_publish as party
from app.cn import storage_v2_events as events
from app.cn.goods_current_match import bounded_current_items_sql
from app.cn.goods_lifecycle_sql import (
    INTRA_PACKAGE_STATUS_RESOLUTION_VERSION,
    incoming_goods_sql,
)
from app.cn.goods_scope_match import exact_touched_scope_sql
from app.cn.legacy_snapshot_persist import (
    LegacySnapshotPersistClient,
    plan_agent_code_batches,
)
from app.cn.quality_subtasks import collect_stage_quality_issues_bounded
from app.cn.resource_client import cn_resource_client
from app.cn.stage_resume import (
    CHECKPOINT_VERSION,
    clear_stage_checkpoint,
    ensure_stage_checkpoint_schema,
    load_stage_checkpoint,
    resume_staged_package,
    save_stage_checkpoint,
    stage_checkpoint_is_usable,
)
from app.cn.storage_v2_goods import GoodsObservationDeltaClient
from app.cn.storage_v2_party_history import PartyHistorySuppressionClient
from app.repository import get_package


goods.incoming_goods_sql = incoming_goods_sql
_LOCK = threading.RLock()
_LEGACY_PUBLISH = legacy._publish
_LEGACY_CLEANUP_PARTIAL = legacy._cleanup_partial_outputs
_LEGACY_CASE_AGG = legacy._case_aggregate_sql
_LEGACY_PARTY_AGG = legacy._party_aggregate_sql

# Real CN monthly patches have a much wider durable-goods amplification than the
# stage row count alone suggests. 2022_3 still reached the 8 GiB per-query guard
# in AggregatingTransform with 100k whole-application goods chunks even after the
# current-item JOIN side was bounded. Keep CASE/PARTY at the proven 100k budget,
# but give GOODS aggregation a 10x smaller unit so its groupArray/argMax states
# stay comfortably below the hard query envelope without raising memory limits.
CN_GOODS_CHUNK_ROWS = 10_000
CN_CASE_CHUNK_ROWS = 100_000
CN_PARTY_CHUNK_ROWS = 100_000


def _run_phase(name: str, operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except Exception as exc:
        raise RuntimeError(f"CN_M1.6 phase={name} failed: {exc}") from exc


def _publish_m16(package_uuid: uuid.UUID, package_meta: dict[str, Any]) -> dict[str, Any]:
    original_goods_client = goods.clickhouse_client
    original_goods_range_planner = goods._plan_goods_application_ranges
    original_current_items_builder = goods._current_items_for_range_sql
    original_scope_builder = goods.scope_from_current_items_sql
    original_lifecycle_scope_builder = goods._lifecycle_scope_sql
    goods_delta_client = GoodsObservationDeltaClient(original_goods_client())
    goods.clickhouse_client = lambda: goods_delta_client

    def bounded_goods_ranges(package, *, client=None, target_rows=CN_GOODS_CHUNK_ROWS):
        return original_goods_range_planner(
            package,
            client=client,
            target_rows=min(int(target_rows), CN_GOODS_CHUNK_ROWS),
        )

    def bounded_current_items(application_range):
        return bounded_current_items_sql(package_uuid, application_range)

    def exact_scope_from_current_items(
        package,
        application_lower=None,
        application_upper=None,
    ):
        touched = goods.touched_scope_sql(package, application_lower, application_upper)
        sql = original_scope_builder(package, application_lower, application_upper)
        return exact_touched_scope_sql(sql, touched)

    def exact_lifecycle_scope(
        package,
        application_lower=None,
        application_upper=None,
    ):
        touched = goods.touched_scope_sql(package, application_lower, application_upper)
        sql = original_lifecycle_scope_builder(
            package,
            application_lower,
            application_upper,
        )
        return exact_touched_scope_sql(sql, touched)

    goods._plan_goods_application_ranges = bounded_goods_ranges
    goods._current_items_for_range_sql = bounded_current_items
    goods.scope_from_current_items_sql = exact_scope_from_current_items
    goods._lifecycle_scope_sql = exact_lifecycle_scope
    try:
        lifecycle_metrics = _run_phase(
            "GOODS_LIFECYCLE",
            lambda: goods.publish_goods_lifecycle(package_uuid, package_meta),
        )
        goods_delta_client.assert_rewrite_count(
            int(lifecycle_metrics["goods_publish_chunk_count"])
        )
    finally:
        goods._lifecycle_scope_sql = original_lifecycle_scope_builder
        goods.scope_from_current_items_sql = original_scope_builder
        goods._current_items_for_range_sql = original_current_items_builder
        goods._plan_goods_application_ranges = original_goods_range_planner
        goods.clickhouse_client = original_goods_client

    case_metrics = _run_phase(
        "CASE_MATERIALIZE",
        lambda: case.materialize_case_publish_stage(
            package_uuid,
            _LEGACY_CASE_AGG,
            target_rows=CN_CASE_CHUNK_ROWS,
        ),
    )
    party_metrics = _run_phase(
        "PARTY_MATERIALIZE",
        lambda: party.materialize_party_publish_stage(
            package_uuid,
            _LEGACY_PARTY_AGG,
            target_rows=CN_PARTY_CHUNK_ROWS,
        ),
    )

    original_case = legacy._case_aggregate_sql
    original_scope = legacy._scope_aggregate_sql
    original_party = legacy._party_aggregate_sql
    original_case_events = legacy._insert_case_events
    original_clickhouse_client = legacy.clickhouse_client
    legacy._case_aggregate_sql = lambda package: case.case_publish_stage_sql(package)
    legacy._scope_aggregate_sql = lambda package: goods.scope_publish_stage_sql(package)
    legacy._party_aggregate_sql = lambda package: party.party_publish_stage_sql(package)
    legacy._insert_case_events = events.insert_case_delta_events

    base_snapshot_client = original_clickhouse_client()
    agent_batches = _run_phase(
        "LEGACY_AGENT_PLAN",
        lambda: plan_agent_code_batches(package_uuid, client=base_snapshot_client),
    )
    snapshot_client = LegacySnapshotPersistClient(
        base_snapshot_client,
        package_uuid=package_uuid,
        agent_batches=agent_batches,
    )
    party_history_client = PartyHistorySuppressionClient(snapshot_client)
    event_delta_client = events.EventBaselineDeltaClient(party_history_client)
    legacy.clickhouse_client = lambda: event_delta_client
    try:
        metrics = _run_phase(
            "LEGACY_SNAPSHOT_PERSIST",
            lambda: _LEGACY_PUBLISH(package_uuid, package_meta),
        )
        snapshot_client.assert_agent_persist_complete()
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
    metrics["cn_goods_chunk_rows"] = CN_GOODS_CHUNK_ROWS
    metrics["cn_case_chunk_rows"] = CN_CASE_CHUNK_ROWS
    metrics["cn_party_chunk_rows"] = CN_PARTY_CHUNK_ROWS
    metrics["cn_goods_durable_scope_filter"] = "EXACT_TOUCHED_KEY_PREWHERE_V1"
    metrics["cn_agent_persist_chunk_count"] = snapshot_client.agent_chunk_count
    metrics["cn_agent_persist_agent_code_count"] = snapshot_client.agent_code_count
    metrics["cn_agent_persist_policy"] = "WHOLE_AGENT_CODE_BATCHES_V1"
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
        original_stage_cleanup = legacy._cleanup_stage
        original_quality = legacy._collect_stage_quality_issues
        quality_metrics: dict[str, Any] = {}

        def bounded_quality(package_uuid: uuid.UUID, run_id: uuid.UUID):
            # This hook runs only after every ZIP member has been parsed and all
            # StageBatchWriter buffers have been flushed. Persist the exact stage
            # boundary before any expensive quality/publish work so a later OOM,
            # timeout, worker restart, or host restart can resume without reading
            # a multi-GB raw package again.
            checkpoint = load_stage_checkpoint(str(package_uuid))
            if checkpoint is None:
                save_stage_checkpoint(
                    package_uuid,
                    client=legacy.clickhouse_client(),
                )

            result = collect_stage_quality_issues_bounded(
                package_uuid,
                run_id,
                client=legacy.clickhouse_client(),
            )
            quality_metrics.update(
                {
                    "mode": "BOUNDED_APPLICATION_SUBTASKS_V1",
                    "subtask_count": result.subtask_count,
                    "range_counts": result.range_counts,
                }
            )
            return result.issues

        def checkpoint_aware_stage_cleanup(package_uuid: uuid.UUID) -> None:
            # Legacy cleanup runs before package status is changed to FAILED, so
            # PROCESSING + a valid checkpoint means a post-stage failure and the
            # raw stage is intentionally retained. On SUCCESS we remove it just
            # as before and delete the checkpoint. Early-stage failures have no
            # checkpoint and still clean partial rows before a full reparse.
            package = get_package(str(package_uuid))
            checkpoint = load_stage_checkpoint(str(package_uuid))
            if str(package.get("status")) == "SUCCESS":
                original_stage_cleanup(package_uuid)
                clear_stage_checkpoint(str(package_uuid))
                return
            if checkpoint is not None:
                return
            original_stage_cleanup(package_uuid)

        legacy._publish = _publish_m16
        legacy._cleanup_partial_outputs = _cleanup_partial_outputs_m16
        legacy._cleanup_stage = checkpoint_aware_stage_cleanup
        legacy._collect_stage_quality_issues = bounded_quality
        try:
            ensure_stage_checkpoint_schema()
            case.ensure_case_publish_schema()
            goods.ensure_m16_goods_schema()
            goods.ensure_m16_goods_replay_boundary()
            party.ensure_party_publish_schema()

            checkpoint = load_stage_checkpoint(package_id) if retrying else None
            if checkpoint is not None:
                package_uuid = uuid.UUID(str(package_id))
                if stage_checkpoint_is_usable(
                    package_uuid,
                    checkpoint,
                    client=legacy.clickhouse_client(),
                ):
                    totals = resume_staged_package(
                        legacy,
                        package_id,
                        path,
                        raw_root,
                        checkpoint,
                        trigger_type=trigger_type,
                        cleanup_stage=original_stage_cleanup,
                    )
                    publish = totals.get("publish")
                    if isinstance(publish, dict):
                        publish["recovery_mode"] = "STAGE_CHECKPOINT_RESUME"
                    totals["stage_quality_subtasks"] = quality_metrics
                    return totals
                clear_stage_checkpoint(package_id)
            elif retrying:
                # load_stage_checkpoint fails closed for source SHA/version/age
                # mismatches. Remove any stale row so the legacy retry cleanup is
                # not accidentally treated as a resumable stage.
                clear_stage_checkpoint(package_id)
            else:
                # A normal REGISTERED package is always a fresh package replay.
                clear_stage_checkpoint(package_id)

            totals = legacy.ingest_cn_package(
                package_id,
                path,
                raw_root,
                trigger_type=trigger_type,
                retrying=retrying,
            )
            totals["cn_stage_resume_used"] = False
            totals["cn_stage_checkpoint_version"] = CHECKPOINT_VERSION
            totals["stage_quality_subtasks"] = quality_metrics
            return totals
        finally:
            legacy._collect_stage_quality_issues = original_quality
            legacy._cleanup_stage = original_stage_cleanup
            legacy._publish = original_publish
            legacy._cleanup_partial_outputs = original_cleanup
            legacy.clickhouse_client = original_legacy_client
            case.clickhouse_client = original_case_client
            goods.clickhouse_client = original_goods_client
            party.clickhouse_client = original_party_client
