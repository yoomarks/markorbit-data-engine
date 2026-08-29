param(
    [ValidateNotNullOrEmpty()]
    [string]$PackageName = '2023_5.zip',

    [ValidateRange(4, 10000)]
    [int]$MaxSourceCandidates = 1024,

    [ValidateRange(1, 256)]
    [int]$MaxValidationWindows = 64
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Helper = Join-Path $RepoRoot 'scripts\find_cn_preliminary_publication_discovery_acceptance_range.py'

Push-Location $RepoRoot
try {
    if (-not (Test-Path -LiteralPath $Helper -PathType Leaf)) {
        throw "Source-derived Discovery range helper is missing: $Helper"
    }

    docker compose ps clickhouse
    if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect ClickHouse.' }

    # The probe deliberately does NOT discover bounds from cn_case_current. It
    # mounts the current checkout read-only into one disposable worker container,
    # streams the already-authorized raw source package through the production CN
    # parser, and asks ClickHouse only about tiny explicit source-derived ranges.
    # --no-deps guarantees this command never starts/restarts the live databases
    # or worker service. The service's existing /data/raw mount remains read-only
    # from the helper's perspective; the helper contains no mutation path.
    # PYTHONPATH is explicit because executing /workspace/scripts/<helper>.py
    # otherwise puts only /workspace/scripts on sys.path, hiding /workspace/app.
    $RepoMount = "${RepoRoot}:/workspace:ro"
    docker compose run --rm --no-deps -T `
        -v $RepoMount `
        -w /workspace `
        -e PYTHONPATH=/workspace `
        worker `
        python scripts/find_cn_preliminary_publication_discovery_acceptance_range.py `
        --package-name $PackageName `
        --max-source-candidates $MaxSourceCandidates `
        --max-validation-windows $MaxValidationWindows

    if ($LASTEXITCODE -ne 0) {
        throw 'Source-derived CN preliminary-publication range probe failed closed.'
    }
}
finally {
    Pop-Location
}
