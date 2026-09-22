param(
    [string]$StateDir,
    [switch]$RecoverStaleLock,
    [string]$ExpectedMainSha = '',
    [switch]$ContractOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

function Assert-ExactMain {
    param([Parameter(Mandatory = $true)][string]$ExpectedSha)

    $expected = $ExpectedSha.Trim().ToLowerInvariant()
    if ($expected -notmatch '^[0-9a-f]{40}$') {
        throw "ExpectedMainSha must be an exact 40-character Git SHA."
    }
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0) { throw "Unable to resolve Git HEAD." }
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0) { throw "Unable to resolve origin/main." }
    if ($head -ne $expected -or $originMain -ne $expected) {
        throw "Exact main drift detected. expected=$expected head=$head origin_main=$originMain"
    }
    if (git status --porcelain=v1) {
        throw "Working tree must be clean for the controlled Singapore production refresh."
    }
    return $expected
}

function Resolve-EnvFileValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )
    $processValue = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ($processValue) { return $processValue.Trim() }

    $envPath = Join-Path $RepoRoot ".env"
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { return $null }
    foreach ($line in Get-Content -LiteralPath $envPath) {
        if ($line -match ("^\s*" + [regex]::Escape($Name) + "\s*=\s*(.+?)\s*$")) {
            $value = $Matches[1].Trim()
            if ($value.Length -ge 2) {
                $first = [int][char]$value[0]
                $last = [int][char]$value[$value.Length - 1]
                if (($first -eq 34 -and $last -eq 34) -or ($first -eq 39 -and $last -eq 39)) {
                    $value = $value.Substring(1, $value.Length - 2)
                }
            }
            return $value
        }
    }
    return $null
}

function Get-TextSha256([string]$Text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Get-CnServingSample {
    $query = "SELECT application_number FROM markorbit_facts.cn_case_current FINAL WHERE is_deleted=0 AND application_number!='' ORDER BY application_number LIMIT 1 FORMAT TabSeparatedRaw"
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(docker compose exec -T clickhouse clickhouse-client --query $query 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    if ($exitCode -ne 0) {
        throw "Unable to select deterministic CN serving sample: $($output -join [Environment]::NewLine)"
    }
    $values = @($output | ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ })
    if ($values.Count -ne 1) {
        throw "Expected exactly one deterministic CN serving sample; observed=$($values.Count)"
    }
    return $values[0]
}

function Invoke-RawHttpGet {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][int]$TimeoutSec
    )
    $request = [System.Net.HttpWebRequest]::Create($Uri)
    $request.Method = 'GET'
    $request.Timeout = $TimeoutSec * 1000
    $request.ReadWriteTimeout = $TimeoutSec * 1000
    $response = $null
    $reader = $null
    try {
        $response = [System.Net.HttpWebResponse]$request.GetResponse()
        if ([int]$response.StatusCode -ne 200) {
            throw "HTTP status is $([int]$response.StatusCode) for $Uri"
        }
        $reader = New-Object System.IO.StreamReader(
            $response.GetResponseStream(),
            [System.Text.Encoding]::UTF8,
            $true
        )
        return $reader.ReadToEnd()
    }
    finally {
        if ($reader) { $reader.Dispose() }
        if ($response) { $response.Dispose() }
    }
}

function Invoke-CnServingProbe {
    param(
        [Parameter(Mandatory = $true)][int]$ApiPort,
        [Parameter(Mandatory = $true)][string]$ApplicationNumber
    )
    $healthUri = "http://127.0.0.1:$ApiPort/api/health"
    $healthContent = Invoke-RawHttpGet -Uri $healthUri -TimeoutSec 20
    $health = $healthContent | ConvertFrom-Json
    foreach ($name in @('api','postgres','clickhouse')) {
        if ([string]$health.$name -ne 'ok') {
            throw "CN serving dependency '$name' is not ok: $($health.$name)"
        }
    }

    $encoded = [uri]::EscapeDataString($ApplicationNumber)
    $caseUri = "http://127.0.0.1:$ApiPort/api/cn/cases/$encoded"
    $caseContent = Invoke-RawHttpGet -Uri $caseUri -TimeoutSec 30
    $payload = $caseContent | ConvertFrom-Json
    if ([string]$payload.case.application_number -ne $ApplicationNumber) {
        throw "CN serving case identity drifted."
    }
    return [ordered]@{
        application_number = $ApplicationNumber
        response_sha256 = Get-TextSha256 ([string]$caseContent)
        response_bytes = [System.Text.Encoding]::UTF8.GetByteCount([string]$caseContent)
        scope_count = @($payload.scopes).Count
        goods_item_count = @($payload.goods_items).Count
        party_count = @($payload.parties).Count
        event_count = @($payload.events).Count
        relation_count = @($payload.relations).Count
        api_health = 'ok'
        postgres_health = 'ok'
        clickhouse_health = 'ok'
    }
}

