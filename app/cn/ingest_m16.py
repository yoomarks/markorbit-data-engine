from __future__ import annotations

from collections import Counter as BuiltinCounter
from pathlib import Path
import threading
import uuid
from typing import Any

from app.cn import goods_lifecycle as goods
from app.cn import ingest as legacy
from app.cn.checkpoint import (
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


def _checkpoint_counter_factory(initial_roles: BuiltinCounter[str]):
    """Seed only legacy.ingest_cn_package's role counter on a resumed run.

    The first Counter() call occurs inside StageBatchWriter for stage row counts;
    the second creates role_counts. Stage row counts must remain current-process
    only, while role_counts must include already checkpointed members so the
    legacy required-role guard remains valid when goods/basic files are skipped.
    """
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
    """M1.6 ingest with item lifecycle plus member-level crash resume.

    source_package_file is the durable member checkpoint: it is written only
    after one ZIP member has been fully parsed. If Python/Docker/the host dies,
    retained stage rows for completed members are reused on the next run while
    any partial uncheckpointed member is synchronously deleted and reparsed.
    Publication remains package-atomic from the application's perspective:
    partial published outputs are always cleaned before an interrupted retry.
    """
    with _LOCK:
        goods.ensure_m16_goods_schema()
        goods.ensure_m16_goods_replay_boundary()

        # Validate metadata checkpoints against retained ClickHouse stage before
        # reusing them. This also makes old pre-checkpoint interrupted runs safe:
        # stale metadata whose stage was previously cleaned is invalidated.
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

        def iter_members_with_resume(member_path: Path):
            for member in _LEGACY_ITER_PACKAGE_MEMBERS(member_path):
                if member.internal_name in completed:
                    continue
                yield member

        def checkpoint_upsert(package: str, item: dict[str, Any]) -> None:
            _LEGACY_UPSERT_PACKAGE_FILE(package, item)
            # source_package_file itself is the authoritative completion marker.
            completed.add(str(item["internal_name"]))
            try:
                record_member_checkpoint(package, item)
            except Exception:
                # Extra resume profile metadata is non-critical; the durable row
                # written above is enough to make the member resumable.
                pass

        def checkpoint_cleanup_stage(package_uuid: uuid.UUID) -> None:
            # Query PostgreSQL fresh because a process can fail between the
            # source_package_file commit and updating the in-memory set.
            durable = completed_member_names(str(package_uuid))
            cleanup_uncheckpointed_stage(package_uuid, durable)

        legacy._publish = _publish_m16
        legacy._cleanup_partial_outputs = _cleanup_partial_outputs_m16
        legacy._cleanup_stage = checkpoint_cleanup_stage
        legacy.iter_package_members = iter_members_with_resume
        legacy.upsert_package_file = checkpoint_upsert
        legacy.Counter = _checkpoint_counter_factory(initial_roles)

        package_uuid = uuid.UUID(str(package_id))
        try:
            totals = legacy.ingest_cn_package(
                package_id,
                path,
                raw_root,
                trigger_type=trigger_type,
                retrying=retrying,
            )

            # legacy success cleanup is intentionally checkpoint-aware and leaves
            # completed stage rows in place. Use them to rebuild whole-package
            # metrics (old + current process), then perform the real full cleanup.
            try:
                totals = finalize_checkpoint_metrics(
                    package_id,
                    totals,
                    reused_members=reused_members,
                )
            except Exception as exc:
                # Publication succeeded but resume metadata finalization did not.
                # Make the package retryable rather than leaving a misleading
                # SUCCESS row; partial outputs are deterministically removable.
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
