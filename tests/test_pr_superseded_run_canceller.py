from pathlib import Path


WORKFLOW = Path(".github/workflows/cancel-superseded-pr-runs.yml")


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_superseded_run_canceller_has_safe_trigger_and_permissions() -> None:
    text = _workflow_text()
    assert "pull_request_target:" in text
    assert "actions: write" in text
    assert "contents: read" in text
    assert "actions/checkout" not in text


def test_superseded_run_canceller_only_targets_old_runs_for_same_pr() -> None:
    text = _workflow_text()
    assert "event: 'pull_request'" in text
    assert "run.head_sha === currentSha" in text
    assert "(run.pull_requests || []).some" in text
    assert "(pr) => pr.number === number" in text
    assert "github.rest.actions.cancelWorkflowRun" in text
    assert "['queued', 'in_progress', 'waiting', 'requested', 'pending']" in text


def test_superseded_run_canceller_retries_registration_race() -> None:
    text = _workflow_text()
    assert "for (let pass = 0; pass < 3; pass += 1)" in text
    assert "await sleep(5000)" in text
