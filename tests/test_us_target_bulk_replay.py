from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.us.package_meta import infer_us_package_descriptor
from app.us.target_bulk_journal import (
    initialize_bulk_journal,
    load_bulk_journal,
    mark_bulk_running,
    mark_package_complete,
    mark_package_final_verified,
)
from app.us.target_bulk_plan import (
    ACCEPTED_PACKAGE1_SHA256,
    ACCEPTED_PACKAGE2_FILE,
    ACCEPTED_PACKAGE2_ID,
    ACCEPTED_PACKAGE2_SHA256,
    ACCEPTED_SCHEMA_MANIFEST_SHA256,
    EXPECTED_SOURCE_COUNT,
    LEGACY_BULK_PLAN_VERSION,
    _canonical_sha256,
    build_bulk_plan,
    validate_bulk_plan,
    validate_stage2_anchor,
)
import app.us.target_bulk_replay as bulk_replay
from app.us.target_canary import APPLICATION_CANARY_TABLES


EXECUTION_MAIN = "a" * 40


def _stage2_receipt() -> dict:
    counts = {table: index + 1 for index, table in enumerate(APPLICATION_CANARY_TABLES)}
    return {
        "receipt_version": "US_TARGET_CANARY_RECEIPT_V1",
        "decision": "BOUNDED_US_APPLICATION_CANARY_STAGE2_PACKAGE2_ACCEPTED",
        "authority": {
            "issue": 526,
            "stage": 2,
            "bounded_package_sequence": 2,
            "consumed": True,
        },
        "package": {
            "sequence": 2,
            "file_name": ACCEPTED_PACKAGE2_FILE,
            "sha256": ACCEPTED_PACKAGE2_SHA256,
            "package_id": ACCEPTED_PACKAGE2_ID,
        },
        "schema": {
            "manifest_sha256": ACCEPTED_SCHEMA_MANIFEST_SHA256,
        },
        "journal": {
            "state": "COMPLETE",
            "expected_row_counts": counts,
            "observed_row_counts": dict(counts),
        },
        "safety": {
            "source_file_preserved": True,
            "package_3_executed": False,
            "full_corpus_executed": False,
            "automatic_next_package": False,
        },
    }


def _source_path(root: Path, sequence: int) -> tuple[Path, Path]:
    canonical = root / f"apc18840407-20251231-{sequence:02d}.zip"
    if sequence == 1:
        actual = root / "apc18840407-20251231-01_9b65bdcb.zip"
        return actual, canonical
    return canonical, canonical


def _fake_preflight(root: Path, count: int = EXPECTED_SOURCE_COUNT) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    steps: list[dict] = []
    for sequence in range(1, count + 1):
        actual, canonical = _source_path(root, sequence)
        actual.write_bytes(b"")
        descriptor = infer_us_package_descriptor(canonical)
        assert descriptor.package_kind != "UNKNOWN"
        if sequence == 1:
            digest = ACCEPTED_PACKAGE1_SHA256
        elif sequence == 2:
            digest = ACCEPTED_PACKAGE2_SHA256
        else:
            digest = f"{sequence:064x}"
        steps.append(
            {
                "sequence": sequence,
                "package_kind": descriptor.package_kind,
                "partition_value": descriptor.partition_value,
                "file_name": actual.name,
                "path": str(actual),
                "location": "archive" if sequence == 1 else "incoming",
                "sha256": digest,
                "needs_staging_from_archive": sequence == 1,
            }
        )
    return {
        "status": "PASS",
        "safe_to_replay": True,
        "source_inventory": {"history_source_count": 91},
        "replay_plan": steps,
    }


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("us-bulk-corpus")
    preflight = _fake_preflight(root)
    receipt = _stage2_receipt()
    return root, preflight, receipt


def _plan(corpus, *, max_packages: int = 2, start_sequence: int = 3) -> dict:
    root, preflight, receipt = corpus
    return build_bulk_plan(
        root,
        execution_main=EXECUTION_MAIN,
        stage2_receipt=receipt,
        start_sequence=start_sequence,
        max_packages=max_packages,
        source_preflight=deepcopy(preflight),
    )


