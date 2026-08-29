param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ApplicationNumberStart,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ApplicationNumberEnd
)

$ErrorActionPreference = 'Stop'

$ExpectedSha = 'b0ea86788dd77b4e0dbdebf94cf2f76cb672ecb0'
$ApiPort = 18211
$ContainerName = 'markorbit-phase4-cn-discovery-acceptance'
$BaseUri = "http://127.0.0.1:$ApiPort"
$RepoRoot = Split-Path -Parent $PSScriptRoot

$Start = $ApplicationNumberStart.Trim()
$End = $ApplicationNumberEnd.Trim()
if (-not $Start -or -not $End) {
    throw 'Application-number bounds must be non-empty.'
}
if ([string]::CompareOrdinal($Start, $End) -ge 0) {
    throw "ApplicationNumberStart must be lexically less than ApplicationNumberEnd: $Start .. $End"
}

Push-Location $RepoRoot
try {
    # Exact-provider guard. Do not accept evidence from a drifted provider main.
    git fetch origin main
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch Data Engine origin/main.' }

    $OriginSha = (git rev-parse origin/main).Trim()
    if ($OriginSha -ne $ExpectedSha) {
        throw "Data Engine origin/main drifted: $OriginSha (expected $ExpectedSha). STOP."
    }

    # The operator may live on an unmerged audit branch. Build and execute the
    # frozen provider from origin/main, not from the operator branch.
    $CurrentBranch = (git branch --show-current).Trim()
    $CurrentSha = (git rev-parse HEAD).Trim()
    if (-not $CurrentBranch) { throw 'Target-host acceptance requires a named local branch.' }

    # Existing serving state must already be healthy/quiescent. This checkpoint
    # is read-only and never manages worker/database lifecycle.
    powershell.exe -ExecutionPolicy Bypass -File .\scripts\check-cn-serving-state.ps1 -Compact
    if ($LASTEXITCODE -ne 0) { throw 'CN serving-state checkpoint is not PASS.' }

    docker compose ps postgres clickhouse
    if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect Data Engine databases.' }

    # Discovery V2 is explicitly caller-bounded by application-number primary-key
    # range. This acceptance operator therefore never scans the fact table to
    # discover its own range; the caller supplies an already-authorized small range.
    Write-Host "Discovery bounds: [$Start, $End)"

    # A temporary worktree guarantees the API image is built from the exact
    # provider commit while this operator itself can remain on its audit branch.
    $ProviderWorktree = Join-Path ([System.IO.Path]::GetTempPath()) ("markorbit-de-discovery-provider-{0}" -f [Guid]::NewGuid().ToString('N'))
    $ProviderEnv = Join-Path $ProviderWorktree '.env'
    $SourceEnv = Join-Path $RepoRoot '.env'
    if (-not (Test-Path -LiteralPath $SourceEnv -PathType Leaf)) {
        throw 'Repository .env is required for the exact-provider disposable API.'
    }

    $ContainerStarted = $false
    $Receipt = $null
    try {
        git worktree add --detach $ProviderWorktree $ExpectedSha | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Unable to create exact-provider temporary worktree.' }
        Copy-Item -LiteralPath $SourceEnv -Destination $ProviderEnv

        $ComposeArgs = @(
            '--project-name', 'markorbit-data-engine',
            '--project-directory', $ProviderWorktree,
            '-f', (Join-Path $ProviderWorktree 'docker-compose.yml')
        )

        # Build exact provider API code only. Existing live containers are not
        # recreated or restarted.
        docker compose @ComposeArgs build api
        if ($LASTEXITCODE -ne 0) { throw 'Exact API image build failed.' }

        # Remove only a stale disposable container created by this same operator.
        $Stale = @(docker ps -a --filter "name=^/$ContainerName$" --format '{{.Names}}')
        if ($Stale -contains $ContainerName) {
            docker rm -f $ContainerName | Out-Null
        }

        $ApiKey = 'mo-de-discovery-' + [Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N')
        $Headers = @{
            Authorization = "Bearer $ApiKey"
            'X-Request-ID' = 'phase4-discovery-target-host'
            'x-correlation-id' = 'phase4-discovery-target-host'
        }

        # Start only one disposable auth-required API container. --no-deps means
        # existing Postgres/ClickHouse/worker services are never started here.
        $ContainerId = (
            docker compose @ComposeArgs run --rm --no-deps -d `
                --name $ContainerName `
                -p "127.0.0.1:${ApiPort}:8080" `
                -e 'INTEGRATION_AUTH_MODE=required' `
                -e "INTEGRATION_API_KEYS=$ApiKey" `
                -e 'INTEGRATION_RATE_LIMIT_ENABLED=false' `
                -e "MARKORBIT_DATA_ENGINE_SHA=$ExpectedSha" `
                api
        ).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $ContainerId) {
            throw 'Disposable authenticated API container failed to start.'
        }
        $ContainerStarted = $true

        $ContractResponse = $null
        for ($i = 0; $i -lt 60; $i++) {
            try {
                $ContractResponse = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUri/api/v1/contract" -Headers $Headers -TimeoutSec 5
                if ($ContractResponse.StatusCode -eq 200) { break }
            }
            catch {
                $ContractResponse = $null
            }
            Start-Sleep -Seconds 1
        }
        if ($null -eq $ContractResponse) {
            docker logs $ContainerName
            throw 'Disposable authenticated Data Engine API did not become ready.'
        }

        $Contract = $ContractResponse.Content | ConvertFrom-Json
        if ($Contract.security.auth_mode -ne 'required') { throw 'Integration auth mode is not required.' }

        # Missing bearer auth must fail closed.
        $UnauthStatus = 0
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "$BaseUri/api/v1/contract" -TimeoutSec 5 -ErrorAction Stop | Out-Null
            throw 'Unauthenticated integration request unexpectedly succeeded.'
        }
        catch {
            if ($null -eq $_.Exception.Response) { throw }
            $UnauthStatus = [int]$_.Exception.Response.StatusCode
        }
        if ($UnauthStatus -ne 401) { throw "Expected unauthenticated HTTP 401, got $UnauthStatus." }

        $StartEsc = [Uri]::EscapeDataString($Start)
        $EndEsc = [Uri]::EscapeDataString($End)
        $Path = "/api/v1/cn/discovery/preliminary-publications?application_number_start=$StartEsc&application_number_end=$EndEsc&page_size=2"

        # Page 1 plus deterministic byte-identical replay.
        $Page1Response = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUri$Path" -Headers $Headers -TimeoutSec 30
        $ReplayResponse = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUri$Path" -Headers $Headers -TimeoutSec 30
        if ($Page1Response.Content -cne $ReplayResponse.Content) { throw 'Discovery replay body is not byte-identical.' }

        $Page1 = $Page1Response.Content | ConvertFrom-Json
        if ($Page1.resource_kind -ne 'PRELIMINARY_PUBLICATION_FACT_DISCOVERY') { throw 'Unexpected Discovery resource_kind.' }
        if ($Page1.jurisdiction -ne 'CN') { throw 'Unexpected Discovery jurisdiction.' }
        if ($Page1.legal_conclusion -ne $false) { throw 'Discovery envelope must remain non-legal.' }
        if ($Page1.payload.stream_id -ne 'CN_PRELIMINARY_PUBLICATION_FACT_DISCOVERY_V2') { throw 'Unexpected Discovery stream_id.' }
        if (@($Page1.payload.results).Count -ne 2) { throw 'Page 1 did not return the expected bounded 2 candidates.' }
        if ([string]::IsNullOrWhiteSpace([string]$Page1.payload.next_cursor)) { throw 'Page 1 did not emit a continuation cursor.' }
        if ($Page1.payload.query.scope.ranking -ne 'NONE' -or $Page1.payload.query.scope.joins -ne 'NONE') { throw 'Discovery gained ranking/JOIN semantics.' }
        if ($Page1.payload.query.scope.application_number.start_inclusive -ne $Start -or $Page1.payload.query.scope.application_number.end_exclusive -ne $End) { throw 'Discovery scope does not match exact bounds.' }
        if ($Page1.payload.query.limits.page_size -ne 2) { throw 'Discovery page-size identity mismatch.' }
        if ($Page1.payload.query.scope.read_budget.max_rows_to_read -ne 250000) { throw 'Discovery scope max_rows_to_read drifted.' }
        if ($Page1.payload.query.scope.read_budget.max_bytes_to_read -ne 268435456) { throw 'Discovery scope max_bytes_to_read drifted.' }
        if ($Page1.payload.query.scope.read_budget.overflow_mode -ne 'throw') { throw 'Discovery scope overflow mode drifted.' }
        if ($Page1.payload.read_budget.max_rows_to_read -ne 250000) { throw 'Discovery page max_rows_to_read drifted.' }
        if ($Page1.payload.read_budget.max_bytes_to_read -ne 268435456) { throw 'Discovery page max_bytes_to_read drifted.' }
        if ($Page1.payload.read_budget.read_overflow_mode -ne 'throw') { throw 'Discovery page overflow mode drifted.' }
        if ([string]$Page1.payload.query.query_hash -notmatch '^sha256:[0-9a-f]{64}$') { throw 'Discovery query hash is malformed.' }
        $ExpectedEngineVersion = "git:$ExpectedSha"
        if ($Page1.payload.provenance.engine_version -ne $ExpectedEngineVersion) { throw "Discovery engine lineage mismatch: $($Page1.payload.provenance.engine_version)" }
        if ($Page1.payload.snapshot.source_version -ne $ExpectedEngineVersion) { throw 'Discovery snapshot source_version mismatch.' }

        # Required V1 integration transport headers must echo the accepted trace
        # identity and exact contract/source-owner values.
        $RequestId = [string]$Page1Response.Headers['X-Request-ID']
        $CorrelationId = [string]$Page1Response.Headers['x-correlation-id']
        $ContractVersion = [string]$Page1Response.Headers['X-MarkOrbit-Contract-Version']
        $SourceOwner = [string]$Page1Response.Headers['X-MarkOrbit-Source-Owner']
        if ($RequestId -ne 'phase4-discovery-target-host') { throw "Unexpected response request ID: $RequestId" }
        if ($CorrelationId -ne 'phase4-discovery-target-host') { throw "Unexpected response correlation ID: $CorrelationId" }
        if ($ContractVersion -ne 'MARKORBIT_DATA_ENGINE_INTEGRATION_V1') { throw "Unexpected integration contract version: $ContractVersion" }
        if ($SourceOwner -ne 'MARKORBIT_DATA_ENGINE') { throw "Unexpected integration source owner: $SourceOwner" }

        # Page 2 must continue on the exact same query/snapshot lineage.
        $CursorEsc = [Uri]::EscapeDataString([string]$Page1.payload.next_cursor)
        $Page2Path = "$Path&cursor=$CursorEsc"
        $Page2Response = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUri$Page2Path" -Headers $Headers -TimeoutSec 30
        $Page2 = $Page2Response.Content | ConvertFrom-Json
        if (@($Page2.payload.results).Count -lt 1) { throw 'Page 2 continuation returned no candidate.' }
        if ($Page2.payload.query.query_hash -ne $Page1.payload.query.query_hash) { throw 'Page 2 query hash drifted.' }
        if ($Page2.payload.snapshot.snapshot_id -ne $Page1.payload.snapshot.snapshot_id) { throw 'Page 2 snapshot drifted.' }
        if ($Page2.payload.provenance.page_number -ne 2) { throw 'Page 2 provenance page number mismatch.' }

        $Ids = @($Page1.payload.results) + @($Page2.payload.results) | ForEach-Object { [string]$_.case_id }
        if (@($Ids | Sort-Object -Unique).Count -ne $Ids.Count) { throw 'Discovery continuation duplicated candidates.' }

        # Reusing the original cursor with a different page-size query identity
        # must fail closed. This avoids inventing or scanning for a second bound.
        $ConflictPath = "/api/v1/cn/discovery/preliminary-publications?application_number_start=$StartEsc&application_number_end=$EndEsc&page_size=3&cursor=$CursorEsc"
        $ConflictStatus = 0
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "$BaseUri$ConflictPath" -Headers $Headers -TimeoutSec 30 -ErrorAction Stop | Out-Null
            throw 'Cursor/query mismatch unexpectedly succeeded.'
        }
        catch {
            if ($null -eq $_.Exception.Response) { throw }
            $ConflictStatus = [int]$_.Exception.Response.StatusCode
        }
        if ($ConflictStatus -ne 409) { throw "Expected cursor/query conflict HTTP 409, got $ConflictStatus." }

        # Build the redacted receipt but do not emit PASS yet. PASS is published
        # only after disposable-resource cleanup and the final serving-state
        # checkpoint both succeed.
        $Receipt = [ordered]@{
            acceptance_version = 'PHASE4_CN_PRELIM_DISCOVERY_TARGET_HOST_V1'
            status = 'PASS'
            data_engine_sha = $ExpectedSha
            operator_branch = $CurrentBranch
            operator_sha = $CurrentSha
            endpoint = '/api/v1/cn/discovery/preliminary-publications'
            auth_required = $true
            unauthenticated_status = $UnauthStatus
            request_id = $RequestId
            correlation_id = $CorrelationId
            integration_contract = $ContractVersion
            source_owner = $SourceOwner
            bounds = [ordered]@{ start_inclusive = $Start; end_exclusive = $End }
            page_size = 2
            page1_count = @($Page1.payload.results).Count
            page2_count = @($Page2.payload.results).Count
            query_hash = [string]$Page1.payload.query.query_hash
            snapshot_id = [string]$Page1.payload.snapshot.snapshot_id
            watermark = [string]$Page1.payload.snapshot.watermark
            engine_version = [string]$Page1.payload.provenance.engine_version
            replay_exact = $true
            continuation_exact = $true
            cursor_query_conflict_status = $ConflictStatus
            ranking = [string]$Page1.payload.query.scope.ranking
            joins = [string]$Page1.payload.query.scope.joins
            legal_conclusion = [bool]$Page1.legal_conclusion
            business_state_write = $false
            secret_emitted = $false
        }
    }
    finally {
        # Only the disposable acceptance container and temporary provider
        # worktree are removed. Live services and data are untouched.
        if ($ContainerStarted) {
            $Existing = @(docker ps -a --filter "name=^/$ContainerName$" --format '{{.Names}}')
            if ($Existing -contains $ContainerName) {
                docker rm -f $ContainerName | Out-Null
            }
        }
        if (Test-Path -LiteralPath $ProviderEnv -PathType Leaf) {
            Remove-Item -LiteralPath $ProviderEnv -Force
        }
        if (Test-Path -LiteralPath $ProviderWorktree -PathType Container) {
            git worktree remove --force $ProviderWorktree | Out-Null
        }
    }

    # Confirm the read-only acceptance did not disturb the serving epoch. A PASS
    # marker is forbidden until this final checkpoint has also succeeded.
    powershell.exe -ExecutionPolicy Bypass -File .\scripts\check-cn-serving-state.ps1 -Compact
    if ($LASTEXITCODE -ne 0) { throw 'Post-acceptance CN serving-state checkpoint is not PASS.' }
    if ($null -eq $Receipt) { throw 'Discovery acceptance completed without a receipt.' }

    Write-Host ''
    Write-Host '=========================================='
    Write-Host 'PHASE4_CN_DISCOVERY_TARGET_HOST_PASS'
    Write-Host '=========================================='
    $Receipt | ConvertTo-Json -Depth 10
}
finally {
    Pop-Location
}
