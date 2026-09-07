from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path

import pytest

from app.us.target_canary import (
    APPLICATION_CANARY_TABLES,
    QueryRows,
    freeze_package,
    stage_table_map,
)
from app.us.target_canary_journal import (
    _seal,
    commit_staged_tables,
    initialize_canary_journal,
    load_canary_journal,
    mark_stage_complete,
    mark_stage_started,
    reset_interrupted_staging_after_verified_zero_final,
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
        self.stage_reverse = {value: key for key, value in self.stage_map.items()}
        self.stage_total = {self.stage_map[table]: count for table, count in counts.items()}
        self.stage_matching = dict(self.stage_total)
        self.stage_visible = dict(counts)
        self.final_count = {table: 0 for table in APPLICATION_CANARY_TABLES}
        self.final_visible = {table: 0 for table in APPLICATION_CANARY_TABLES}
        self.commands: list[str] = []
        self.raise_before_commit_for: str | None = None
        self.raise_after_commit_for: str | None = None
        self.partial_after_commit_for: str | None = None

    def query(self, sql: str) -> QueryRows:
        if "countIf(" in sql:
            stage_table = sql.split("FROM ", 1)[1].strip()
            return QueryRows(
                result_rows=[[self.stage_total[stage_table], self.stage_matching[stage_table]]]
            )
        table = sql.split("FROM ", 1)[1].split(" WHERE ", 1)[0].strip()
        if "uniqExact(" in sql:
            if table in self.stage_reverse:
                value = self.stage_visible[self.stage_reverse[table]]
            else:
                value = self.final_visible[table]
            return QueryRows(result_rows=[[value]])
        if table in self.stage_reverse:
            return QueryRows(result_rows=[[self.stage_total[table]]])
        return QueryRows(result_rows=[[self.final_count[table]]])

    def command(self, sql: str) -> str:
        self.commands.append(sql)
        table = sql.split("INSERT INTO ", 1)[1].split(" SELECT ", 1)[0].strip()
        expected = self.counts[table]
        visible = self.stage_visible[table]
        if self.raise_before_commit_for == table:
            self.raise_before_commit_for = None
            raise RuntimeError("simulated transport loss before server commit")
        if self.partial_after_commit_for == table:
            self.final_count[table] = max(visible - 1, 1)
            self.final_visible[table] = max(visible - 1, 1)
            return ""
        self.final_count[table] = visible if visible < expected else expected
        self.final_visible[table] = visible
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


def test_zero_visible_rows_after_uncertain_insert_requires_explicit_reconciliation(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path)
    counts = {table: 2 for table in APPLICATION_CANARY_TABLES}
    client = FakeClient(package, counts)
    journal = _ready_journal(tmp_path, package, client, counts)
    first = APPLICATION_CANARY_TABLES[0]
    client.raise_before_commit_for = first

    with pytest.raises(RuntimeError, match="before server commit"):
        commit_staged_tables(
            client,
            journal,
            package=package,
            schema_manifest_sha256=SCHEMA_SHA,
        )
    command_count = len(client.commands)

    after_failure = load_canary_journal(
        journal,
        package=package,
        schema_manifest_sha256=SCHEMA_SHA,
    )
    assert after_failure["commits"][first]["status"] == "INSERT_STARTED"
    assert client.final_count[first] == 0

    with pytest.raises(RuntimeError, match="in-flight reconciliation"):
        commit_staged_tables(
            client,
            journal,
            package=package,
            schema_manifest_sha256=SCHEMA_SHA,
        )
    assert len(client.commands) == command_count


def test_preexisting_package_rows_before_insert_boundary_fail_closed(tmp_path: Path) -> None:
    package = _package(tmp_path)
    counts = {table: 2 for table in APPLICATION_CANARY_TABLES}
    client = FakeClient(package, counts)
    journal = _ready_journal(tmp_path, package, client, counts)
    first = APPLICATION_CANARY_TABLES[0]
    client.final_count[first] = counts[first]

    with pytest.raises(RuntimeError, match="pre-existing package rows"):
        commit_staged_tables(
            client,
            journal,
            package=package,
            schema_manifest_sha256=SCHEMA_SHA,
        )
    assert client.commands == []


def test_partial_final_count_fails_closed_and_is_not_retried(tmp_path: Path) -> None:
    package = _package(tmp_path)
    counts = {table: 3 for table in APPLICATION_CANARY_TABLES}
    client = FakeClient(package, counts)
    journal = _ready_journal(tmp_path, package, client, counts)
    first = APPLICATION_CANARY_TABLES[0]
    client.partial_after_commit_for = first

    with pytest.raises(RuntimeError, match="did not reach exact replacement-visible count"):
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
    package.path.write_bytes(b"x" * package.size_bytes)

    with pytest.raises(RuntimeError, match="source SHA-256 changed"):
        commit_staged_tables(
            client,
            journal,
            package=package,
            schema_manifest_sha256=SCHEMA_SHA,
        )
    assert client.commands == []


def test_interrupted_staging_can_reset_only_after_verified_zero_final(tmp_path: Path) -> None:
    package = _package(tmp_path)
    journal = tmp_path / "canary-journal.json"
    initialize_canary_journal(journal, package=package, schema_manifest_sha256=SCHEMA_SHA)
    mark_stage_started(journal, package=package, schema_manifest_sha256=SCHEMA_SHA)
    zeros = {table: 0 for table in APPLICATION_CANARY_TABLES}
    recovered = reset_interrupted_staging_after_verified_zero_final(
        journal,
        package=package,
        schema_manifest_sha256=SCHEMA_SHA,
        verified_final_row_counts=zeros,
        removed_stage_table_count=len(APPLICATION_CANARY_TABLES),
    )
    assert recovered["state"] == "PREPARED"
    assert recovered["stage"]["status"] == "NOT_STARTED"
    assert recovered["staging_recoveries"][-1]["reason"] == (
        "VERIFIED_ZERO_FINAL_ROWS_AFTER_INTERRUPTED_STAGING"
    )


def test_interrupted_staging_reset_rejects_visible_final_rows(tmp_path: Path) -> None:
    package = _package(tmp_path)
    journal = tmp_path / "canary-journal.json"
    initialize_canary_journal(journal, package=package, schema_manifest_sha256=SCHEMA_SHA)
    mark_stage_started(journal, package=package, schema_manifest_sha256=SCHEMA_SHA)
    counts = {table: 0 for table in APPLICATION_CANARY_TABLES}
    counts[APPLICATION_CANARY_TABLES[0]] = 1
    with pytest.raises(RuntimeError, match="requires zero final rows"):
        reset_interrupted_staging_after_verified_zero_final(
            journal,
            package=package,
            schema_manifest_sha256=SCHEMA_SHA,
            verified_final_row_counts=counts,
            removed_stage_table_count=1,
        )


def test_replacing_visible_count_recovers_without_duplicate_insert(tmp_path: Path) -> None:
    package = _package(tmp_path)
    counts = {table: 2 for table in APPLICATION_CANARY_TABLES}
    owner = "markorbit_facts.us_owner_current"
    counts[owner] = 5
    client = FakeClient(package, counts)
    client.stage_visible[owner] = 3
    journal = _ready_journal(tmp_path, package, client, counts)
    client.raise_after_commit_for = owner

    with pytest.raises(RuntimeError, match="transport loss"):
        commit_staged_tables(
            client, journal, package=package, schema_manifest_sha256=SCHEMA_SHA
        )
    after_failure = load_canary_journal(
        journal, package=package, schema_manifest_sha256=SCHEMA_SHA
    )
    assert after_failure["commits"][owner]["status"] == "INSERT_STARTED"
    assert after_failure["commits"][owner]["expected_rows"] == 5
    assert after_failure["commits"][owner]["expected_visible_rows"] == 3
    assert client.final_count[owner] == 3

    result = commit_staged_tables(
        client, journal, package=package, schema_manifest_sha256=SCHEMA_SHA
    )
    assert result["commits"][owner]["status"] == "COMMITTED"
    assert result["commits"][owner]["recovered_after_uncertain_insert"] is True
    assert result["commits"][owner]["observed_visible_rows"] == 3
    assert client.commands.count(result["commits"][owner]["statement"]) == 1


def test_legacy_insert_started_journal_backfills_visible_expectation(tmp_path: Path) -> None:
    package = _package(tmp_path)
    counts = {table: 2 for table in APPLICATION_CANARY_TABLES}
    owner = "markorbit_facts.us_owner_current"
    counts[owner] = 5
    client = FakeClient(package, counts)
    client.stage_visible[owner] = 3
    journal = _ready_journal(tmp_path, package, client, counts)
    raw = json.loads(journal.read_text(encoding="utf-8"))
    raw["state"] = "COMMITTING"
    raw["commits"][owner]["status"] = "INSERT_STARTED"
    raw["commits"][owner]["observed_rows"] = 0
    for item in raw["commits"].values():
        item.pop("expected_visible_rows", None)
        item.pop("observed_visible_rows", None)
        item.pop("replacement_collapsed_rows", None)
    journal.write_text(
        json.dumps(_seal(raw), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    client.final_count[owner] = 3
    client.final_visible[owner] = 3

    result = commit_staged_tables(
        client, journal, package=package, schema_manifest_sha256=SCHEMA_SHA
    )
    commit = result["commits"][owner]
    assert commit["expected_rows"] == 5
    assert commit["expected_visible_rows"] == 3
    assert commit["replacement_collapsed_rows"] == 2
    assert commit["status"] == "COMMITTED"
    assert commit["recovered_after_uncertain_insert"] is True
    assert client.commands.count(commit["statement"]) == 0