function Test-CnServingStable {
    param(
        [Parameter(Mandatory = $true)][object]$Before,
        [Parameter(Mandatory = $true)][object]$After
    )
    return (
        [string]$After.application_number -eq [string]$Before.application_number -and
        [string]$After.response_sha256 -eq [string]$Before.response_sha256 -and
        [string]$After.api_health -eq 'ok' -and
        [string]$After.postgres_health -eq 'ok' -and
        [string]$After.clickhouse_health -eq 'ok'
    )
}

function Write-CompactJson {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $temp = "$Path.part"
    $json = $Value | ConvertTo-Json -Depth 12
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($temp, $json, $utf8)
    Move-Item -LiteralPath $temp -Destination $Path -Force
}

function Resolve-IposSgStateDir {
    param(
        [string]$RequestedStateDir,
        [string]$RepoRoot
    )

    if ($RequestedStateDir) {
        if ([System.IO.Path]::IsPathRooted($RequestedStateDir)) {
            return [System.IO.Path]::GetFullPath($RequestedStateDir)
        }
        return [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $RequestedStateDir))
    }

    $rawDataPath = $env:RAW_DATA_PATH
    if (-not $rawDataPath) {
        $envPath = Join-Path $RepoRoot ".env"
        if (Test-Path -LiteralPath $envPath) {
            foreach ($line in Get-Content -LiteralPath $envPath) {
                if ($line -match '^\s*RAW_DATA_PATH\s*=\s*(.+?)\s*$') {
                    $rawDataPath = $Matches[1].Trim()
                    if ($rawDataPath.Length -ge 2) {
                        $first = [int][char]$rawDataPath[0]
                        $last = [int][char]$rawDataPath[$rawDataPath.Length - 1]
                        if (($first -eq 34 -and $last -eq 34) -or ($first -eq 39 -and $last -eq 39)) {
                            $rawDataPath = $rawDataPath.Substring(1, $rawDataPath.Length - 2)
                        }
                    }
                    break
                }
            }
        }
    }

    if (-not $rawDataPath) {
        throw "StateDir was not supplied and RAW_DATA_PATH is not configured; refusing repo-local fallback."
    }
    if ([System.IO.Path]::IsPathRooted($rawDataPath)) {
        $rawRoot = [System.IO.Path]::GetFullPath($rawDataPath)
    }
    else {
        $rawRoot = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $rawDataPath))
    }
    return [System.IO.Path]::GetFullPath((Join-Path $rawRoot "ipos_sg"))
}
if ($ContractOnly) {
    $known = Get-TextSha256 'abc'
    if ($known -ne 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad') {
        throw 'CN serving SHA256 contract drifted.'
    }
    $before = [pscustomobject]@{
        application_number='100001'; response_sha256=('a' * 64);
        api_health='ok'; postgres_health='ok'; clickhouse_health='ok'
    }
    $same = [pscustomobject]@{
        application_number='100001'; response_sha256=('a' * 64);
        api_health='ok'; postgres_health='ok'; clickhouse_health='ok'
    }
    $drift = [pscustomobject]@{
        application_number='100001'; response_sha256=('b' * 64);
        api_health='ok'; postgres_health='ok'; clickhouse_health='ok'
    }
    if (-not (Test-CnServingStable -Before $before -After $same)) {
        throw 'CN serving stable-state contract drifted.'
    }
    if (Test-CnServingStable -Before $before -After $drift) {
        throw 'CN serving regression contract failed to detect response drift.'
    }
    Write-Host 'IPOS_SG_PRODUCTION_REFRESH_CONTRACT_PASS'
    exit 0
}

