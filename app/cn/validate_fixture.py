from __future__ import annotations

from datetime import date
import json
import time
import uuid
from typing import Any

from app.cn.ingest import (
    STAGE_COLUMNS,
    _basic_stage_row,
    _cleanup_partial_outputs,
    _cleanup_stage,
    _other_stage_row,
    _party_values,
    _publish,
)
from app.db import clickhouse_client


DIRECT_APP = "990000000001"
MULTI_APP = "990000000002"
G_ROOT = "G99000001"
G_CHILD = "G99000001A"
AGENT_CODE = "AGFIX001"


def _insert(client: Any, table: str, row: list[Any]) -> None:
    client.insert(table, [row], column_names=STAGE_COLUMNS[table])


def _stage_basic(client: Any, package: uuid.UUID, application_number: str, class_no: int,
                 mark_name: str, line: int, *, agent_code: str = AGENT_CODE) -> None:
    record = {
        "application_number": application_number,
        "class_no": str(class_no),
        "filing_date": "2020-01-02",
        "mark_name": mark_name,
        "mark_type_raw": "WORD",
        "agent_code": agent_code,
        "prelim_pub_issue": "1801",
        "prelim_pub_date": "2020-05-01",
        "registration_pub_issue": "1810",
        "registration_pub_date": "2020-07-01",
        "exclusive_start_date": "2020-07-01",
        "exclusive_end_date": "2030-06-30",
        "exclusive_period": "2020-07-01至2030-06-30",
    }
    row, _, _ = _basic_stage_row(
        package, record, "fixture/注册商标基本信息.csv", line, line
    )
    if row is None:
        raise RuntimeError(f"fixture basic row rejected: {application_number}/{class_no}")
    _insert(client, "markorbit_facts.cn_stage_basic", row)


def _stage_owner(client: Any, package: uuid.UUID, application_number: str, class_no: int,
                 owner_name: str, line: int) -> None:
    record = {
        "application_number": application_number,
        "class_no": str(class_no),
        "owner_name_cn": owner_name,
        "owner_address_cn": "北京市朝阳区测试路1号",
    }
    result = _party_values(
        package, record, "OWNER", "fixture/商标注册人信息.csv", line, line
    )
    if result is None:
        raise RuntimeError(f"fixture owner row rejected: {application_number}/{class_no}")
    _insert(client, result.table, result.row)


def _stage_coowner(client: Any, package: uuid.UUID, application_number: str,
                   owner_name: str, line: int) -> None:
    record = {
        "application_number": application_number,
        "coowner_name_cn": owner_name,
        "coowner_address_cn": "上海市浦东新区测试路2号",
    }
    result = _party_values(
        package, record, "CO_OWNER", "fixture/注册商标共有人信息.csv", line, line
    )
    if result is None:
        raise RuntimeError(f"fixture coowner row rejected: {application_number}")
    _insert(client, result.table, result.row)


def _stage_other(client: Any, role: str, package: uuid.UUID, record: dict[str, str],
                 source_file: str, line: int) -> None:
    staged = _other_stage_row(role, package, record, source_file, line, line)
    if staged is None:
        raise RuntimeError(f"fixture {role} row rejected: {record}")
    table, row, _, _ = staged
    _insert(client, table, row)


