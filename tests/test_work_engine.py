from __future__ import annotations

import pytest

from app.work_engine import DurableWorkUnitStore, WorkUnitIdentity, work_engine_contract


class _MemoryPersistence:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.last_running_spec = None

    def read(self, task_key: str):
        row = self.rows.get(task_key)
        return None if row is None else dict(row)

    def running(self, spec) -> None:
        self.last_running_spec = spec
        previous = self.rows.get(spec.task_key) or {}
        self.rows[spec.task_key] = {
            "status": "RUNNING",
            "job_id": spec.job_id,
            "operation_hash": spec.operation_hash,
            "attempts": int(previous.get("attempts") or 0) + 1,
            "partition_kind": spec.partition_kind,
            "lower": spec.partition_lower,
            "upper": spec.partition_upper,
        }

    def success(self, task_key: str) -> None:
        self.rows[task_key]["status"] = "SUCCESS"

    def failed(self, task_key: str, error: str) -> None:
        self.rows[task_key]["status"] = "FAILED"
        self.rows[task_key]["error"] = error

    def summary(self):
        result: dict[str, int] = {}
        for row in self.rows.values():
            status = row["status"]
            result[status] = result.get(status, 0) + 1
        return result


def _store(
    memory: _MemoryPersistence,
    *,
    owner: str = "CN_FINAL_PUBLISH",
    job_id: str = "job-001",
):
    return DurableWorkUnitStore(
        owner_scope=owner,
        job_id=job_id,
        checkpoint_version="V1",
        read_task=memory.read,
        upsert_running=memory.running,
        set_success=memory.success,
        set_failed=memory.failed,
        summarize=memory.summary,
    )


def test_task_identity_is_stable_and_owner_scoped() -> None:
    base = WorkUnitIdentity(
        owner_scope="CN_FINAL_PUBLISH",
        job_id="job-001",
        checkpoint_version="V1",
        operation_hash="abc",
        partition_kind="APPLICATION_RANGE",
        partition_lower="100",
        partition_upper="200",
    )
    same = WorkUnitIdentity(
        owner_scope="CN_FINAL_PUBLISH",
        job_id="job-001",
        checkpoint_version="V1",
        operation_hash="abc",
        partition_kind="APPLICATION_RANGE",
        partition_lower="100",
        partition_upper="200",
    )
    other_owner = WorkUnitIdentity(
        owner_scope="EUIPO_FINAL_PUBLISH",
        job_id="job-001",
        checkpoint_version="V1",
        operation_hash="abc",
        partition_kind="APPLICATION_RANGE",
        partition_lower="100",
        partition_upper="200",
    )

    assert base.task_key() == same.task_key()
    assert base.task_key() != other_owner.task_key()
    assert len(base.task_key()) == 64


def test_job_id_is_outer_scope_without_rekeying_v1_tasks() -> None:
    first = WorkUnitIdentity(
        owner_scope="CN_FINAL_PUBLISH",
        job_id="job-001",
        checkpoint_version="V1",
        operation_hash="abc",
        partition_kind="APPLICATION_RANGE",
        partition_lower="100",
        partition_upper="200",
    )
    second = WorkUnitIdentity(
        owner_scope="CN_FINAL_PUBLISH",
        job_id="job-002",
        checkpoint_version="V1",
        operation_hash="abc",
        partition_kind="APPLICATION_RANGE",
        partition_lower="100",
        partition_upper="200",
    )

    assert first != second
    assert first.task_key() == second.task_key()


def test_job_id_is_exposed_to_domain_persistence() -> None:
    memory = _MemoryPersistence()
    store = _store(memory, job_id="source-package-123")
    key = store.task_key(
        operation_hash="op",
        partition_kind="FILE_PART",
        lower="part-001",
        upper="part-002",
    )

    store.mark_running(
        task_key=key,
        task_group="PARSE",
        task_index=1,
        task_total=1,
        partition_kind="FILE_PART",
        lower="part-001",
        upper="part-002",
        operation_hash="op",
    )

    assert memory.last_running_spec is not None
    assert memory.last_running_spec.job_id == "source-package-123"
    assert memory.rows[key]["job_id"] == "source-package-123"