def _all_counts(value: int = 1) -> dict[str, int]:
    return {table: value for table in APPLICATION_CANARY_TABLES}


def test_bulk_plan_freezes_bridge_anchor_and_bounded_suffix(corpus) -> None:
    plan = _plan(corpus, max_packages=2)
    assert plan["read_only"] is True
    assert plan["production_mutation_authorized"] is False
    assert plan["expected_history_parts"] == 91
    assert plan["accepted_source_count"] == EXPECTED_SOURCE_COUNT
    assert plan["bridge_sequence"] == 1
    assert plan["accepted_existing_target_sequence"] == 2
    assert plan["start_sequence"] == 3
    assert plan["end_sequence"] == 4
    assert plan["suffix_package_count"] == 2
    assert [item["sequence"] for item in plan["packages"]] == [1, 3, 4]
    assert plan["packages"][0]["role"] == "PACKAGE1_TARGET_BRIDGE_REQUIRE_OR_ADOPT"
    assert plan["packages"][0]["location"] == "archive"
    assert plan["packages"][0]["sha256"] == ACCEPTED_PACKAGE1_SHA256
    assert plan["accepted_package2_source"]["sequence"] == 2
    assert plan["accepted_package2_source"]["sha256"] == ACCEPTED_PACKAGE2_SHA256
    assert plan["required_authority_token"].endswith(plan["plan_sha256"])
    validate_bulk_plan(plan)


def test_bulk_plan_requires_exactly_one_bound(corpus) -> None:
    root, preflight, receipt = corpus
    with pytest.raises(ValueError, match="exactly one"):
        build_bulk_plan(
            root,
            execution_main=EXECUTION_MAIN,
            stage2_receipt=receipt,
            source_preflight=preflight,
        )
    with pytest.raises(ValueError, match="exactly one"):
        build_bulk_plan(
            root,
            execution_main=EXECUTION_MAIN,
            stage2_receipt=receipt,
            end_sequence=4,
            max_packages=2,
            source_preflight=preflight,
        )
    with pytest.raises(ValueError, match="cannot start before"):
        build_bulk_plan(
            root,
            execution_main=EXECUTION_MAIN,
            stage2_receipt=receipt,
            start_sequence=2,
            max_packages=1,
            source_preflight=preflight,
        )
    with pytest.raises(ValueError, match="exceeds"):
        build_bulk_plan(
            root,
            execution_main=EXECUTION_MAIN,
            stage2_receipt=receipt,
            start_sequence=310,
            max_packages=2,
            source_preflight=preflight,
        )


def test_bulk_plan_rejects_inventory_gap_or_duplicate_identity(corpus) -> None:
    root, preflight, receipt = corpus
    missing = deepcopy(preflight)
    missing["replay_plan"].pop(10)
    with pytest.raises(RuntimeError, match="corpus count drifted"):
        build_bulk_plan(
            root,
            execution_main=EXECUTION_MAIN,
            stage2_receipt=receipt,
            max_packages=1,
            source_preflight=missing,
        )

    duplicate = deepcopy(preflight)
    duplicate["replay_plan"][3]["sha256"] = duplicate["replay_plan"][2]["sha256"]
    with pytest.raises(RuntimeError, match="duplicate SHA-256"):
        build_bulk_plan(
            root,
            execution_main=EXECUTION_MAIN,
            stage2_receipt=receipt,
            max_packages=1,
            source_preflight=duplicate,
        )


def test_bulk_plan_integrity_and_package2_anchor_fail_closed(corpus) -> None:
    plan = _plan(corpus, max_packages=1)
    tampered = deepcopy(plan)
    tampered["packages"][1]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="integrity"):
        validate_bulk_plan(tampered)

    receipt = _stage2_receipt()
    receipt["journal"]["observed_row_counts"] = _all_counts(999)
    with pytest.raises(RuntimeError, match="row counts disagree"):
        validate_stage2_anchor(receipt)


