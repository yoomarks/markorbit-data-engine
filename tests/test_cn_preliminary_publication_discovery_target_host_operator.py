from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPERATOR = ROOT / "scripts" / "run-cn-preliminary-publication-discovery-target-host-acceptance.ps1"


def test_discovery_target_host_operator_keeps_exact_provider_and_read_only_scope() -> None:
    text = OPERATOR.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "b0ea86788dd77b4e0dbdebf94cf2f76cb672ecb0" in text
    assert "3e31dfea05a56d4a36cbf93520bbdf79148b601c" not in text
    assert "git fetch origin main" in text
    assert "origin/main drifted" in text
    assert "git worktree add --detach $ProviderWorktree $ExpectedSha" in text
    assert "check-cn-serving-state.ps1" in text
    assert "docker compose @ComposeArgs run --rm --no-deps -d" in text
    assert "INTEGRATION_AUTH_MODE=required" in text
    assert "INTEGRATION_RATE_LIMIT_ENABLED=false" in text
    assert "MARKORBIT_DATA_ENGINE_SHA=$ExpectedSha" in text

    forbidden = (
        "docker compose up",
        "docker compose restart",
        "docker compose stop",
        "docker compose down",
        "docker restart",
        "docker stop",
        "truncate table",
        "drop table",
        "alter table",
        "optimize final",
        "systemctl",
    )
    for marker in forbidden:
        assert marker not in lowered


def test_discovery_target_host_operator_requires_explicit_primary_key_bounds() -> None:
    text = OPERATOR.read_text(encoding="utf-8")

    assert "[string]$ApplicationNumberStart" in text
    assert "[string]$ApplicationNumberEnd" in text
    assert "[Parameter(Mandatory = $true)]" in text
    assert "[string]::CompareOrdinal($Start, $End)" in text
    assert "ApplicationNumberStart must be lexically less than ApplicationNumberEnd" in text

    # The official V2 acceptance contract is caller-bounded. The target-host
    # runner must never scan or walk cn_case_current to discover its own range.
    assert "$SampleQuery" not in text
    assert "$SeedQuery" not in text
    assert "$CandidateQuery" not in text
    assert "$SampleWindowSize" not in text
    assert "$MaxSampleWindows" not in text
    assert "Bounded CN sample lookup failed." not in text
    assert "SELECT application_number FROM markorbit_facts.cn_case_current" not in text


def test_discovery_target_host_operator_locks_runtime_read_budgets() -> None:
    text = OPERATOR.read_text(encoding="utf-8")

    assert "payload.query.scope.read_budget.max_rows_to_read -ne 250000" in text
    assert "payload.query.scope.read_budget.max_bytes_to_read -ne 268435456" in text
    assert "payload.query.scope.read_budget.overflow_mode -ne 'throw'" in text
    assert "payload.read_budget.max_rows_to_read -ne 250000" in text
    assert "payload.read_budget.max_bytes_to_read -ne 268435456" in text
    assert "payload.read_budget.read_overflow_mode -ne 'throw'" in text


def test_discovery_target_host_operator_exercises_required_acceptance_and_scrubs_secret() -> None:
    text = OPERATOR.read_text(encoding="utf-8")

    assert "/api/v1/cn/discovery/preliminary-publications" in text
    assert "Unauthenticated integration request unexpectedly succeeded." in text
    assert "Discovery replay body is not byte-identical." in text
    assert "Page 2 query hash drifted." in text
    assert "Page 2 snapshot drifted." in text
    assert "Discovery continuation duplicated candidates." in text
    assert "Cursor/query mismatch unexpectedly succeeded." in text
    assert "Expected cursor/query conflict HTTP 409" in text
    assert "page_size=3&cursor=$CursorEsc" in text
    assert "PHASE4_CN_DISCOVERY_TARGET_HOST_PASS" in text
    assert "PHASE4_CN_PRELIM_DISCOVERY_TARGET_HOST_V1" in text
    assert "business_state_write = $false" in text
    assert "secret_emitted = $false" in text

    # Lock the canonical integration transport contract instead of guessed
    # resource-specific response headers.
    assert "X-Request-ID" in text
    assert "x-correlation-id" in text
    assert "X-MarkOrbit-Contract-Version" in text
    assert "X-MarkOrbit-Source-Owner" in text
    assert "MARKORBIT_DATA_ENGINE_INTEGRATION_V1" in text
    assert "MARKORBIT_DATA_ENGINE" in text
    assert "X-MarkOrbit-Integration-Contract" not in text
    assert "X-MarkOrbit-Resource-Version" not in text

    # A generated bearer exists only in process memory and the Authorization
    # request header. It must never be copied into the receipt or printed.
    assert "api_key = $ApiKey" not in text
    assert "bearer_key = $ApiKey" not in text
    assert "Write-Host $ApiKey" not in text
    assert "Write-Output $ApiKey" not in text


def test_discovery_target_host_operator_cleans_only_its_disposable_resources() -> None:
    text = OPERATOR.read_text(encoding="utf-8")

    assert "$ContainerName = 'markorbit-phase4-cn-discovery-acceptance'" in text
    assert 'docker ps -a --filter "name=^/$ContainerName$"' in text
    assert "docker rm -f $ContainerName" in text
    assert "git worktree remove --force $ProviderWorktree" in text
    assert "Post-acceptance CN serving-state checkpoint is not PASS." in text


def test_discovery_target_host_operator_emits_pass_only_after_cleanup_and_postcheck() -> None:
    text = OPERATOR.read_text(encoding="utf-8")

    cleanup_index = text.index("git worktree remove --force $ProviderWorktree")
    postcheck_index = text.index("Post-acceptance CN serving-state checkpoint is not PASS.")
    pass_index = text.index("PHASE4_CN_DISCOVERY_TARGET_HOST_PASS")

    assert cleanup_index < postcheck_index < pass_index
    assert "$Receipt = $null" in text
    assert "Discovery acceptance completed without a receipt." in text
