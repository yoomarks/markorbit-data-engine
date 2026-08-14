from __future__ import annotations

import re


_TOUCHED_JOIN = re.compile(
    r"FROM markorbit_facts\.cn_goods_item_current AS item FINAL\s+"
    r"INNER JOIN \((?P<touched>.*?)\) AS touched\s+"
    r"ON touched\.application_number = item\.application_number\s+"
    r"AND touched\.class_no = item\.class_no\s+"
    r"WHERE item\.is_deleted = 0",
    re.DOTALL,
)


def _canonical_sql(sql: str) -> str:
    return " ".join(sql.split())


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
    matches = list(_TOUCHED_JOIN.finditer(sql))
    if len(matches) != 1:
        raise RuntimeError(
            "M1.6 durable goods scope SQL shape changed; expected one touched-scope JOIN."
        )

    match = matches[0]
    actual_touched = match.group("touched")
    if _canonical_sql(actual_touched) != _canonical_sql(touched_sql):
        raise RuntimeError(
            "M1.6 durable goods scope SQL used an unexpected touched-scope selector."
        )

    replacement = f"""FROM markorbit_facts.cn_goods_item_current AS item FINAL
            PREWHERE (item.application_number, item.class_no) IN (
                {actual_touched}
            )
            WHERE item.is_deleted = 0"""
    return sql[: match.start()] + replacement + sql[match.end() :]