def _stage_package_one(client: Any, package: uuid.UUID) -> None:
    # Direct case + co-owner + agent + priority.
    _stage_basic(client, package, DIRECT_APP, 25, "FIXTURE DIRECT", 2)
    _stage_owner(client, package, DIRECT_APP, 25, "Alpha Fixture Ltd", 2)
    _stage_coowner(client, package, DIRECT_APP, "Beta Coowner Ltd", 2)
    _stage_other(client, "goods", package, {
        "application_number": DIRECT_APP, "class_no": "25", "similar_group": "2501",
        "goods_sequence": "1", "goods_name": "服装", "goods_status_raw": "",
    }, "fixture/注册商标商品服务信息.csv", 2)
    _stage_other(client, "goods", package, {
        "application_number": DIRECT_APP, "class_no": "25", "similar_group": "2507",
        "goods_sequence": "2", "goods_name": "鞋", "goods_status_raw": "删除",
    }, "fixture/注册商标商品服务信息.csv", 3)
    _stage_other(client, "priority", package, {
        "application_number": DIRECT_APP, "class_no": "25", "priority_number": "P-FIX-1",
        "priority_type": "PARIS", "priority_date": "2019-08-01",
        "priority_goods": "服装", "priority_country_region": "US",
    }, "fixture/注册商标优先权信息.csv", 2)

    # Multi-class case: same legal case, two class scopes.
    for line, class_no, goods_name in ((3, 9, "计算机软件"), (4, 42, "软件即服务")):
        _stage_basic(client, package, MULTI_APP, class_no, "FIXTURE MULTI", line)
        _stage_owner(client, package, MULTI_APP, class_no, "Multi Fixture Ltd", line)
        _stage_other(client, "goods", package, {
            "application_number": MULTI_APP, "class_no": str(class_no),
            "similar_group": "0901" if class_no == 9 else "4209",
            "goods_sequence": "1", "goods_name": goods_name, "goods_status_raw": "有效",
        }, "fixture/注册商标商品服务信息.csv", line)

    # Madrid designation CN root + derived A case. Both remain CN facts.
    for line, app_no, mark_name in ((5, G_ROOT, "FIXTURE MADRID ROOT"),
                                    (6, G_CHILD, "FIXTURE MADRID CHILD")):
        _stage_basic(client, package, app_no, 25, mark_name, line)
        _stage_owner(client, package, app_no, 25, "Madrid Fixture Ltd", line)
        _stage_other(client, "goods", package, {
            "application_number": app_no, "class_no": "25", "similar_group": "2501",
            "goods_sequence": "1", "goods_name": "服装", "goods_status_raw": "有效",
        }, "fixture/注册商标商品服务信息.csv", line)
        _stage_other(client, "madrid", package, {
            "application_number": app_no,
            "international_registration_number": "99000001",
            "international_registration_date": "2019-01-01",
            "international_notification_date": "2019-02-01",
            "application_language": "EN",
            "application_type": "MADRID",
            "international_pub_issue": "2019/01",
            "international_pub_date": "2019-01-15",
            "subsequent_designation_date": "",
            "basic_registration_date": "2018-12-01",
        }, "fixture/国际注册基本信息.csv", line)

    _stage_other(client, "agent", package, {
        "agent_code": AGENT_CODE,
        "agent_name": "Fixture Trademark Agency",
    }, "fixture/商标代理机构信息.csv", 2)


def _stage_package_two(client: Any, package: uuid.UUID) -> None:
    # A later observation changes OWNER for the same case. This makes the
    # party-touched/supersession SQL execute with non-empty rows.
    _stage_basic(client, package, DIRECT_APP, 25, "FIXTURE DIRECT", 2)
    _stage_owner(client, package, DIRECT_APP, 25, "Gamma Fixture Ltd", 2)
    _stage_other(client, "goods", package, {
        "application_number": DIRECT_APP, "class_no": "25", "similar_group": "2501",
        "goods_sequence": "1", "goods_name": "服装", "goods_status_raw": "",
    }, "fixture/注册商标商品服务信息.csv", 2)
    _stage_other(client, "goods", package, {
        "application_number": DIRECT_APP, "class_no": "25", "similar_group": "2507",
        "goods_sequence": "2", "goods_name": "鞋", "goods_status_raw": "删除",
    }, "fixture/注册商标商品服务信息.csv", 3)
    _stage_other(client, "agent", package, {
        "agent_code": AGENT_CODE,
        "agent_name": "Fixture Trademark Agency",
    }, "fixture/商标代理机构信息.csv", 2)


def _scalar(client: Any, sql: str) -> Any:
    rows = client.query(sql).result_rows
    return rows[0][0] if rows else None