def test_bulk_journal_is_sealed_and_checkpoints_execution_order(corpus, tmp_path) -> None:
    plan = _plan(corpus, max_packages=2)
    journal_path = tmp_path / "bulk.json"
    state_dir = tmp_path / "state"
    payload = initialize_bulk_journal(journal_path, plan=plan, state_dir=state_dir)
    assert payload["state"] == "PREPARED"
    assert set(payload["packages"]) == {"1", "3", "4"}

    mark_bulk_running(journal_path, plan=plan)
    for sequence in (1, 3, 4):
        mark_package_final_verified(
            journal_path,
            plan=plan,
            sequence=sequence,
            final_row_counts=_all_counts(sequence),
        )
        payload = mark_package_complete(
            journal_path,
            plan=plan,
            sequence=sequence,
        )
    assert payload["state"] == "COMPLETE"
    assert payload["completed_package_count"] == 3
    assert payload["last_completed_sequence"] == 4

    raw = json.loads(journal_path.read_text(encoding="utf-8"))
    raw["last_completed_sequence"] = 99
    journal_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RuntimeError, match="integrity"):
        load_bulk_journal(journal_path, plan=plan)


def test_bulk_journal_rejects_different_plan_binding(corpus, tmp_path) -> None:
    plan = _plan(corpus, max_packages=2)
    other = _plan(corpus, max_packages=1)
    journal_path = tmp_path / "bulk.json"
    initialize_bulk_journal(journal_path, plan=plan, state_dir=tmp_path / "state")
    with pytest.raises(RuntimeError, match="binding drifted"):
        load_bulk_journal(journal_path, plan=other)


def test_executor_stops_on_first_error_and_does_not_advance(
    corpus, tmp_path, monkeypatch
) -> None:
    plan = _plan(corpus, max_packages=2)
    receipt = _stage2_receipt()
    journal_path = tmp_path / "bulk.json"
    calls: list[int] = []

    monkeypatch.setattr(bulk_replay, "_verify_package2_target", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        bulk_replay,
        "audit_bulk_plan",
        lambda **kwargs: {"journal_state": "COMPLETE"},
    )

    def commit(item, state):
        sequence = int(item["sequence"])
        calls.append(sequence)
        if sequence == 3:
            raise RuntimeError("synthetic package failure")
        return _all_counts(sequence)

    def cleanup(item, state, counts):
        return None

    with pytest.raises(RuntimeError, match="synthetic package failure"):
        bulk_replay.execute_bulk_plan(
            plan=plan,
            stage2_receipt=receipt,
            journal_path=journal_path,
            state_dir=tmp_path / "state",
            authority_token=plan["required_authority_token"],
            commit_package=commit,
            cleanup_package=cleanup,
        )
    assert calls == [1, 3]
    journal = load_bulk_journal(journal_path, plan=plan)
    assert journal["state"] == "BLOCKED"
    assert journal["blocked"]["sequence"] == 3
    assert journal["blocked"]["automatic_next_package"] is False
    assert journal["packages"]["1"]["status"] == "COMPLETE"
    assert journal["packages"]["4"]["status"] == "PENDING"


