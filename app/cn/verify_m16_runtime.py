from __future__ import annotations

import json

from app.cn import goods_lifecycle as goods
from app.cn.goods_lifecycle_sql import incoming_goods_sql


EXPECTED_IDENTITY_VERSION = "CN_GOODS_ITEM_ID_V2_STRICT_SOURCE_FIELDS"


def main() -> None:
    sql = incoming_goods_sql("00000000-0000-0000-0000-000000000001")
    checks = {
        "identity_version": goods.GOODS_ITEM_IDENTITY_VERSION == EXPECTED_IDENTITY_VERSION,
        "strict_sequence": "'|SEQ|', goods_sequence" in sql,
        "strict_similar_group": "'|GROUP|', similar_group" in sql,
        "strict_goods_name": "'|NAME|', lowerUTF8(goods_name)" in sql,
        "legacy_sequence_branch_absent": "goods_sequence != ''" not in sql,
        "runtime_builder_installed": goods.incoming_goods_sql is incoming_goods_sql,
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
