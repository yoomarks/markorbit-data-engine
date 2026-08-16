from __future__ import annotations

import uuid

from app.cn import final_publish
from app.cn.final_publish import ResumableFinalPublishClient


class _Result:
    def __init__(self, rows):
        self.result_rows = rows


class _Client:
    def __init__(self):
        self.queries: list[str] = []
        self.commands: list[str] = []

    def query(self, sql, *args, **kwargs):
        self.queries.append(sql)
        return _Result([])

    def command(self, sql, *args, **kwargs):
        self.commands.append(sql)
        return None


class _Store:
    def __init__(self, *, existing_work: bool = False):
        self.rows: dict[str, dict] = {}
        if existing_work:
            self.rows["old-task"] = {
                "status": "SUCCESS",
                "sql_hash": "old-hash",
            }

    @staticmethod
    def task_key(*, sql_hash, stage_table, lower, upper):
        return f"{sql_hash}:{stage_table}:{lower}:{upper}"

    def is_success(self, task_key, sql_hash):
        row = self.rows.get(task_key)
        return bool(row and row["status"] == "SUCCESS" and row["sql_hash"] == sql_hash)

    def mark_running(self, *, task_key, sql_hash, **metadata):
        self.rows[task_key] = {
            "status": "RUNNING",
            "sql_hash": sql_hash,
            **metadata,
        }

    def mark_success(self, task_key):
        self.rows[task_key]["status"] = "SUCCESS"

    def mark_failed(self, task_key, error):
        self.rows[task_key]["status"] = "FAILED"
        self.rows[task_key]["error"] = error

    def summary(self):
        counts = {"SUCCESS": 0, "RUNNING": 0, "FAILED": 0}
        for row in self.rows.values():
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return counts

    def assert_complete(self):
        return self.summary()


def _priority_placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_priority_current
        SELECT application_number
        FROM markorbit_facts.cn_stage_priority
        WHERE package_id = toUUID('{package}')
    """


def test_new_final_publish_checkpoint_enables_native_aux_and_persists_marker(monkeypatch) -> None:
    package = uuid.uuid4()
    client = _Client()
    store = _Store()
    monkeypatch.setattr(final_publish, "get_package", lambda package_id: {"source_rank": 77})

    publisher = ResumableFinalPublishClient(
        client,
        package_uuid=package,
        agent_batches=[],
        subtask_store=store,
    )

    assert publisher.native_aux_enabled is True
    marker_rows = [
        row for row in store.rows.values() if row.get("task_group") == "NATIVE_AUX_CUTOVER"
    ]
    assert len(marker_rows) == 1
    assert marker_rows[0]["status"] == "SUCCESS"

    publisher.command(_priority_placeholder(str(package)))

    assert len(client.commands) == 1
    assert "GROUP BY application_number, class_no, priority_number" in client.commands[0]
    assert "source_rank" not in _priority_placeholder(str(package))
    assert "77, now64(3), 0" in client.commands[0]


def test_inflight_compatibility_checkpoint_does_not_switch_execution_mode() -> None:
    package = uuid.uuid4()
    publisher = ResumableFinalPublishClient(
        _Client(),
        package_uuid=package,
        agent_batches=[],
        subtask_store=_Store(existing_work=True),
    )

    assert publisher.native_aux_enabled is False