def test_executor_resumes_from_checkpoint_without_replaying_complete(
    corpus, tmp_path, monkeypatch
) -> None:
    plan = _plan(corpus, max_packages=2)
    receipt = _stage2_receipt()
    journal_path = tmp_path / "bulk.json"

    monkeypatch.setattr(bulk_replay, "_verify_package2_target", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        bulk_replay,
        "audit_bulk_plan",
        lambda **kwargs: {"journal_state": "COMPLETE"},
    )

    first_calls: list[int] = []

    def first_commit(item, state):
        sequence = int(item["sequence"])
        first_calls.append(sequence)
        if sequence == 3:
            raise RuntimeError("stop once")
        return _all_counts(sequence)

    with pytest.raises(RuntimeError, match="stop once"):
        bulk_replay.execute_bulk_plan(
            plan=plan,
            stage2_receipt=receipt,
            journal_path=journal_path,
            state_dir=tmp_path / "state",
            authority_token=plan["required_authority_token"],
            commit_package=first_commit,
            cleanup_package=lambda item, state, counts: None,
        )

    resumed: list[int] = []

    result = bulk_replay.execute_bulk_plan(
        plan=plan,
        stage2_receipt=receipt,
        journal_path=journal_path,
        state_dir=tmp_path / "state",
        authority_token=plan["required_authority_token"],
        commit_package=lambda item, state: (
            resumed.append(int(item["sequence"])) or _all_counts(int(item["sequence"]))
        ),
        cleanup_package=lambda item, state, counts: None,
    )
    assert first_calls == [1, 3]
    assert resumed == [3, 4]
    assert result["decision"] == bulk_replay.BULK_ACCEPTED_DECISION
    journal = load_bulk_journal(journal_path, plan=plan)
    assert journal["state"] == "COMPLETE"


def test_executor_retries_cleanup_without_recommitting_final_verified(
    corpus, tmp_path, monkeypatch
) -> None:
    plan = _plan(corpus, max_packages=1)
    receipt = _stage2_receipt()
    journal_path = tmp_path / "bulk.json"

    monkeypatch.setattr(bulk_replay, "_verify_package2_target", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        bulk_replay,
        "audit_bulk_plan",
        lambda **kwargs: {"journal_state": "COMPLETE"},
    )

    cleanup_calls: list[int] = []

    def cleanup_once(item, state, counts):
        sequence = int(item["sequence"])
        cleanup_calls.append(sequence)
        if len(cleanup_calls) == 1:
            raise RuntimeError("cleanup interrupted")

    with pytest.raises(RuntimeError, match="cleanup interrupted"):
        bulk_replay.execute_bulk_plan(
            plan=plan,
            stage2_receipt=receipt,
            journal_path=journal_path,
            state_dir=tmp_path / "state",
            authority_token=plan["required_authority_token"],
            commit_package=lambda item, state: _all_counts(int(item["sequence"])),
            cleanup_package=cleanup_once,
        )
    journal = load_bulk_journal(journal_path, plan=plan)
    assert journal["packages"]["1"]["status"] == "FINAL_VERIFIED"

    committed: list[int] = []
    result = bulk_replay.execute_bulk_plan(
        plan=plan,
        stage2_receipt=receipt,
        journal_path=journal_path,
        state_dir=tmp_path / "state",
        authority_token=plan["required_authority_token"],
        commit_package=lambda item, state: (
            committed.append(int(item["sequence"])) or _all_counts()
        ),
        cleanup_package=lambda item, state, counts: None,
    )
    assert 1 not in committed
    assert committed == [3]
    assert result["journal_state"] == "COMPLETE"


def test_executor_rejects_wrong_plan_bound_authority(corpus, tmp_path, monkeypatch) -> None:
    plan = _plan(corpus, max_packages=1)
    monkeypatch.setattr(bulk_replay, "_verify_package2_target", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="authority token"):
        bulk_replay.execute_bulk_plan(
            plan=plan,
            stage2_receipt=_stage2_receipt(),
            journal_path=tmp_path / "bulk.json",
            state_dir=tmp_path / "state",
            authority_token="GO #545 wrong",
            commit_package=lambda item, state: _all_counts(),
            cleanup_package=lambda item, state, counts: None,
        )
    assert not (tmp_path / "bulk.json").exists()


