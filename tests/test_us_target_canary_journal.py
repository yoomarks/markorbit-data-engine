from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path

import pytest

from app.us.target_canary import APPLICATION_CANARY_TABLES, QueryRows, freeze_package, stage_table_map
from app.us.target_canary_journal import (
    commit_staged_tables,
    initialize_canary_journal,
    load_canary_journal,
    mark_stage_complete,
    mark_stage_started,
)


SCHEMA_SHA = "a" * 64


def _package(tmp_path: Path):
    source = tmp_path / "apc260102.zip"
    source.write_bytes(b"bounded-us-canary")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return freeze_package(
        source,
        expected_size=source.stat().st_size,
        expected_sha256=digest,
        package_kind="APPLICATION_DAILY",
        source_rank=200,
        source_effective_date=date(2026, 1, 2),
    )


class FakeClient:
    def __init__(self, package, counts: dict[str, int]) -> None:
        self.package = package
        self.counts = dict(counts)
        self.stage_map = stage_table_map(package)
        self.stage_total = {self.stage_map[table]: count for table, count in counts.items()}
        self.stage_matching = dict(self.stage_total)
        self.final_count = {table: 0 for table in APPLICATION_CANARY_TABLES}
        self.commands: list[str] = []
        self.raise_after_commit_for: str | None = None
        self.partial_after_commit_for: str | None = None

    def query(self, sql: str) -> QueryRows:
        if "countIf(" in sql:
            stage_table = sql.split("FROM ", 1)[1].strip()
            return QueryRows(
                result_rows=[[self.stage_total[stage_table], self.stage_matching[stage_table]]]
            )
        table = sql.split("FROM ", 1)[1].split(" WHERE ", 1)[0].strip()
        return QueryRows(result_rows=[[self.final_count[table]]])

    def command(self, sql: str) -> str:
        self.commands.append(sql)
        table = sql.split("INSERT INTO ", 1)[1].split(" SELECT ", 1)[0].strip()
        expected = self.counts[table]
        if self.partial_after_commit_for == table:
            self.final_count[table] = max(expected - 1, 1)
            return ""
        self.final_count[table] = expected
        if self.raise_after_commit_for == table:
            self.raise_after_commit_for = None
            raise RuntimeError("simulated transport loss after server commit")
        return ""


def _ready_journal(tmp_path: Path, package, client: FakeClient, counts: dict[str, int]) -> Path:
    journal = tmp_path / "canary-journal.json"
    initialize_canary_journal(journal, package=package, schema_manifest_sha256=SCHEMA_SHA)
    mark_stage_started(journal, package=package, schema_manifest_sha256=SCHEMA_SHA)
    mark_stage_complete(
        client,
        journal,
        package=package,
        schema_manifest_sha256=SCHEMA_SHA,
        expected_row_counts=counts,
    )
    return journal


def test_journal_is_package_schema_and_integrity_bound(tmp_path: Path) -> None:
    package = _package(tmp_path)
    journal = tmp_path / "canary-journal.json"
    payload = initialize_canary_journal(
        journal,
        package=package,
        schema_manifest_sha256=SCHEMA_SHA,
    )

    assert payload["state"] == "PREPARED"
    assert payload["package"] == package.as_dict()
    assert payload["schema_manifest_sha256"] == SCHEMA_SHA
    assert len(payload["integrity_sha256"]) == 64

    raw = json.loads(journal.read_text(encoding="utf-8"))
    raw["state"] = "COMPLETE"
    journal.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RuntimeError, match="integrity mismatch"):
        load_canary_journal(
            journal,
            package=package,
            schema_manifest_sha256=SCHEMA_SHA,
        )


def test_stage_started_cannot_be_blindly_restarted(tmp_path: Path) -> None:
    package = _package(tmp_path)
    journal = tmp_path / "canary-journal.json"
    initialize_canary_journal(journal, package=package, schema_manifest_sha256=SCHEMA_SHA)
    mark_stage_started(journal, package=package, schema_manifest_sha256=SCHEMA_SHA)

    with pytest.raises(RuntimeError, match="cannot be restarted blindly"):
        mark_stage_started(journal, package=package, schema_manifest_sha256=SCHEMA_SHA)


