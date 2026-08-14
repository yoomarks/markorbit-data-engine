from __future__ import annotations


def exact_touched_scope_sql(sql: str, touched_sql: str) -> str:
    """Rewrite one durable-goods scope aggregate to use exact touched keys.

    Monthly patches can be sparse in application-number space. A 10k stage-row
    chunk may therefore span a very wide lexical range in the durable current
    table. The legacy INNER JOIN reads that range before the join can discard
    untouched applications, so SourceFromNativeStream/AggregatingTransform can
    still hit the per-query memory guard even with a small stage chunk.

    ``cn_goods_item_current`` is ordered by
    ``(application_number, class_no, goods_item_key)``. Put the exact touched
    ``(application_number, class_no)`` set in PREWHERE so ClickHouse can prune on
    the primary-key prefix before reading the wide durable rows. Keep the
    existing range predicate and aggregate SQL unchanged as a secondary guard.
    The rewrite is fail-closed if the expected M1.6 SQL shape changes.
    """
    old = f"""FROM markorbit_facts.cn_goods_item_current AS item FINAL
            INNER JOIN ({touched_sql}) AS touched
              ON touched.application_number = item.application_number
             AND touched.class_no = item.class_no
            WHERE item.is_deleted = 0"""
    new = f"""FROM markorbit_facts.cn_goods_item_current AS item FINAL
            PREWHERE (item.application_number, item.class_no) IN (
                {touched_sql}
            )
            WHERE item.is_deleted = 0"""
    if sql.count(old) != 1:
        raise RuntimeError(
            "M1.6 durable goods scope SQL shape changed; expected one touched-scope JOIN."
        )
    return sql.replace(old, new, 1)
