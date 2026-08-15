from pathlib import Path
import uuid

import pytest

from app.cn.goods_lifecycle import ApplicationRange
from app.cn.quality_subtasks import (
    QUALITY_SUBTASK_TARGET_ROWS,
    _integrity_sql,
    collect_stage_quality_issues_bounded,
    plan_application_ranges,
)


PACKAGE = uuid.UUID("00000000-0000-0000-0000-000000000001")
RUN = uuid.UUID("00000000-0000-0000-0000-000000000002")


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sql: list[str] = []

    def query(self, sql: str):
        self.sql.append(sql)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return Result(response)


def test_quality_range_planner_keeps_whole_application_boundaries():
    client = ScriptedClient(
        [
            [("100",)],
            [("300",)],
            [],
        ]
    )
    ranges = plan_application_ranges(
        PACKAGE,
        "markorbit_facts.cn_stage_goods",
        client=client,
        target_rows=10,
    )

    assert ranges == [
        ApplicationRange(lower=None, upper="300"),
        ApplicationRange(lower="300", upper=None),
    ]
    assert "LIMIT 1 OFFSET 10" in client.sql[1]
    assert "application_number >= '300'" in client.sql[2]


def test_integrity_query_bounds_both_sides_to_one_subtask_range():
    sql = _integrity_sql(
        str(PACKAGE),
        "markorbit_facts.cn_stage_goods",
        "markorbit_facts.cn_stage_basic",
        ApplicationRange(lower="200", upper="300"),
        "src",
        "dst",
    )

    assert sql.count("application_number >= '200'") == 2
    assert sql.count("application_number < '300'") == 2
    assert "FROM markorbit_facts.cn_stage_goods" in sql
    assert "FROM markorbit_facts.cn_stage_basic" in sql
    assert "GROUP BY application_number, class_no" in sql
    assert "SETTINGS max_threads = 1" in sql


def test_quality_planner_failure_has_phase_and_subtask_label():
    client = ScriptedClient([RuntimeError("memory limit")])

    with pytest.raises(RuntimeError) as exc_info:
        collect_stage_quality_issues_bounded(PACKAGE, RUN, client=client, target_rows=10)

    message = str(exc_info.value)
    assert "CN_M1.6 phase=STAGE_QUALITY" in message
    assert "subtask=PLAN_BASIC" in message
    assert "memory limit" in message


def test_m16_installs_and_restores_bounded_quality_collector():
    source = Path("app/cn/ingest_m16.py").read_text(encoding="utf-8")

    assert "collect_stage_quality_issues_bounded" in source
    assert "original_quality = legacy._collect_stage_quality_issues" in source
    assert "legacy._collect_stage_quality_issues = bounded_quality" in source
    assert "legacy._collect_stage_quality_issues = original_quality" in source
    assert '"BOUNDED_APPLICATION_SUBTASKS_V1"' in source


def test_large_package_quality_budget_is_explicit_and_bounded():
    assert QUALITY_SUBTASK_TARGET_ROWS == 1_000_000
