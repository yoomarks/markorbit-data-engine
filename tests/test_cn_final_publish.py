from __future__ import annotations

import uuid

import pytest

from app.cn.final_publish import ResumableFinalPublishClient, plan_publish_ranges


class _QueryResult:
    def __init__(self, rows):
        self.result_rows = rows


class _SequencedClient:
    def __init__(self, query_results=None, *, fail_command: int | None = None):
        self.query_results = [list(rows) for rows in (query_results or [])]
        self.queries: list[str] = []
        self.commands: list[str] = []
        self.fail_command = fail_command

    def query(self, sql: str, *args, **kwargs):
        self.queries.append(sql)
        rows = self.query_results.pop(0) if self.query_results else []
        return _QueryResult(rows)

    def command(self, sql: str, *args, **kwargs):
        self.commands.append(sql)
        if self.fail_command is not None and len(self.commands) == self.fail_command:
            raise RuntimeError("synthetic clickhouse failure")
        return len(self.commands)


class _MemoryStore:
    def __init__(self):
        self.status: dict[str, tuple[str, str]] = {}
        self.running: list[str] = []
        self.failed: list[str] = []

    @staticmethod
    def task_key(*, sql_hash, stage_table, lower, upper):
        return f"{sql_hash}:{stage_table}:{lower}:{upper}"

    def is_success(self, task_key, sql_hash):
        return self.status.get(task_key) == ("SUCCESS", sql_hash)

    def mark_running(self, *, task_key, sql_hash, **kwargs):
        self.status[task_key] = ("RUNNING", sql_hash)
        self.running.append(task_key)

    def mark_success(self, task_key):
        _, sql_hash = self.status[task_key]
        self.status[task_key] = ("SUCCESS", sql_hash)

    def mark_failed(self, task_key, error):
        _, sql_hash = self.status[task_key]
        self.status[task_key] = ("FAILED", sql_hash)
        self.failed.append(task_key)

    def assert_complete(self):
        counts = {"SUCCESS": 0, "RUNNING": 0, "FAILED": 0}
        for status, _ in self.status.values():
            counts[status] += 1
        if counts["RUNNING"] or counts["FAILED"]:
            raise RuntimeError(f"incomplete: {counts}")
        return counts


def _party_current_sql(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_case_party_current
        SELECT incoming.application_number
        FROM (
            SELECT application_number, role, relation_key
            FROM markorbit_facts.cn_stage_party_publish
            WHERE package_id = toUUID('{package}')
        ) AS incoming
        LEFT JOIN (
            SELECT application_number, role
            FROM markorbit_facts.cn_stage_party_publish
            WHERE package_id = toUUID('{package}')
        ) AS touched
          ON touched.application_number = incoming.application_number
    """


def test_final_range_plan_keeps_boundary_application_whole() -> None:
    package = uuid.uuid4()
    client = _SequencedClient(
        [
            [("200",)],
            [("200",)],
            [("300",)],
            [],
        ]
    )

    ranges = plan_publish_ranges(
        package,
        client=client,
        stage_table="cn_stage_party_publish",
        target_rows=2,
    )

    assert ranges == [(None, "200"), ("200", "300"), ("300", None)]
    assert "LIMIT 1 OFFSET 2" in client.queries[0]
    assert "application_number > '200'" in client.queries[2]


def test_final_publish_bounds_every_stage_source_and_resumes_successes() -> None:
    package = uuid.uuid4()
    store = _MemoryStore()
    sql = _party_current_sql(str(package))

    first_delegate = _SequencedClient([[('200',)], []])
    first = ResumableFinalPublishClient(
        first_delegate,
        package_uuid=package,
        agent_batches=[],
        subtask_store=store,
    )
    first.command(sql)

    assert first.final_tasks_executed == 2
    assert first.final_tasks_skipped == 0
    assert len(first_delegate.commands) == 2
    assert first_delegate.commands[0].count("application_number < '200'") == 2
    assert first_delegate.commands[1].count("application_number >= '200'") == 2

    second_delegate = _SequencedClient([[('200',)], []])
    second = ResumableFinalPublishClient(
        second_delegate,
        package_uuid=package,
        agent_batches=[],
        subtask_store=store,
    )
    second.command(sql)

    assert second.final_tasks_executed == 0
    assert second.final_tasks_skipped == 2
    assert second_delegate.commands == []


def test_failed_range_is_the_only_range_retried_after_restart() -> None:
    package = uuid.uuid4()
    store = _MemoryStore()
    sql = _party_current_sql(str(package))

    failing_delegate = _SequencedClient([[('200',)], []], fail_command=2)
    first = ResumableFinalPublishClient(
        failing_delegate,
        package_uuid=package,
        agent_batches=[],
        subtask_store=store,
    )
    with pytest.raises(RuntimeError, match=r"task=2/2.*synthetic clickhouse failure"):
        first.command(sql)

    assert first.final_tasks_executed == 1
    assert len(store.failed) == 1

    retry_delegate = _SequencedClient([[('200',)], []])
    retry = ResumableFinalPublishClient(
        retry_delegate,
        package_uuid=package,
        agent_batches=[],
        subtask_store=store,
    )
    retry.command(sql)

    assert retry.final_tasks_skipped == 1
    assert retry.final_tasks_executed == 1
    assert len(retry_delegate.commands) == 1
    assert "application_number >= '200'" in retry_delegate.commands[0]


def test_final_publish_fails_closed_when_stage_source_cannot_be_bounded() -> None:
    package = uuid.uuid4()
    store = _MemoryStore()
    delegate = _SequencedClient([[('200',)], []])
    client = ResumableFinalPublishClient(
        delegate,
        package_uuid=package,
        agent_batches=[],
        subtask_store=store,
    )
    sql = (
        "INSERT INTO markorbit_facts.cn_case_current "
        "SELECT * FROM markorbit_facts.cn_stage_case_publish"
    )

    with pytest.raises(RuntimeError, match="bounded_sources=0"):
        client.command(sql)


def test_final_publish_rejects_mixed_publish_stage_sources() -> None:
    package = uuid.uuid4()
    client = ResumableFinalPublishClient(
        _SequencedClient(),
        package_uuid=package,
        agent_batches=[],
        subtask_store=_MemoryStore(),
    )
    sql = (
        "SELECT * FROM markorbit_facts.cn_stage_case_publish "
        "JOIN markorbit_facts.cn_stage_party_publish USING application_number"
    )

    with pytest.raises(RuntimeError, match="mixes publish-stage tables"):
        client.command(sql)
