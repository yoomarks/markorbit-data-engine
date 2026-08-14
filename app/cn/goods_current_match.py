from __future__ import annotations

import uuid

from app.cn.goods_lifecycle import ApplicationRange
from app.cn.goods_lifecycle_sql import incoming_goods_sql


def bounded_current_items_sql(
    package_uuid: uuid.UUID | str,
    application_range: ApplicationRange,
) -> str:
    """Return only durable current rows that can match this incoming goods chunk.

    CN monthly patches are sparse in application-number space. Bounding a chunk
    by only its lexical lower/upper application numbers can still force a scan of
    millions of unrelated durable rows between those two values. Derive the
    authoritative incoming goods keys with ``incoming_goods_sql`` and put that
    exact three-column set in PREWHERE instead. The durable table is ordered by
    the same ``(application_number, class_no, goods_item_key)`` prefix, allowing
    ClickHouse to prune before reading the full-width current rows.
    """
    incoming = incoming_goods_sql(
        package_uuid,
        application_range.lower,
        application_range.upper,
    )
    return f"""
        SELECT cur.*
        FROM markorbit_facts.cn_goods_item_current AS cur FINAL
        PREWHERE (cur.application_number, cur.class_no, cur.goods_item_key) IN
        (
            SELECT application_number, class_no, goods_item_key
            FROM ({incoming}) AS incoming_keys_source
        )
        WHERE cur.is_deleted = 0
    """
