from __future__ import annotations

import json

# Import the real production entrypoint first. ingest_m16 installs the M1.6
# ClickHouse-24.8-safe goods SQL builder into goods_lifecycle at module import.
# Without this import the verifier would inspect the unhooked module in isolation
# and report a false negative even though app.jobs -> ingest_m16 is the path used
# by run-cn / worker ingestion.
from app.cn import ingest_m16 as _ingest_m16  # noqa: F401
from app.cn import goods_lifecycle as goods
from app.cn.goods_lifecycle_sql import incoming_goods_sql


EXPECTED_IDENTITY_VERSION = "CN_GOODS_ITEM_ID_V2_STRICT_SOURCE_FIELDS"


def main() -> None:
    sql = incoming_goods_sql("00000000-0000-0000-0000-000000000001")
    runtime_sql = goods.incoming_goods_sql("00000000-0000-0000-0000-000000000001")
    checks = {
        "identity_version": goods.GOODS_ITEM_IDENTITY_VERSION == EXPECTED_IDENTITY_VERSION,
        "strict_sequence": "'|SEQ|', goods_sequence" in sql,
        "strict_similar_group": "'|GROUP|', similar_group" in sql,
        "strict_goods_name": "'|NAME|', lowerUTF8(goods_name)" in sql,
        "legacy_sequence_branch_absent": "goods_sequence != ''" not in sql,
        "runtime_builder_installed": goods.incoming_goods_sql is incoming_goods_sql,
        "runtime_sql_strict_sequence": "'|SEQ|', goods_sequence" in runtime_sql,
        "runtime_sql_strict_similar_group": "'|GROUP|', similar_group" in runtime_sql,
        "runtime_sql_strict_goods_name": "'|NAME|', lowerUTF8(goods_name)" in runtime_sql,
        "runtime_sql_legacy_branch_absent": "goods_sequence != ''" not in runtime_sql,
    }
    result = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "contract": "M1.6_RUNTIME_IDENTITY",
        "identity_version": goods.GOODS_ITEM_IDENTITY_VERSION,
        "checks": checks,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
