from __future__ import annotations

import uuid

from app.cn.goods_lifecycle import ApplicationRange
from app.cn.goods_lifecycle_sql import incoming_goods_sql


def bounded_current_items_sql(
    package_uuid: uuid.UUID | str,
    application_range: ApplicationRange,
) -> str:
    """Return only durable current rows that can match this incoming goods chunk.

    The M1.6 publisher historically put every durable current item inside the
    chunk's lexical application-number range on the right side of two LEFT
    JOINs.  As the durable corpus grows, that right side is no longer bounded by
    the staged package chunk and can consume multiple GiB even when only 100k
    staged rows are being processed.

    Keep the existing item identity/status semantics by deriving the match keys
    from the authoritative ``incoming_goods_sql`` builder.  ClickHouse therefore
    builds the inner ANY JOIN from at most the current chunk's incoming keys;
    the result fed to the outer LEFT JOIN is also bounded by those keys.  The
    durable table's ORDER BY starts with the same application/class/item key, so
    the range predicate still gives ClickHouse a physical pruning boundary.
    """
    package = str(package_uuid)
    incoming = incoming_goods_sql(
        package_uuid,
        application_range.lower,
        application_range.upper,
    )
    current_range = application_range.and_predicate("cur.application_number")
    return f"""
        SELECT cur.*
        FROM markorbit_facts.cn_goods_item_current AS cur FINAL
        ANY INNER JOIN
        (
            SELECT application_number, class_no, goods_item_key
            FROM ({incoming}) AS incoming_keys_source
        ) AS incoming_keys
          ON incoming_keys.application_number = cur.application_number
         AND incoming_keys.class_no = cur.class_no
         AND incoming_keys.goods_item_key = cur.goods_item_key
        WHERE cur.is_deleted = 0{current_range}
    """
