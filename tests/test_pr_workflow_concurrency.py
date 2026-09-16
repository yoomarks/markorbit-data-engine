from pathlib import Path


WORKFLOW_DIR = Path(".github/workflows")
EXPECTED_GROUP = (
    "group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}"
)
EXPECTED_CANCEL = "cancel-in-progress: ${{ github.event_name == 'pull_request' }}"
UNSCOPED_PR_ALLOWLIST = {"ci.yml", "platformization-static-checkpoint.yml"}
STORAGE_RUNTIME_SCRIPT_PATHS = {
    "global-multi-disk-ext4-spike-preflight-runtime.yml": {
        "scripts/preflight-global-multi-disk-ext4-spike.ps1",
    },
    "global-multi-disk-ext4-spike-runtime.yml": {
        "scripts/run-global-multi-disk-ext4-spike.ps1",
    },
    "clickhouse-linux-volume-final-acceptance-runtime.yml": {
        "scripts/finalize-clickhouse-linux-volume-acceptance.ps1",
        "scripts/stop-idle-worker.ps1",
    },
    "clickhouse-linux-volume-recovery-runtime.yml": set(),
    "wsl-ext4-tooling-distro-runtime.yml": {
        "scripts/ensure-wsl-ext4-tooling-distro.ps1",
    },
    "linux-volume-us-capacity-profile-runtime.yml": {
        "scripts/profile-linux-volume-us-capacity-target-host.ps1",
        "scripts/assert-clickhouse-active-hot-storage-contract.ps1",
        "scripts/profile-storage-capacity.ps1",
    },
    "dedicated-wsl-clickhouse-preflight-runtime.yml": {
        "scripts/preflight-dedicated-wsl-clickhouse-spike.ps1",
    },
    "global-multi-disk-host-inventory-runtime.yml": {
        "scripts/inventory-global-multi-disk-host.ps1",
    },
}


def _workflow_texts() -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in sorted(WORKFLOW_DIR.glob("*.yml"))}


def test_unscoped_pr_allowlist_is_exact_repository_wide_gates() -> None:
    assert UNSCOPED_PR_ALLOWLIST == {
        "ci.yml",
        "platformization-static-checkpoint.yml",
    }


def test_every_pull_request_workflow_uses_server_side_concurrency() -> None:
    workflows = _workflow_texts()
    pr_workflows = {path: text for path, text in workflows.items() if "\n  pull_request:" in text}

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
        path.name for path, text in _workflow_texts().items() if "pull_request_target:" in text
    ]
    assert offenders == []


def test_manual_full_corpus_acceptance_remains_non_cancelling() -> None:
    text = (WORKFLOW_DIR / "ipos-sg-full-corpus-acceptance.yml").read_text(encoding="utf-8")
    assert "pull_request:" not in text
    assert "group: ipos-sg-full-corpus-manual" in text
    assert "cancel-in-progress: false" in text


def test_domain_specific_pull_request_workflows_are_path_scoped() -> None:
    offenders: list[str] = []
    for path, text in _workflow_texts().items():
        if "\n  pull_request:" not in text or path.name in UNSCOPED_PR_ALLOWLIST:
            continue
        if (
            "\n  pull_request:\n    paths:" not in text
            and "\n  pull_request:\n    paths-ignore:" not in text
        ):
            offenders.append(path.name)

    assert offenders == [], (
        "Domain/runtime PR workflows must declare affected-scope path filters; "
        f"unscoped: {offenders}"
    )

def test_storage_runtime_workflows_use_audited_script_dependencies() -> None:
    for workflow_name, expected_scripts in STORAGE_RUNTIME_SCRIPT_PATHS.items():
        text = (WORKFLOW_DIR / workflow_name).read_text(encoding="utf-8")
        paths_block = text.split("    paths:\n", 1)[1].split("  push:\n", 1)[0]
        actual_scripts = {
            line.strip()[3:-1]
            for line in paths_block.splitlines()
            if line.strip().startswith("- 'scripts/")
        }

        assert "scripts/**" not in actual_scripts
        assert actual_scripts == expected_scripts, (
            f"{workflow_name} script trigger scope drifted: "
            f"expected {sorted(expected_scripts)}, got {sorted(actual_scripts)}"
        )
