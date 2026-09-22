from __future__ import annotations

from pathlib import Path


SCRIPT = Path("scripts/recover-accepted-target-runtime.ps1")


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8").lower()


def test_recovery_freezes_exact_accepted_topology() -> None:
    value = text()
    assert "markorbit-clickhouse" in value
    assert "24.8.14.39" in value
    assert "e:\\markorbitdata\\production\\clickhouse\\hot_us.vhdx" in value
    assert "e:\\markorbitdata\\production\\clickhouse\\warm_cn.vhdx" in value
    assert "markorbit_prod_hot_us" in value
    assert "markorbit_prod_warm_cn" in value
    assert "521a7b20-4380-4d6a-8018-2bab78fc2c4b" in value
    assert "2ee74d16-f0bd-461b-ab6a-279603e6c570" in value
    assert "274877906944" in value
    assert "842887331840" in value
    assert "c7240b6c05a96dff2dc4c9e5a801cd524065bd101b5d006f2e8610b63ca56a59" in value
def test_review_binds_exact_main_and_emits_exact_plan_authority() -> None:
    value = text()
    assert "git rev-parse head" in value
    assert "git rev-parse origin/main" in value
    assert "git status --porcelain=v1" in value
    assert "plan_sha256=$sha" in value
    assert "go #769 target-runtime $sha recover" in value
    assert "mutation_performed=false" in value
    assert "offline_unmounted" in value
    assert "partial_review_required" in value


def test_apply_requires_plan_hash_token_and_administrator() -> None:
    value = text()
    assert "apply requires -planpath" in value
    assert 'go #769 target-runtime $plansha recover' in value
    assert "authority token does not match frozen recovery plan" in value
    assert "apply requires administrator powershell" in value
    assert "physical length drifted" in value
    assert "mtime drifted" in value


def test_apply_has_only_exact_named_mount_surfaces_and_no_rollback() -> None:
    value = text()
    assert value.count("@('--mount','--vhd',$script:") == 2
    assert "@('--mount','--vhd',$script:hotvhdx,'--name',$script:hotname)" in value
    assert "@('--mount','--vhd',$script:warmvhdx,'--name',$script:warmname)" in value
    for forbidden in (
        "--unmount",
        "--shutdown",
        "--unregister",
        "new-vhd",
        "resize-vhd",
        "format-volume",
        "mkfs.",
        "docker restart",
        "docker stop",
        "docker rm",
        "insert into",
        "create table",
        "alter table",
        "optimize table",
        "move partition",
        "delete where",
        "truncate table",
    ):
        assert forbidden not in value, forbidden
    assert "automatic_unmount_or_rollback_performed=false" in value


def test_recovery_orders_keeper_mounts_then_server_and_validates_storage() -> None:
    value = text()
    apply = value.index("$keeper = start-keeper")
    hot = value.index("invoke-native 'wsl.exe' @('--mount','--vhd',$script:hotvhdx", apply)
    warm = value.index("invoke-native 'wsl.exe' @('--mount','--vhd',$script:warmvhdx", hot)
    server = value.index("start-targetserver", warm)
    validate = value.index("$validation = validate-target", server)
    assert apply < hot < warm < server < validate
    assert "findmnt -n -o source,fstype" in value
    assert "blkid -s uuid -o value" in value
    assert "blockdev --getsize64" in value
    assert "hot_us_only" in value
    assert "warm_cn_only" in value
    assert "t.storage_policy='hot_us_only' and p.disk_name!='hot_us'" in value
    assert "t.storage_policy='warm_cn_only' and p.disk_name!='warm_cn'" in value
    assert "t.storage_policy='default' and p.disk_name!='default'" in value
    assert "placement_mismatches=0" in value


def test_apply_is_journaled_and_fail_closed_after_partial_recovery() -> None:
    value = text()
    assert "accepted_target_runtime_recovery_journal.json" in value
    assert "keeper_started=$false" in value
    assert "hot_us_mounted=$false" in value
    assert "warm_cn_mounted=$false" in value
    assert "server_started=$false" in value
    assert "validated=$false" in value
    assert "accepted_target_runtime_recovered" in value
    assert "existing_vhdx_mounts=$true" in value
    assert "ddl=$false" in value
    assert "insert=$false" in value
    assert "replay=$false" in value


def test_review_never_starts_target_and_evidence_stays_outside_worktree() -> None:
    value = text()
    main = value.rindex("assert-exactmain")
    apply_guard = value.index("require (-not [string]::isnullorwhitespace($planpath))", main)
    review = value[main:apply_guard]
    assert "get-runningdistros" in review
    assert "test-namedmountvisible" in review
    assert "start-keeper" not in review
    assert "invoke-runtime 'printf runtime_ok'" not in review
    assert "sha256sum '$($script:configpath)'" not in review
    assert "d:\\yoomarks\\governed-plans\\769" in value
    apply = value[apply_guard:]
    assert "start-keeper" in apply
    assert "sha256sum '$($script:configpath)'" in apply


def test_review_fact_variables_cannot_shadow_mount_path_constants() -> None:
    value = text()
    assert "$hotmount = $null" not in value
    assert "$warmmount = $null" not in value
    assert "$hotmountfact = $null" in value
    assert "$warmmountfact = $null" in value
    assert "mount_path=$script:hotmount" in value
    assert "mount_path=$script:warmmount" in value
