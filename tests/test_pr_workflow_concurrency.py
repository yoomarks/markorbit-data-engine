from pathlib import Path


WORKFLOW_DIR = Path(".github/workflows")
EXPECTED_GROUP = "group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}"
EXPECTED_CANCEL = "cancel-in-progress: ${{ github.event_name == 'pull_request' }}"


def _workflow_texts() -> dict[Path, str]:
    return {
        path: path.read_text(encoding="utf-8")
        for path in sorted(WORKFLOW_DIR.glob("*.yml"))
    }


def test_every_pull_request_workflow_uses_server_side_concurrency() -> None:
    workflows = _workflow_texts()
    pr_workflows = {
        path: text
        for path, text in workflows.items()
        if "\n  pull_request:" in text
    }

    # Repository baseline at introduction time. New PR workflows are allowed, but
    # none of the existing coverage may silently disappear from this guard.
    assert len(pr_workflows) >= 31

    missing = []
    for path, text in pr_workflows.items():
        if (
            "\nconcurrency:\n" not in text
            or EXPECTED_GROUP not in text
            or EXPECTED_CANCEL not in text
        ):
            missing.append(path.name)

    assert missing == [], (
        "PR workflows must use GitHub scheduler-level concurrency before runner "
        f"allocation; missing/drifted: {missing}"
    )


def test_no_runner_dependent_pull_request_target_canceller_remains() -> None:
    offenders = [
        path.name
        for path, text in _workflow_texts().items()
        if "pull_request_target:" in text
    ]
    assert offenders == []


def test_manual_full_corpus_acceptance_remains_non_cancelling() -> None:
    text = (WORKFLOW_DIR / "ipos-sg-full-corpus-acceptance.yml").read_text(
        encoding="utf-8"
    )
    assert "pull_request:" not in text
    assert "group: ipos-sg-full-corpus-manual" in text
    assert "cancel-in-progress: false" in text
