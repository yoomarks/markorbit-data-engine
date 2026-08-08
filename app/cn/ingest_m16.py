from __future__ import annotations

import csv
from pathlib import Path
import threading
import uuid
from typing import Any

from app.cn import goods_lifecycle as goods
from app.cn import ingest as legacy
from app.cn import reader as cn_reader
from app.cn.goods_lifecycle_sql import (
    INTRA_PACKAGE_STATUS_RESOLUTION_VERSION,
    incoming_goods_sql,
)


# ClickHouse 24.8 resolves aliases aggressively inside aggregate queries. Keep
# the lifecycle orchestration in goods_lifecycle.py, but install the M1.6 query
# builder that enforces raw -> private aggregate aliases -> permanent aliases.
goods.incoming_goods_sql = incoming_goods_sql


# Newer CN exports may quote every CSV field. The legacy record-boundary probe
# split raw physical lines on commas and therefore saw the first field as
# '"12345678"', which does not match the application-number grammar. When that
# happens, many physical rows can be concatenated into one enormous logical
# record. Use csv.reader for the boundary prefix so quoted and unquoted exports
# follow the same application/class/date contract without changing row parsing.
def _record_start_csv_aware(schema: cn_reader.FileSchema, physical_line: str) -> bool:
    line = physical_line.lstrip("\ufeff")
    try:
        values = next(csv.reader([line], strict=False))
    except (csv.Error, StopIteration):
        values = line.split(",", 3)

    if schema.role == "agent":
        return True
    if not values or not cn_reader.APP_RE.fullmatch((values[0] or "").strip()):
        return False
    if schema.requires_class:
        if len(values) < 2 or not cn_reader.CLASS_RE.fullmatch((values[1] or "").strip()):
            return False
    if schema.requires_date:
        if len(values) < 3 or not cn_reader.DATE_RE.match((values[2] or "").strip()):
            return False
    return True


# iter_member_rows resolves _record_start from app.cn.reader globals at runtime,
# so replacing the probe here fixes both the reader reference and the copy
# imported earlier by app.cn.ingest, while keeping the change scoped to M1.6.
cn_reader._record_start = _record_start_csv_aware


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
        goods.ensure_m16_goods_schema()
        goods.ensure_m16_goods_replay_boundary()

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