def test_bulk_stage_drop_is_strictly_package_scoped() -> None:
    calls: list[tuple[str, str, bytes]] = []

    class Response:
        status = 200

        def read(self):
            return b""

        def getheader(self, name):
            return None

    class Connection:
        def request(self, method, path, *, body, headers):
            calls.append((method, path, bytes(body)))

        def getresponse(self):
            return Response()

        def close(self):
            pass

    client = bulk_replay.BulkTargetClient(connection=Connection())
    short = APPLICATION_CANARY_TABLES[0].split(".", 1)[1]
    table = f"markorbit_canary_stage.{short}__0123456789abcdef"
    client.drop_bulk_stage_table(table)
    assert calls == [
        (
            "POST",
            "/?wait_end_of_query=1",
            f"DROP TABLE IF EXISTS {table}".encode("utf-8"),
        )
    ]

    with pytest.raises(ValueError):
        client.drop_bulk_stage_table(APPLICATION_CANARY_TABLES[0])
    with pytest.raises(ValueError):
        client.drop_bulk_stage_table(
            "markorbit_canary_stage.not_an_application_table__0123456789abcdef"
        )
    with pytest.raises(ValueError):
        client.drop_bulk_stage_table(f"markorbit_canary_stage.{short}__bad")


def test_selected_package_is_rehashed_immediately_before_mutation(corpus) -> None:
    plan = _plan(corpus, max_packages=1)
    item = plan["packages"][1]
    # The fixture intentionally uses a preflight SHA that does not match its empty file.
    with pytest.raises(RuntimeError, match="SHA-256"):
        bulk_replay._frozen_from_plan(item)



def test_interrupted_staging_recovery_drops_only_package_stage_and_resets(monkeypatch) -> None:
    package = SimpleNamespace(
        package_id="00000000-0000-0000-0000-000000000005",
        sha256="a" * 64,
    )
    journal = {
        "state": "STAGING",
        "stage": {"status": "STARTED", "row_counts": None},
        "commits": {
            table: {"status": "PENDING", "expected_rows": None, "observed_rows": None}
            for table in APPLICATION_CANARY_TABLES
        },
    }
    dropped: list[str] = []
    reset_calls: list[dict] = []
    client = SimpleNamespace(drop_bulk_stage_table=lambda table: dropped.append(table))
    monkeypatch.setattr(bulk_replay, "load_canary_journal", lambda *a, **k: journal)
    monkeypatch.setattr(bulk_replay, "_read_target_manifest", lambda client: {})
    monkeypatch.setattr(
        bulk_replay,
        "_final_package_counts",
        lambda client, package_id: {table: 0 for table in APPLICATION_CANARY_TABLES},
    )
    monkeypatch.setattr(
        bulk_replay,
        "_stage_table_count",
        lambda client, package: 0 if len(dropped) == len(APPLICATION_CANARY_TABLES) else len(APPLICATION_CANARY_TABLES),
    )
    monkeypatch.setattr(bulk_replay, "_verify_storage", lambda client: {})
    monkeypatch.setattr(bulk_replay, "assert_package_unchanged", lambda package: None)
    monkeypatch.setattr(
        bulk_replay,
        "reset_interrupted_staging_after_verified_zero_final",
        lambda *a, **k: reset_calls.append(k),
    )
    bulk_replay._recover_interrupted_staging(
        client,
        journal_path=Path("package.canary.json"),
        package=package,
    )
    assert dropped == list(bulk_replay.stage_table_map(package).values())
    assert reset_calls[0]["removed_stage_table_count"] == len(APPLICATION_CANARY_TABLES)
    assert all(value == 0 for value in reset_calls[0]["verified_final_row_counts"].values())