def test_stage_completion_requires_exact_total_and_package_counts(tmp_path: Path) -> None:
    package = _package(tmp_path)
    counts = {table: 2 for table in APPLICATION_CANARY_TABLES}
    client = FakeClient(package, counts)
    journal = tmp_path / "canary-journal.json"
    initialize_canary_journal(journal, package=package, schema_manifest_sha256=SCHEMA_SHA)
    mark_stage_started(journal, package=package, schema_manifest_sha256=SCHEMA_SHA)

    first = APPLICATION_CANARY_TABLES[0]
    client.stage_matching[client.stage_map[first]] = 1
    with pytest.raises(RuntimeError, match="stage verification failed closed"):
        mark_stage_complete(
            client,
            journal,
            package=package,
            schema_manifest_sha256=SCHEMA_SHA,
            expected_row_counts=counts,
        )


def test_normal_commit_is_table_by_table_and_completes(tmp_path: Path) -> None:
    package = _package(tmp_path)
    counts = {table: index + 1 for index, table in enumerate(APPLICATION_CANARY_TABLES)}
    client = FakeClient(package, counts)
    journal = _ready_journal(tmp_path, package, client, counts)

    result = commit_staged_tables(
        client,
        journal,
        package=package,
        schema_manifest_sha256=SCHEMA_SHA,
    )

    assert result["state"] == "COMPLETE"
    assert len(client.commands) == len(APPLICATION_CANARY_TABLES)
    for table in APPLICATION_CANARY_TABLES:
        assert result["commits"][table]["status"] == "COMMITTED"
        assert result["commits"][table]["observed_rows"] == counts[table]
        assert client.final_count[table] == counts[table]


def test_transport_loss_after_server_commit_recovers_without_duplicate_insert(tmp_path: Path) -> None:
    package = _package(tmp_path)
    counts = {table: 2 for table in APPLICATION_CANARY_TABLES}
    client = FakeClient(package, counts)
    journal = _ready_journal(tmp_path, package, client, counts)
    first = APPLICATION_CANARY_TABLES[0]
    client.raise_after_commit_for = first

    with pytest.raises(RuntimeError, match="transport loss"):
        commit_staged_tables(
            client,
            journal,
            package=package,
            schema_manifest_sha256=SCHEMA_SHA,
        )

    after_failure = load_canary_journal(
        journal,
        package=package,
        schema_manifest_sha256=SCHEMA_SHA,
    )
    assert after_failure["commits"][first]["status"] == "INSERT_STARTED"
    assert client.commands.count(after_failure["commits"][first]["statement"]) == 1

    result = commit_staged_tables(
        client,
        journal,
        package=package,
        schema_manifest_sha256=SCHEMA_SHA,
    )
    assert result["state"] == "COMPLETE"
    assert result["commits"][first]["recovered_after_uncertain_insert"] is True
    assert client.commands.count(result["commits"][first]["statement"]) == 1


def test_partial_final_count_fails_closed_and_is_not_retried(tmp_path: Path) -> None:
    package = _package(tmp_path)
    counts = {table: 3 for table in APPLICATION_CANARY_TABLES}
    client = FakeClient(package, counts)
    journal = _ready_journal(tmp_path, package, client, counts)
    first = APPLICATION_CANARY_TABLES[0]
    client.partial_after_commit_for = first

    with pytest.raises(RuntimeError, match="did not reach exact expected count"):
        commit_staged_tables(
            client,
            journal,
            package=package,
            schema_manifest_sha256=SCHEMA_SHA,
        )
    command_count = len(client.commands)

    with pytest.raises(RuntimeError, match="partial final-table state detected"):
        commit_staged_tables(
            client,
            journal,
            package=package,
            schema_manifest_sha256=SCHEMA_SHA,
        )
    assert len(client.commands) == command_count


def test_source_change_after_checkpoint_blocks_final_commit(tmp_path: Path) -> None:
    package = _package(tmp_path)
    counts = {table: 1 for table in APPLICATION_CANARY_TABLES}
    client = FakeClient(package, counts)
    journal = _ready_journal(tmp_path, package, client, counts)
    package.path.write_bytes(b"changed-after-stage")

    with pytest.raises(RuntimeError, match="source SHA-256 changed"):
        commit_staged_tables(
            client,
            journal,
            package=package,
            schema_manifest_sha256=SCHEMA_SHA,
        )
    assert client.commands == []