def _assert_fixture(client: Any) -> dict[str, Any]:
    classes = _scalar(client, f"""
        SELECT classes FROM markorbit_facts.cn_case_current FINAL
        WHERE application_number = '{MULTI_APP}' AND is_deleted = 0
    """)
    if list(classes or []) != [9, 42]:
        raise RuntimeError(f"multi-class contract failed: classes={classes}")

    g_row = client.query(f"""
        SELECT filing_route, international_registration_number, case_family_root, suffix_path
        FROM markorbit_facts.cn_case_current FINAL
        WHERE application_number = '{G_CHILD}' AND is_deleted = 0
    """).result_rows
    if not g_row or tuple(g_row[0]) != ("MADRID_DESIGNATION_CN", "99000001", G_ROOT, "A"):
        raise RuntimeError(f"G-number current contract failed: {g_row}")

    relation_count = int(_scalar(client, f"""
        SELECT count() FROM markorbit_facts.cn_case_relation_current FINAL
        WHERE source_application_number = '{G_ROOT}'
          AND target_application_number = '{G_CHILD}' AND is_deleted = 0
    """) or 0)
    if relation_count != 1:
        raise RuntimeError(f"G derived relation contract failed: count={relation_count}")

    carve_count = int(_scalar(client, f"""
        SELECT count() FROM markorbit_facts.cn_scope_carve_out_current FINAL
        WHERE source_application_number = '{G_ROOT}'
          AND target_application_number = '{G_CHILD}' AND class_no = 25 AND is_deleted = 0
    """) or 0)
    if carve_count != 1:
        raise RuntimeError(f"G scope carve-out contract failed: count={carve_count}")

    current_owners = client.query(f"""
        SELECT raw_name FROM markorbit_facts.cn_case_party_current FINAL
        WHERE application_number = '{DIRECT_APP}' AND role = 'OWNER'
          AND is_current = 1 AND is_deleted = 0
        ORDER BY raw_name
    """).result_rows
    owner_names = [str(row[0]) for row in current_owners]
    if owner_names != ["Gamma Fixture Ltd"]:
        raise RuntimeError(f"party replacement contract failed: current owners={owner_names}")

    alpha_current = int(_scalar(client, f"""
        SELECT count() FROM markorbit_facts.cn_case_party_current FINAL
        WHERE application_number = '{DIRECT_APP}' AND role = 'OWNER'
          AND raw_name = 'Alpha Fixture Ltd' AND is_current = 1 AND is_deleted = 0
    """) or 0)
    if alpha_current != 0:
        raise RuntimeError(f"party supersession contract failed: Alpha still current={alpha_current}")

    superseded_history = int(_scalar(client, f"""
        SELECT count() FROM markorbit_facts.cn_case_party_relation_history FINAL
        WHERE application_number = '{DIRECT_APP}' AND role = 'OWNER'
          AND raw_name = 'Alpha Fixture Ltd' AND action = 'SUPERSEDED'
    """) or 0)
    if superseded_history < 1:
        raise RuntimeError("party supersession history contract failed")

    superseded_events = int(_scalar(client, f"""
        SELECT count() FROM markorbit_facts.cn_observed_event FINAL
        WHERE application_number = '{DIRECT_APP}'
          AND event_type = 'OWNER_RELATION_SUPERSEDED_OBSERVED'
    """) or 0)
    if superseded_events < 1:
        raise RuntimeError("party supersession event contract failed")

    lineage_ok = int(_scalar(client, f"""
        SELECT count() FROM markorbit_facts.cn_case_current FINAL
        WHERE application_number IN ('{DIRECT_APP}', '{MULTI_APP}', '{G_ROOT}', '{G_CHILD}')
          AND source_file != '' AND source_first_line > 0 AND source_last_line >= source_first_line
          AND is_deleted = 0
    """) or 0)
    if lineage_ok != 4:
        raise RuntimeError(f"permanent lineage value contract failed: count={lineage_ok}")

    return {
        "multi_class": "PASS",
        "party_replacement_nonempty": "PASS",
        "g_madrid_cn_derived_case": "PASS",
        "scope_carve_out": "PASS",
        "permanent_lineage_values": "PASS",
    }


def main() -> None:
    started = time.perf_counter()
    client = clickhouse_client()
    package_one = uuid.uuid4()
    package_two = uuid.uuid4()
    rank_one = 9_000_000_000_000_001
    rank_two = rank_one + 1

    try:
        _stage_package_one(client, package_one)
        metrics_one = _publish(package_one, {
            "package_kind": "CONTRACT_FIXTURE_BASE",
            "source_rank": rank_one,
            "source_period_end": date(2026, 1, 1),
        })

        _stage_package_two(client, package_two)
        metrics_two = _publish(package_two, {
            "package_kind": "CONTRACT_FIXTURE_PATCH",
            "source_rank": rank_two,
            "source_period_end": date(2026, 2, 1),
        })

        checks = _assert_fixture(client)
        result = {
            "status": "PASS",
            "contract": "M1.5.3.1",
            "fixture": "nonempty-two-package-runtime",
            "checks": checks,
            "publish_one": metrics_one,
            "publish_two": metrics_two,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        # Leave no fixture facts behind, even when a runtime assertion fails.
        for package in (package_two, package_one):
            try:
                _cleanup_partial_outputs(package)
            except Exception:
                pass
            try:
                _cleanup_stage(package)
            except Exception:
                pass


if __name__ == "__main__":
    main()
