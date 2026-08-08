from __future__ import annotations

from collections import Counter as BuiltinCounter
from pathlib import Path
import threading
import uuid
from typing import Any

from app.cn import goods_lifecycle as goods
from app.cn import ingest as legacy
from app.cn.checkpoint import (
    RESUMABLE_ROLES,
    checkpoint_role_counts,
    cleanup_uncheckpointed_stage,
    completed_member_names,
    finalize_checkpoint_metrics,
    record_member_checkpoint,
    validated_completed_member_names,
)
from app.cn.goods_lifecycle_sql import incoming_goods_sql
from app.repository import update_package_status


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
_LEGACY_CLEANUP_STAGE = legacy._cleanup_stage
_LEGACY_ITER_PACKAGE_MEMBERS = legacy.iter_package_members
_LEGACY_UPSERT_PACKAGE_FILE = legacy.upsert_package_file
_LEGACY_COUNTER = legacy.Counter
_LEGACY_STAGE_BATCH_WRITER = legacy.StageBatchWriter


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
    metrics["goods_item_identity_version"] = goods.GOODS_ITEM_IDENTITY_VERSION
    return metrics


def _cleanup_partial_outputs_m16(package_uuid: uuid.UUID) -> None:
    _LEGACY_CLEANUP_PARTIAL(package_uuid)
    goods.cleanup_goods_outputs(package_uuid)


def _checkpoint_counter_factory(initial_roles: BuiltinCounter[str]):
    """Seed only legacy.ingest_cn_package's role counter on a resumed run."""
    calls = 0

    def factory(*args: Any, **kwargs: Any):
        nonlocal calls
        calls += 1
        if calls == 2 and not args and not kwargs:
            return BuiltinCounter(initial_roles)
        return BuiltinCounter(*args, **kwargs)

    return factory


def ingest_cn_package(
    package_id: str,
    path: Path,
    raw_root: Path,
    trigger_type: str = "SCHEDULED",
    retrying: bool = False,
) -> dict[str, Any]:
    """M1.6 ingest with conservative member-level crash resume.

    Only goods/priority/madrid members are resumable in V2. Those roles have no
    deferred entity/mention side effects. Before their source_package_file row is
    promoted to a checkpoint, the active StageBatchWriter is synchronously
    flushed and the exact retained ClickHouse row count is recorded.

    Party/basic/agent members are deliberately reparsed after a crash until their
    entity-side effects gain an equally strong checkpoint contract.
    """
    with _LOCK:
        goods.ensure_m16_goods_schema()
        goods.ensure_m16_goods_replay_boundary()

        # V2 invalidates old V1/unsafe/incomplete metadata before retry cleanup.
        initial_completed = validated_completed_member_names(package_id)
        reused_members = len(initial_completed)
        initial_roles = checkpoint_role_counts(package_id)
        completed = set(initial_completed)

        original_publish = legacy._publish
        original_cleanup_partial = legacy._cleanup_partial_outputs
        original_cleanup_stage = legacy._cleanup_stage
        original_iter_members = legacy.iter_package_members
        original_upsert_package_file = legacy.upsert_package_file
        original_counter = legacy.Counter
        original_stage_writer = legacy.StageBatchWriter

        active_writer: legacy.StageBatchWriter | None = None

        class CheckpointStageBatchWriter(_LEGACY_STAGE_BATCH_WRITER):
            def __init__(self, *args: Any, **kwargs: Any):
                nonlocal active_writer
                super().__init__(*args, **kwargs)
                active_writer = self

        def iter_members_with_resume(member_path: Path):
            for member in _LEGACY_ITER_PACKAGE_MEMBERS(member_path):
                if member.internal_name in completed:
                    continue
                yield member

        def checkpoint_upsert(package: str, item: dict[str, Any]) -> None:
            name = str(item["internal_name"])
            role = str(item.get("role") or "")

            # The legacy writer flushes by batch size, not by ZIP-member boundary.
            # A source_package_file row must never claim completion while the
            # member's tail is still only in Python memory. Flush before marking
            # a safe member resumable.
            if role in RESUMABLE_ROLES and active_writer is not None:
                active_writer.close()

            _LEGACY_UPSERT_PACKAGE_FILE(package, item)

            if role in RESUMABLE_ROLES:
                if record_member_checkpoint(package, item):
                    completed.add(name)
            else:
                # Profiling metadata may exist for these roles, but it is not a
                # crash-resume checkpoint and must never cause a future skip.
                completed.discard(name)

        def checkpoint_cleanup_stage(package_uuid: uuid.UUID) -> None:
            # On a retry, validated_completed_member_names has already removed
            # old/unsafe checkpoint metadata. Retain only the rows whose remaining
            # metadata is safe for this attempt; delete every other partial row.
            durable = completed_member_names(str(package_uuid))
            cleanup_uncheckpointed_stage(package_uuid, durable)

        legacy._publish = _publish_m16
        legacy._cleanup_partial_outputs = _cleanup_partial_outputs_m16
        legacy._cleanup_stage = checkpoint_cleanup_stage
        legacy.iter_package_members = iter_members_with_resume
        legacy.upsert_package_file = checkpoint_upsert
        legacy.Counter = _checkpoint_counter_factory(initial_roles)
        legacy.StageBatchWriter = CheckpointStageBatchWriter

        package_uuid = uuid.UUID(str(package_id))
        try:
            totals = legacy.ingest_cn_package(
                package_id,
                path,
                raw_root,
                trigger_type=trigger_type,
                retrying=retrying,
            )

            # Legacy success cleanup is checkpoint-aware and leaves completed
            # stage rows in place long enough to rebuild whole-package metrics.
            try:
                totals = finalize_checkpoint_metrics(
                    package_id,
                    totals,
                    reused_members=reused_members,
                )
            except Exception as exc:
                try:
                    _cleanup_partial_outputs_m16(package_uuid)
                finally:
                    update_package_status(
                        package_id,
                        "INTERRUPTED",
                        error_message=(
                            "Checkpoint finalization failed after publication; "
                            f"safe replay required: {exc}"
                        ),
                    )
                raise
            finally:
                _LEGACY_CLEANUP_STAGE(package_uuid)
            return totals
        finally:
            legacy._publish = original_publish
            legacy._cleanup_partial_outputs = original_cleanup_partial
            legacy._cleanup_stage = original_cleanup_stage
            legacy.iter_package_members = original_iter_members
            legacy.upsert_package_file = original_upsert_package_file
            legacy.Counter = original_counter
            legacy.StageBatchWriter = original_stage_writer
