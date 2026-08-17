from __future__ import annotations

from app.contact_ingest import country_inference_runtime as runtime


class _FakeConnection:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


class _FakeCursor:
    def __init__(self) -> None:
        self.connection = _FakeConnection()


def test_unknown_contact_count_commits_long_lived_transaction(monkeypatch) -> None:
    cursor = _FakeCursor()
    monkeypatch.setattr(runtime, "_ORIGINAL_UNKNOWN_CONTACT_COUNT", lambda cur: 54290)

    assert runtime._unknown_contact_count_with_commit(cursor) == 54290
    assert cursor.connection.commits == 1


def test_show_run_emits_persisted_result_without_reprocessing(monkeypatch, capsys) -> None:
    run_id = "40b493e2-b2d7-4921-8583-d5414dcd6fbd"
    monkeypatch.setattr(
        runtime,
        "_country_inference_run",
        lambda value: {
            "run_id": value,
            "status": "SUCCESS",
            "metrics": {"evaluated": 54290, "accepted": 20891},
        },
    )

    assert runtime._show_run(run_id) == 0
    output = capsys.readouterr().out
    assert "CONTACT_COUNTRY_INFERENCE_RUN" in output
    assert '"accepted": 20891' in output


def test_runtime_version_marks_transaction_fix() -> None:
    assert runtime.CONTACT_COUNTRY_RUNTIME_MODEL_VERSION == "CONTACT_COUNTRY_RUNTIME_MODEL_V3"
    assert runtime._CONTACT_CITY_COUNTS_SQL.count("entity.entity_mention") == 0
