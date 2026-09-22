from pathlib import Path


SCRIPT = Path('scripts/run-ipos-sg-capacity-pilot.ps1')


def _text() -> str:
    return SCRIPT.read_text(encoding='utf-8')


def test_sg_capacity_pilot_is_exact_main_and_non_production() -> None:
    text = _text()
    assert 'Assert-ExactMain' in text
    assert 'git rev-parse HEAD' in text
    assert 'git rev-parse origin/main' in text
    assert 'git status --porcelain=v1' in text
    assert 'markorbit-sg-capacity-pilot-' in text
    assert 'markorbit_sg_capacity_pilot_' in text
    assert "production_mutation_authorized = $false" in text
    assert "production_mutation_performed = $false" in text
    assert "production_clickhouse_mutated = $false" in text


def test_sg_capacity_pilot_freezes_exact_source_contract() -> None:
    text = _text()
    assert '$ExpectedHeaders = @(' in text
    assert "'Application Number'" in text
    assert "'Agent Correspondence Details'" in text
    assert '$ExpectedHeaders.Count -ne 39' in text
    assert 'ExpectedContentHash' in text
    assert 'ExpectedSchemaHash' in text
    assert 'Manifest content hash drifted.' in text
    assert 'Manifest schema hash drifted.' in text
    assert 'Snapshot filename does not match accepted content hash.' in text
    assert 'Snapshot header drift' in text


def test_sg_capacity_pilot_runs_sample_then_full_snapshot() -> None:
    text = _text()
    assert '[int]$SampleRows = 10000' in text
    assert 'LIMIT $SampleRows' in text
    assert 'native_sample' in text
    assert 'native_full' in text
    assert 'Start-AsyncSql' in text
    assert 'Wait-AsyncSql' in text
    assert 'Full pilot row count mismatch' in text
    assert 'CSVWithNames' in text


def test_sg_capacity_pilot_measures_storage_footprint() -> None:
    text = _text()
    for marker in (
        'bytes_on_disk',
        'data_compressed_bytes',
        'data_uncompressed_bytes',
        'marks_bytes',
        'primary_key_bytes_in_memory',
        'secondary_indices_compressed_bytes',
        'peak_data_dir_bytes',
        'peak_data_dir_growth_bytes',
    ):
        assert marker in text
    assert 'raw_to_target_bytes_on_disk_ratio' in text
    assert 'compressed_to_uncompressed_ratio' in text


def test_sg_capacity_pilot_emits_scenarios_and_cleanup_contract() -> None:
    text = _text()
    assert 'current_1x_min_virtual_bytes' in text
    assert 'growth_2x_min_virtual_bytes' in text
    assert 'growth_4x_min_virtual_bytes' in text
    assert 'internal_free_target = 0.30' in text
    assert 'REVIEW_2X_AND_4X_SCENARIOS_BEFORE_PRODUCTION_PROVISIONING' in text
    assert "next_gate = 'REVIEW_MEASURED_HOT_GLOBAL_CAPACITY_SCENARIOS'" in text
    assert '[switch]$KeepPilotRuntime' in text
    assert 'cleanup_verified' in text
    assert 'Disposable pilot runtime cleanup verification failed.' in text


def test_sg_capacity_pilot_has_powershell_contract_mode() -> None:
    text = _text()
    assert '[switch]$ContractOnly' in text
    assert 'IPOS_SG_CAPACITY_PILOT_CONTRACT_PASS' in text

def test_sg_capacity_pilot_accepts_api_and_display_header_aliases() -> None:
    text = _text()
    assert "$ExpectedApiHeaders = @(" in text
    assert "'applicationNumber'" in text
    assert "'agentCorrespondenceDetails_json'" in text
    assert "$observed[$i] -ne $display -and $observed[$i] -ne $api" in text
    assert "headers = $observed" in text
    assert "$schema = Get-SchemaText $sourceHeaders" in text
    assert "Quote-ChIdentifier $sourceHeaders[0]" in text


def test_sg_capacity_pilot_keeps_readonly_input_outside_clickhouse_data_dir() -> None:
    text = _text()
    assert "$pilotInputPath = '/pilot-input'" in text
    assert '<user_files_path>$pilotInputPath/</user_files_path>' in text
    assert 'CLICKHOUSE_DO_NOT_CHOWN=1' in text
    assert 'dst=$pilotInputPath,readonly' in text
    assert 'dst=/etc/clickhouse-server/config.d/pilot-user-files.xml,readonly' in text
    assert '/var/lib/clickhouse/user_files/input' not in text
    assert '$inputRef = $fileName' in text
    assert 'clickhouse_do_not_chown = $true' in text
