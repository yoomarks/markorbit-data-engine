import pytest

from app.cn.final_publish import ResumableFinalPublishClient, _PUBLISH_STAGE_TABLES


class FakeSubtaskStore:
    def __init__(self, *, summary=None, stage_error: Exception | None = None):
        self._summary = dict(summary or {"SUCCESS": 1040})
        self._stage_error = stage_error
        self.stage_validation_calls: list[tuple[str, ...]] = []

    def assert_complete(self) -> dict[str, int]:
        return dict(self._summary)

    def assert_stage_groups_complete(self, stage_tables: tuple[str, ...]) -> None:
        self.stage_validation_calls.append(stage_tables)
        if self._stage_error is not None:
            raise self._stage_error


def client_with_seen(
    store: FakeSubtaskStore,
    seen: dict[str, int],
) -> ResumableFinalPublishClient:
    client = object.__new__(ResumableFinalPublishClient)
    client._subtask_store = store
    client._stage_commands_seen = seen
    return client


def test_completed_durable_ledger_allows_resume_when_legacy_stage_commands_disappear():
    store = FakeSubtaskStore(summary={"SUCCESS": 1040})
    client = client_with_seen(
        store,
        {table: 0 for table in _PUBLISH_STAGE_TABLES},
    )

    summary = client.assert_final_publish_complete()

    assert summary == {"SUCCESS": 1040}
    assert store.stage_validation_calls == [_PUBLISH_STAGE_TABLES]


def test_missing_legacy_stage_commands_still_fail_closed_for_partial_durable_ledger():
    store = FakeSubtaskStore(
        stage_error=RuntimeError(
            "CN final publish durable stage ledger incomplete: missing_stage_ledgers=cn_stage_scope_publish"
        )
    )
    client = client_with_seen(
        store,
        {table: 0 for table in _PUBLISH_STAGE_TABLES},
    )

    with pytest.raises(RuntimeError, match="durable stage ledger incomplete"):
        client.assert_final_publish_complete()


def test_normal_publish_shape_does_not_need_durable_resume_validation():
    store = FakeSubtaskStore(summary={"SUCCESS": 1040})
    client = client_with_seen(
        store,
        {table: 1 for table in _PUBLISH_STAGE_TABLES},
    )

    summary = client.assert_final_publish_complete()

    assert summary == {"SUCCESS": 1040}
    assert store.stage_validation_calls == []
