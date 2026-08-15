from __future__ import annotations

from pathlib import Path

from app.us_assignment import corpus_replay


def test_assignment_replay_runs_boundary_hook_before_package_mutation(monkeypatch) -> None:
    order: list[str] = []
    preflight_calls: list[str] = []
    next_action = {
        "file_name": "assignment-historical.zip",
        "path": "/raw/us_assignment/assignment-historical.zip",
        "effective_date": "2026-01-01",
        "source_kind": "HISTORICAL_SNAPSHOT",
        "action": "REGISTER_AND_INGEST",
        "registry_status": "UNREGISTERED",
    }
    plans = iter(
        [
            {
                "status": "READY",
                "remaining_count": 1,
                "next_action": next_action,
                "blockers": [],
            },
            {
                "status": "READY",
                "remaining_count": 1,
                "next_action": next_action,
                "blockers": [],
            },
            {
                "status": "COMPLETE",
                "remaining_count": 0,
                "next_action": None,
                "blockers": [],
            },
        ]
    )

    monkeypatch.setattr(
        corpus_replay,
        "preflight_manifest",
        lambda manifest, raw_root: preflight_calls.append(str(manifest)) or {"safe": True},
    )
    monkeypatch.setattr(
        corpus_replay,
        "_build_replay_plan_from_preflight",
        lambda preflight: next(plans),
    )
    monkeypatch.setattr(
        corpus_replay,
        "register_assignment_source",
        lambda *args, **kwargs: order.append("register"),
    )
    monkeypatch.setattr(
        corpus_replay,
        "run_assignment_once",
        lambda *args, **kwargs: order.append("ingest") or {"status": "SUCCESS"},
    )

    def before_package(action):
        assert action["file_name"] == "assignment-historical.zip"
        assert order == []
        order.append("boundary")

    result = corpus_replay.execute_replay(
        Path("/raw/manifests/us_assignment/corpus.json"),
        Path("/raw"),
        apply=True,
        all_packages=True,
        resume_failed=True,
        before_package=before_package,
    )

    assert order == ["boundary", "register", "ingest"]
    assert len(preflight_calls) == 1
    assert result["status"] == "COMPLETE"
    assert result["processed_count"] == 1
    assert result["source_preflight_runs"] == 1
    assert result["legal_ownership_conclusion"] is False


def test_assignment_boundary_exception_prevents_registration_and_ingestion(monkeypatch) -> None:
    next_action = {
        "file_name": "assignment-historical.zip",
        "path": "/raw/us_assignment/assignment-historical.zip",
        "effective_date": "2026-01-01",
        "source_kind": "HISTORICAL_SNAPSHOT",
        "action": "REGISTER_AND_INGEST",
        "registry_status": "UNREGISTERED",
    }
    plans = iter(
        [
            {
                "status": "READY",
                "remaining_count": 1,
                "next_action": next_action,
                "blockers": [],
            },
            {
                "status": "READY",
                "remaining_count": 1,
                "next_action": next_action,
                "blockers": [],
            },
        ]
    )
    monkeypatch.setattr(corpus_replay, "preflight_manifest", lambda *args, **kwargs: {"safe": True})
    monkeypatch.setattr(
        corpus_replay,
        "_build_replay_plan_from_preflight",
        lambda preflight: next(plans),
    )
    monkeypatch.setattr(
        corpus_replay,
        "register_assignment_source",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not register")),
    )
    monkeypatch.setattr(
        corpus_replay,
        "run_assignment_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not ingest")),
    )

    class BoundaryStop(RuntimeError):
        pass

    try:
        corpus_replay.execute_replay(
            Path("/raw/manifests/us_assignment/corpus.json"),
            Path("/raw"),
            apply=True,
            all_packages=True,
            resume_failed=True,
            before_package=lambda action: (_ for _ in ()).throw(BoundaryStop("stop")),
        )
    except BoundaryStop as exc:
        assert str(exc) == "stop"
    else:
        raise AssertionError("boundary exception must escape before package mutation")