def test_blank_job_id_is_rejected() -> None:
    memory = _MemoryPersistence()

    with pytest.raises(ValueError, match="job_id is required"):
        _store(memory, job_id="   ")


def test_resume_skips_only_exact_matching_success() -> None:
    memory = _MemoryPersistence()
    store = _store(memory)
    key = store.task_key(
        operation_hash="sql-v1",
        partition_kind="APPLICATION_RANGE",
        lower=None,
        upper="200",
    )
    store.mark_running(
        task_key=key,
        task_group="CASE_CURRENT",
        task_index=1,
        task_total=2,
        partition_kind="APPLICATION_RANGE",
        lower=None,
        upper="200",
        operation_hash="sql-v1",
    )
    store.mark_success(key)

    assert store.is_success(key, "sql-v1") is True
    assert store.is_success(key, "sql-v2") is False


def test_failed_work_unit_is_rerunnable_and_attempts_increment() -> None:
    memory = _MemoryPersistence()
    store = _store(memory)
    key = store.task_key(
        operation_hash="op",
        partition_kind="FILE_PART",
        lower="part-001",
        upper="part-002",
    )

    for attempt in (1, 2):
        store.mark_running(
            task_key=key,
            task_group="PARSE",
            task_index=1,
            task_total=1,
            partition_kind="FILE_PART",
            lower="part-001",
            upper="part-002",
            operation_hash="op",
        )
        assert memory.rows[key]["attempts"] == attempt
        store.mark_failed(key, "boom")

    store.mark_running(
        task_key=key,
        task_group="PARSE",
        task_index=1,
        task_total=1,
        partition_kind="FILE_PART",
        lower="part-001",
        upper="part-002",
        operation_hash="op",
    )
    store.mark_success(key)

    assert memory.rows[key]["attempts"] == 3
    assert store.assert_complete() == {"RUNNING": 0, "SUCCESS": 1, "FAILED": 0}


def test_completion_fails_closed_for_running_or_failed_units() -> None:
    memory = _MemoryPersistence()
    store = _store(memory)
    key = store.task_key(
        operation_hash="op",
        partition_kind="CUSTOM",
        lower=None,
        upper=None,
    )
    store.mark_running(
        task_key=key,
        task_group="AUDIT",
        task_index=1,
        task_total=1,
        partition_kind="CUSTOM",
        lower=None,
        upper=None,
        operation_hash="op",
    )

    with pytest.raises(RuntimeError, match="ledger incomplete"):
        store.assert_complete()

    store.mark_failed(key, "audit failed")
    with pytest.raises(RuntimeError, match="ledger incomplete"):
        store.assert_complete()


def test_invalid_progress_index_is_rejected_before_persistence() -> None:
    memory = _MemoryPersistence()
    store = _store(memory)

    with pytest.raises(ValueError, match="progress index"):
        store.mark_running(
            task_key="x",
            task_group="PUBLISH",
            task_index=3,
            task_total=2,
            partition_kind="APPLICATION_RANGE",
            lower=None,
            upper=None,
            operation_hash="op",
        )

    assert memory.rows == {}


def test_contract_freezes_resume_and_legal_semantics() -> None:
    contract = work_engine_contract()

    assert contract["version"] == "MARKORBIT_WORK_ENGINE_V1"
    assert contract["role"] == "DURABLE_IDEMPOTENT_RESUMABLE_WORK_UNITS"
    assert contract["work_unit_identity"] == [
        "owner_scope",
        "job_id",
        "checkpoint_version",
        "task_key",
    ]
    assert contract["persistence_identity"] == ["job_id", "task_key"]
    assert "job_id" not in contract["task_key_identity"]
    assert contract["task_key_job_local"] is True
    assert contract["resume_policy"]["skip_only_matching_success"] is True
    assert contract["resume_policy"]["checkpoint_artifact_validation_required"] is True
    assert contract["resume_policy"]["cleanup_only_after_job_success"] is True
    assert contract["legal_conclusion"] is False
    assert "APPLICATION_RANGE" in contract["partition_kinds"]
