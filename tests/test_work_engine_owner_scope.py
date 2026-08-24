from app.work_engine import DurableWorkUnitStore


class _Memory:
    def __init__(self):
        self.rows = {}

    def read(self, job_id, key):
        return self.rows.get((job_id, key))

    def running(self, spec):
        self.rows[(spec.job_id, spec.task_key)] = {
            "status": "RUNNING",
            "operation_hash": spec.operation_hash,
        }

    def success(self, job_id, key):
        self.rows[(job_id, key)]["status"] = "SUCCESS"

    def failed(self, job_id, key, error):
        self.rows[(job_id, key)]["status"] = "FAILED"

    def summary(self, job_id):
        result = {}
        for (row_job_id, _), row in self.rows.items():
            if row_job_id != job_id:
                continue
            result[row["status"]] = result.get(row["status"], 0) + 1
        return result


def _store(memory, owner):
    return DurableWorkUnitStore(
        owner_scope=owner,
        job_id="owner-scope-fixture",
        checkpoint_version="V1",
        read_task=memory.read,
        upsert_running=memory.running,
        set_success=memory.success,
        set_failed=memory.failed,
        summarize=memory.summary,
    )


def test_same_partition_in_two_domains_cannot_share_task_identity() -> None:
    memory = _Memory()
    cn = _store(memory, "CN_FINAL_PUBLISH")
    wipo = _store(memory, "WIPO_FIXTURE")

    cn_key = cn.task_key(
        operation_hash="same-op",
        partition_kind="APPLICATION_RANGE",
        lower="A",
        upper="B",
    )
    wipo_key = wipo.task_key(
        operation_hash="same-op",
        partition_kind="APPLICATION_RANGE",
        lower="A",
        upper="B",
    )

    assert cn_key != wipo_key