def test_interrupted_staging_recovery_refuses_visible_final_rows(monkeypatch) -> None:
    package = SimpleNamespace(
        package_id="00000000-0000-0000-0000-000000000005",
        sha256="a" * 64,
    )
    journal = {
        "state": "STAGING",
        "stage": {"status": "STARTED", "row_counts": None},
        "commits": {
            table: {"status": "PENDING", "expected_rows": None, "observed_rows": None}
            for table in APPLICATION_CANARY_TABLES
        },
    }
    dropped: list[str] = []
    client = SimpleNamespace(drop_bulk_stage_table=lambda table: dropped.append(table))
    counts = {table: 0 for table in APPLICATION_CANARY_TABLES}
    counts[APPLICATION_CANARY_TABLES[0]] = 1
    monkeypatch.setattr(bulk_replay, "load_canary_journal", lambda *a, **k: journal)
    monkeypatch.setattr(bulk_replay, "_read_target_manifest", lambda client: {})
    monkeypatch.setattr(bulk_replay, "_final_package_counts", lambda client, package_id: counts)
    monkeypatch.setattr(bulk_replay, "assert_package_unchanged", lambda package: None)
    with pytest.raises(RuntimeError, match="visible final rows"):
        bulk_replay._recover_interrupted_staging(
            client,
            journal_path=Path("package.canary.json"),
            package=package,
        )
    assert dropped == []


def test_complete_canary_counts_use_replacement_visible_cardinality() -> None:
    commits = {}
    for table in APPLICATION_CANARY_TABLES:
        commits[table] = {
            "status": "COMMITTED",
            "expected_rows": 10,
            "expected_visible_rows": 10,
            "observed_rows": 10,
            "observed_visible_rows": 10,
        }
    owner = "markorbit_facts.us_owner_current"
    commits[owner].update(
        expected_rows=5,
        expected_visible_rows=3,
        observed_rows=3,
        observed_visible_rows=3,
    )
    counts = bulk_replay._expected_counts_from_canary({"commits": commits})
    assert counts[owner] == 3
    assert counts["markorbit_facts.us_case_observation_history"] == 10


def test_final_package_counts_are_logical_for_replacing_tables() -> None:
    class Client:
        def __init__(self) -> None:
            self.sql: list[str] = []

        def query(self, sql: str):
            self.sql.append(sql)
            value = 3 if "uniqExact(" in sql else 4
            return SimpleNamespace(result_rows=[[value]])

    client = Client()
    counts = bulk_replay._final_package_counts(
        client,
        "6db93db0-77a5-5fc2-a2e3-eef6f05e5da4",
    )
    assert counts["markorbit_facts.us_owner_current"] == 3
    assert counts["markorbit_facts.us_case_observation_history"] == 4
    assert any("uniqExact(tuple(serial_number, owner_key))" in sql for sql in client.sql)


def test_frozen_plan_source_resolves_exact_archive_copy_after_incoming_move(tmp_path: Path) -> None:
    import hashlib

    raw_root = tmp_path / "raw"
    incoming = raw_root / "incoming" / "us"
    archive = raw_root / "archive" / "us"
    incoming.mkdir(parents=True)
    archive.mkdir(parents=True)
    source = incoming / "apc20260102.zip"
    source.write_bytes(b"bounded-source-relocation")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    item = {
        "path": str(source),
        "file_name": source.name,
        "size_bytes": source.stat().st_size,
        "sha256": digest,
        "package_kind": "APPLICATION_DAILY",
        "source_rank": 2026010200000001,
        "source_effective_date": "2026-01-02",
        "package_id": "00000000-0000-0000-0000-000000000008",
    }
    archived = archive / source.name
    source.replace(archived)
    frozen = bulk_replay._frozen_from_plan(item)
    assert frozen.path == archived.resolve()
    assert frozen.sha256 == digest
    assert frozen.size_bytes == archived.stat().st_size


def test_frozen_plan_source_does_not_hide_tampered_planned_file(tmp_path: Path) -> None:
    import hashlib

    incoming = tmp_path / "raw" / "incoming" / "us"
    archive = tmp_path / "raw" / "archive" / "us"
    incoming.mkdir(parents=True)
    archive.mkdir(parents=True)
    source = incoming / "apc20260102.zip"
    source.write_bytes(b"expected")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    (archive / source.name).write_bytes(b"expected")
    source.write_bytes(b"tampered")
    item = {
        "path": str(source),
        "file_name": source.name,
        "size_bytes": len(b"expected"),
        "sha256": digest,
        "package_kind": "APPLICATION_DAILY",
        "source_rank": 2026010200000001,
        "source_effective_date": "2026-01-02",
        "package_id": "00000000-0000-0000-0000-000000000008",
    }
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        bulk_replay._frozen_from_plan(item)


