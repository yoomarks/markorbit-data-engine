from hashlib import sha256
import uuid

from app.cn.publish_subtasks import CHECKPOINT_VERSION, PublishSubtaskStore


def _legacy_key(sql_hash: str, stage_table: str, lower: str | None, upper: str | None) -> str:
    payload = "|".join(
        (
            CHECKPOINT_VERSION,
            sql_hash,
            stage_table,
            lower or "-inf",
            upper or "+inf",
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def test_cn_bridge_preserves_exact_pr137_task_key_identity() -> None:
    store = PublishSubtaskStore(uuid.uuid4())
    sql_hash = "a" * 64

    actual = store.task_key(
        sql_hash=sql_hash,
        stage_table="cn_stage_party_publish",
        lower="100",
        upper="200",
    )

    assert actual == _legacy_key(sql_hash, "cn_stage_party_publish", "100", "200")


def test_cn_bridge_preserves_open_range_task_key_identity() -> None:
    store = PublishSubtaskStore(uuid.uuid4())
    sql_hash = "b" * 64

    assert store.task_key(
        sql_hash=sql_hash,
        stage_table="cn_stage_case_publish",
        lower=None,
        upper=None,
    ) == _legacy_key(sql_hash, "cn_stage_case_publish", None, None)