Push-Location $repoRoot
try {
    $executionMainSha = Assert-ExactMain -ExpectedSha $ExpectedMainSha

    if (-not $env:DATA_GOV_SG_API_KEY) {
        throw "DATA_GOV_SG_API_KEY must be set in the current PowerShell session."
    }

    $runningWorker = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker Compose worker state."
    }
    if ($runningWorker) {
        throw "A worker container is already running. Finish or stop it before the Singapore IPOS full-corpus run."
    }

    $explicitStateDir = [bool]$StateDir
    $hostState = Resolve-IposSgStateDir -RequestedStateDir $StateDir -RepoRoot $repoRoot
    if (-not (Test-Path -LiteralPath $hostState -PathType Container)) {
        if (-not $explicitStateDir) {
            throw "Resolved production Singapore IPOS state directory does not exist: $hostState. Refusing implicit bootstrap. Use -StateDir explicitly only for an intentional bootstrap."
        }
        New-Item -ItemType Directory -Force -Path $hostState | Out-Null
    }
    $hostState = (Resolve-Path -LiteralPath $hostState).Path

    $apiPortRaw = Resolve-EnvFileValue -Name 'API_PORT' -RepoRoot $repoRoot
    $apiPort = if ($apiPortRaw) { [int]$apiPortRaw } else { 8080 }
    if ($apiPort -lt 1 -or $apiPort -gt 65535) {
        throw "API_PORT is outside the valid TCP port range: $apiPort"
    }
    $cnSample = Get-CnServingSample
    Write-Host "Running pre-refresh CN serving regression probe..."
    $cnPre = Invoke-CnServingProbe -ApiPort $apiPort -ApplicationNumber $cnSample
    Write-Host "CN pre-refresh serving probe: PASS"

    $appMount = "${repoRoot}\app:/app/app:ro"
    $stateMount = "${hostState}:/app/ipos_sg_state"
    $operatorArgs = @(
        "-m",
        "app.snapshot_delta.ipos_sg_operator",
        "--state-dir",
        "/app/ipos_sg_state"
    )
    if ($RecoverStaleLock) {
        $operatorArgs += "--recover-stale-lock"
    }

    Write-Host "Running leased authenticated Singapore IPOS operator cycle..."
    Write-Host "The same one-shot worker remains alive for preflight, source authentication, full corpus, and postflight."
    Write-Host "State: $hostState"
    docker compose run --rm --no-deps -T `
        --env DATA_GOV_SG_API_KEY `
        --volume $appMount `
        --volume $stateMount `
        worker python @operatorArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Singapore IPOS operator cycle failed. Accepted state, failure evidence, and any recoverable lifecycle artifacts remain in $hostState."
    }

    $operatorReportPath = Join-Path $hostState 'acceptance\operator_latest.json'
    if (-not (Test-Path -LiteralPath $operatorReportPath -PathType Leaf)) {
        throw "Singapore operator PASS did not produce operator_latest.json."
    }
    $operatorReport = Get-Content -LiteralPath $operatorReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$operatorReport.status -ne 'PASS') {
        throw "Singapore operator report status is not PASS."
    }
    $operatorReportSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $operatorReportPath).Hash.ToLowerInvariant()

    Write-Host "Running post-refresh CN serving regression probe..."
    $cnPost = $null
    $cnPostError = $null
    try {
        $cnPost = Invoke-CnServingProbe -ApiPort $apiPort -ApplicationNumber $cnSample
    }
    catch {
        $cnPostError = $_.Exception.Message
    }
    $cnServingStable = (
        $null -ne $cnPost -and
        (Test-CnServingStable -Before $cnPre -After $cnPost)
    )

    $acceptancePath = Join-Path $hostState 'acceptance\production_refresh_latest.json'
    $refreshReceipt = [ordered]@{
        version = 'IPOS_SG_PRODUCTION_REFRESH_ACCEPTANCE_V1'
        status = if ($cnServingStable) { 'PASS' } else { 'FAILED' }
        completed_at = [DateTimeOffset]::UtcNow.ToString('o')
        execution_main_sha = $executionMainSha
        state_directory = $hostState
        api_port = $apiPort
        operator_report_path = $operatorReportPath
        operator_report_sha256 = $operatorReportSha
        lifecycle_result = [string]$operatorReport.full_corpus.status
        row_count = [int64]$operatorReport.full_corpus.row_count
        live_total_rows = [int64]$operatorReport.full_corpus.live_total_rows
        live_row_count_delta = [int64]$operatorReport.full_corpus.live_row_count_delta
        allowed_live_row_drift = [int64]$operatorReport.full_corpus.allowed_live_row_drift
        content_hash = [string]$operatorReport.full_corpus.content_hash
        schema_hash = [string]$operatorReport.full_corpus.schema_hash
        retained_full_snapshot_count = [int]$operatorReport.full_corpus.retained_full_snapshot_count
        storage_preflight = $operatorReport.storage_preflight
        elapsed_seconds = [double]$operatorReport.full_corpus.elapsed_seconds
        cn_serving_pre = $cnPre
        cn_serving_post = $cnPost
        cn_serving_post_error = $cnPostError
        cn_serving_stable = [bool]$cnServingStable
        credential_material_persisted = $false
        recurring_schedule_enabled = $false
    }
    Write-CompactJson -Value $refreshReceipt -Path $acceptancePath

    if (-not $cnServingStable) {
        throw "Singapore refresh completed, but CN serving regression gate failed. Review $acceptancePath before any further SG run."
    }

    Write-Host "Singapore IPOS authenticated operator acceptance: PASS"
    Write-Host "CN serving regression gate: PASS"
    Write-Host "State: $hostState"
    Write-Host "Corpus report: $(Join-Path $hostState 'acceptance\latest.json')"
    Write-Host "Operator report: $operatorReportPath"
    Write-Host "Production refresh receipt: $acceptancePath"
    Write-Host "Production refresh receipt SHA256: $((Get-FileHash -Algorithm SHA256 -LiteralPath $acceptancePath).Hash.ToLowerInvariant())"
}
finally {
    Pop-Location
}
