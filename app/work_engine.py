from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable


WORK_ENGINE_VERSION = "MARKORBIT_WORK_ENGINE_V1"
WORK_UNIT_STATUSES = ("RUNNING", "SUCCESS", "FAILED")


@dataclass(frozen=True)
class WorkUnitIdentity:
    owner_scope: str
    job_id: str
    checkpoint_version: str
    operation_hash: str
    partition_kind: str
    partition_lower: str | None = None
    partition_upper: str | None = None

    def task_key(self) -> str:
        """Return the stable V1 task key within one durable job scope.

        ``job_id`` is deliberately the outer persistence scope and is not added to the
        V1 hash. This preserves already-established task-key semantics while the full
        work-unit identity is the pair ``(job_id, task_key)``.
        """
        payload = "|".join(
            (
                WORK_ENGINE_VERSION,
                self.owner_scope,
                self.checkpoint_version,
                self.operation_hash,
                self.partition_kind,
                self.partition_lower or "-inf",
                self.partition_upper or "+inf",
            )
        )
        return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WorkUnitSpec:
    job_id: str
    task_key: str
    task_group: str
    task_index: int
    task_total: int
    partition_kind: str
    partition_lower: str | None
    partition_upper: str | None
    operation_hash: str


TaskKeyFactory = Callable[[str, str, str | None, str | None], str]


class DurableWorkUnitStore:
    """Generic durable work-unit state machine backed by caller-supplied persistence.

    The engine deliberately does not own a database schema. A domain adapter supplies
    small persistence callbacks so an existing in-flight checkpoint format can be kept
    stable while the state-machine semantics are shared across jurisdictions.

    ``job_id`` is the owner-neutral durable job scope. Domain adapters may map that
    value onto an existing physical identifier (for example CN ``package_id``) without
    changing their database schema.

    ``task_key_factory`` is intentionally injectable. New domains should use the
    generic owner-scoped identity. Existing domains may supply a compatibility factory
    while migrating so already persisted SUCCESS work is not orphaned by refactoring.
    """

    def __init__(
        self,
        *,
        owner_scope: str,
        job_id: str,
        checkpoint_version: str,
        read_task: Callable[[str], dict[str, Any] | None],
        upsert_running: Callable[[WorkUnitSpec], None],
        set_success: Callable[[str], None],
        set_failed: Callable[[str, str], None],
        summarize: Callable[[], dict[str, int]],
        task_key_factory: TaskKeyFactory | None = None,
    ) -> None:
        normalized_job_id = str(job_id).strip()
        if not normalized_job_id:
            raise ValueError("job_id is required")
        self.owner_scope = owner_scope
        self.job_id = normalized_job_id
        self.checkpoint_version = checkpoint_version
        self._read_task = read_task
        self._upsert_running = upsert_running
        self._set_success = set_success
        self._set_failed = set_failed
        self._summarize = summarize
        self._task_key_factory = task_key_factory

    def task_key(
        self,
        *,
        operation_hash: str,
        partition_kind: str,
        lower: str | None,
        upper: str | None,
    ) -> str:
        if self._task_key_factory is not None:
            return self._task_key_factory(operation_hash, partition_kind, lower, upper)
        return WorkUnitIdentity(
            owner_scope=self.owner_scope,
            job_id=self.job_id,
            checkpoint_version=self.checkpoint_version,
            operation_hash=operation_hash,
            partition_kind=partition_kind,
            partition_lower=lower,
            partition_upper=upper,
        ).task_key()

    def is_success(self, task_key: str, operation_hash: str) -> bool:
        row = self._read_task(task_key)
        return bool(
            row
            and str(row.get("status") or "") == "SUCCESS"
            and str(row.get("operation_hash") or row.get("sql_hash") or "")
            == operation_hash
        )

    def mark_running(
        self,
        *,
        task_key: str,
        task_group: str,
        task_index: int,
        task_total: int,
        partition_kind: str,
        lower: str | None,
        upper: str | None,
        operation_hash: str,
    ) -> None:
        if task_index < 1 or task_total < 1 or task_index > task_total:
            raise ValueError("invalid work-unit progress index")
        if not partition_kind.strip():
            raise ValueError("partition_kind is required")
        self._upsert_running(
            WorkUnitSpec(
                job_id=self.job_id,
                task_key=task_key,
                task_group=task_group,
                task_index=int(task_index),
                task_total=int(task_total),
                partition_kind=partition_kind,
                partition_lower=lower,
                partition_upper=upper,
                operation_hash=operation_hash,
            )
        )

    def mark_success(self, task_key: str) -> None:
        self._set_success(task_key)

    def mark_failed(self, task_key: str, error: str) -> None:
        self._set_failed(task_key, str(error))

    def summary(self) -> dict[str, int]:
        raw = self._summarize()
        result = {status: 0 for status in WORK_UNIT_STATUSES}
        for status, value in raw.items():
            normalized = str(status).upper()
            if normalized not in result:
                raise RuntimeError(f"unsupported durable work-unit status: {status}")
            result[normalized] = int(value or 0)
        return result

    def assert_complete(self) -> dict[str, int]:
        summary = self.summary()
        if summary["RUNNING"] or summary["FAILED"]:
            raise RuntimeError(f"durable work-unit ledger incomplete: {summary}")
        return summary


def work_engine_contract() -> dict[str, Any]:
    task_key_identity = [
        "owner_scope",
        "checkpoint_version",
        "operation_hash",
        "partition_kind",
        "partition_lower",
        "partition_upper",
    ]
    return {
        "version": WORK_ENGINE_VERSION,
        "role": "DURABLE_IDEMPOTENT_RESUMABLE_WORK_UNITS",
        "statuses": list(WORK_UNIT_STATUSES),
        "work_unit_identity": [
            "owner_scope",
            "job_id",
            "checkpoint_version",
            "task_key",
        ],
        "persistence_identity": ["job_id", "task_key"],
        # Compatibility alias retained for existing V1 contract consumers.
        "task_identity": list(task_key_identity),
        "task_key_identity": list(task_key_identity),
        "task_key_job_local": True,
        "resume_policy": {
            "skip_only_matching_success": True,
            "failed_is_rerunnable": True,
            "attempts_are_durable": True,
            "checkpoint_artifact_validation_required": True,
            "completion_fails_closed": True,
            "cleanup_only_after_job_success": True,
            "legacy_task_key_compatibility_supported": True,
        },
        "partition_kinds": [
            "APPLICATION_RANGE",
            "SERIAL_RANGE",
            "FILE_PART",
            "HASH_BUCKET",
            "ENTITY_RANGE",
            "AGENT_CODE_BATCH",
            "CUSTOM",
        ],
        "legal_conclusion": False,
    }
