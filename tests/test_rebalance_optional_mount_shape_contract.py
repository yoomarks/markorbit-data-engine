from pathlib import Path


SCRIPT = Path("scripts/profile-production-storage-rebalance-candidates.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_rebalance_reference_inventory_tolerates_optional_shape_fields() -> None:
    text = _text()
    for marker in (
        "Get-OptionalPropertyValue",
        "Get-OptionalArrayProperty",
        "Get-OptionalArrayProperty $container 'Mounts'",
        "Get-OptionalPropertyValue $mount 'Name'",
        "Get-OptionalArrayProperty $serviceProperty.Value 'volumes'",
        "Docker inspect omitted Id",
        "Docker inspect omitted State",
        "Compose bind for service",
        "'inspect','--format','{{json .}}'",
    ):
        assert marker in text

    assert "$container.Mounts" not in text
    assert "$mount.Name" not in text
    assert "$serviceProperty.Value.volumes" not in text


def test_rebalance_reference_inventory_uses_bidirectional_overlap() -> None:
    text = _text()
    assert "function Test-PathsOverlap" in text
    assert "Test-PathContains $LeftPath $RightPath" in text
    assert "Test-PathContains $RightPath $LeftPath" in text
    assert "Test-PathsOverlap $CandidatePath $_.normalized_source" in text


def test_rebalance_reference_shape_fix_preserves_fail_closed_and_read_only_boundaries() -> None:
    text = _text()
    for marker in (
        "Unable to enumerate Docker containers.",
        "Unable to inspect container",
        "accepted_production_mount_ready",
        "legacy_e_hot_delete_authorized=$false",
        "legacy_raw_delete_authorized=$false",
        "accepted_volume_delete_authorized=$false",
        "vhdx_create_authorized=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
    ):
        assert marker in text