def test_bulk_plan_v1_artifact_remains_valid(corpus) -> None:
    plan = _plan(corpus, max_packages=1)
    legacy = deepcopy(plan)
    legacy["plan_version"] = LEGACY_BULK_PLAN_VERSION
    legacy.pop("accepted_prefix", None)
    contract = {
        key: value
        for key, value in legacy.items()
        if key not in {"plan_sha256", "required_authority_token"}
    }
    digest = _canonical_sha256(contract)
    legacy["plan_sha256"] = digest
    legacy["required_authority_token"] = (
        f"GO #545 bounded US Application bulk replay {digest}"
    )
    validate_bulk_plan(legacy)


def test_bulk_plan_supports_verified_incremental_suffix(monkeypatch, tmp_path) -> None:
    root = tmp_path / "corpus343"
    preflight = _fake_preflight(root, count=343)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    for sequence in [1, *range(3, 311)]:
        (state_dir / f"package_{sequence:03d}_{sequence:016x}.canary.json").write_text("{}")
    monkeypatch.setattr(
        "app.us.target_bulk_plan.load_canary_journal",
        lambda *args, **kwargs: {"state": "COMPLETE"},
    )
    plan = build_bulk_plan(
        root,
        execution_main=EXECUTION_MAIN,
        stage2_receipt=_stage2_receipt(),
        start_sequence=311,
        end_sequence=343,
        source_preflight=preflight,
        accepted_state_dir=state_dir,
    )
    assert plan["accepted_source_count"] == 343
    assert plan["accepted_prefix"]["through_sequence"] == 310
    assert plan["accepted_prefix"]["canary_evidence_count"] == 309
    assert [item["sequence"] for item in plan["packages"]][1:] == list(range(311, 344))


def test_incremental_plan_fails_closed_when_prefix_evidence_missing(monkeypatch, tmp_path) -> None:
    root = tmp_path / "corpus343_missing"
    preflight = _fake_preflight(root, count=343)
    state_dir = tmp_path / "state_missing"
    state_dir.mkdir()
    for sequence in [1, *range(3, 311)]:
        if sequence != 200:
            (state_dir / f"package_{sequence:03d}_{sequence:016x}.canary.json").write_text("{}")
    monkeypatch.setattr(
        "app.us.target_bulk_plan.load_canary_journal",
        lambda *args, **kwargs: {"state": "COMPLETE"},
    )
    with pytest.raises(RuntimeError, match="sequence=200"):
        build_bulk_plan(
            root,
            execution_main=EXECUTION_MAIN,
            stage2_receipt=_stage2_receipt(),
            start_sequence=311,
            end_sequence=343,
            source_preflight=preflight,
            accepted_state_dir=state_dir,
        )


def test_incremental_plan_can_freeze_current_corpus_end(monkeypatch, tmp_path) -> None:
    root = tmp_path / "corpus343_current_end"
    preflight = _fake_preflight(root, count=343)
    state_dir = tmp_path / "state_current_end"
    state_dir.mkdir()
    for sequence in [1, *range(3, 311)]:
        (state_dir / f"package_{sequence:03d}_{sequence:016x}.canary.json").write_text("{}")
    monkeypatch.setattr(
        "app.us.target_bulk_plan.load_canary_journal",
        lambda *args, **kwargs: {"state": "COMPLETE"},
    )
    plan = build_bulk_plan(
        root,
        execution_main=EXECUTION_MAIN,
        stage2_receipt=_stage2_receipt(),
        start_sequence=311,
        to_current_end=True,
        source_preflight=preflight,
        accepted_state_dir=state_dir,
    )
    assert plan["start_sequence"] == 311
    assert plan["end_sequence"] == 343
    assert plan["suffix_package_count"] == 33
    assert plan["batch_start_sequence"] == 311
