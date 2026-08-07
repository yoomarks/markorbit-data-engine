from __future__ import annotations

from app.cn.goods_lifecycle import GOODS_ITEM_IDENTITY_VERSION
from app.db import clickhouse_client


GOODS_IDENTITY_COMPONENT = "CN_GOODS_ITEM_IDENTITY"


def ensure_goods_identity_contract() -> None:
    """Refuse production ingestion when the durable store uses another item identity.

    M1.6 item identity changed after the first real 1999 replay proved that
    application + class + sequence was not unique. Mixing rows produced under
    different identity functions would make monthly delta matching ambiguous, so
    the database must advertise the exact identity contract before ingestion.
    """
    client = clickhouse_client()
    rows = client.query(
        f"""
        SELECT count()
        FROM markorbit_facts.schema_version FINAL
        WHERE component = '{GOODS_IDENTITY_COMPONENT}'
          AND version = '{GOODS_ITEM_IDENTITY_VERSION}'
        """
    ).result_rows
    present = int(rows[0][0] or 0) if rows else 0
    if present == 0:
        raise RuntimeError(
            "CN goods item identity contract mismatch. Expected "
            f"{GOODS_ITEM_IDENTITY_VERSION}. A clean M1.6 reset/replay is required; "
            "do not mix goods rows created by different identity versions."
        )
