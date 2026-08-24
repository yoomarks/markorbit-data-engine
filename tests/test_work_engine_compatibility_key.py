from app.work_engine import DurableWorkUnitStore


class _Memory:
    def read(self, job_id, key):
        return None

    def running(self, spec):
        pass

    def success(self, job_id, key):
        pass

    def failed(self, job_id, key, error):
        pass

    def summary(self, job_id):
        return {}


def test_compatibility_task_key_factory_can_preserve_existing_domain_identity() -> None:
    calls = []

    def legacy_key(operation_hash, partition_kind, lower, upper):
        calls.append((operation_hash, partition_kind, lower, upper))
        return "legacy-key"

    memory = _Memory()
    store = DurableWorkUnitStore(
        owner_scope="CN_FINAL_PUBLISH",
        job_id="package-001",
        checkpoint_version="CN_FINAL_PUBLISH_V1",
        read_task=memory.read,
        upsert_running=memory.running,
        set_success=memory.success,
        set_failed=memory.failed,
        summarize=memory.summary,
        task_key_factory=legacy_key,
    )

    assert store.task_key(
        operation_hash="hash",
        partition_kind="cn_stage_case_publish",
        lower="A",
        upper="B",
    ) == "legacy-key"
    assert calls == [("hash", "cn_stage_case_publish", "A", "B")]
