from pathlib import Path


def test_manifest_builder_script_is_dry_run_by_default_and_worker_guarded() -> None:
    source = Path("scripts/build-uspto-odp-corpus-manifest.ps1").read_text(encoding="utf-8")
    assert '[ValidateSet("assignment", "ttab")]' in source
    assert "[switch]$Apply" in source
    assert "SourceSpecPath" in source
    assert "MetadataPath" in source
    assert "docker compose ps --status running -q worker" in source
    assert "Persistent worker is running" in source
    assert "python -m app.uspto_odp_manifest_builder --stdin" in source
    assert "Dry run only" in source
    assert "-ManifestOutputPath is required when -Apply is used" in source
    assert "UTF8Encoding($false)" in source
    assert "GetFullPath($ManifestOutputPath)" in source


def test_manifest_builder_script_only_writes_manifest_after_apply_gate() -> None:
    source = Path("scripts/build-uspto-odp-corpus-manifest.ps1").read_text(encoding="utf-8")
    dry_run_position = source.index("if (-not $Apply)")
    output_required_position = source.index("if (-not $ManifestOutputPath)")
    write_position = source.index("[System.IO.File]::WriteAllText")
    assert dry_run_position < output_required_position < write_position
    assert "No replay or ingestion was started" in source
