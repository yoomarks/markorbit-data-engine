from pathlib import Path


SCRIPT = Path("scripts/provision-global-hot-owner-credentials.ps1")
RUNBOOK = Path("docs/GLOBAL_HOT_OWNER_CREDENTIALS.md")


def test_owner_credential_provisioning_is_fail_closed() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "ExpectedServiceIdentitySid" in source
    assert "SetAccessRuleProtection($true, $false)" in source
    assert "SetOwner($ServiceSid)" in source
    assert "Assert-ParentCannotDeleteChildren" in source
    assert "Test-PrivateAcl" in source
    assert "Published credential ACL verification failed." in source
    assert "CredentialDirectory already exists; refusing to overwrite" in source
    assert "credential_values_persisted_in_output = $false" in source


def test_owner_credential_provisioning_uses_staged_dpapi_files() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert source.count("Export-Clixml -LiteralPath") == 2
    assert "Move-Item -LiteralPath $staging -Destination $target" in source
    assert "Remove-Item -LiteralPath $target -Recurse -Force" in source
    assert "Remove-Item -LiteralPath $staging -Recurse -Force" in source


def test_owner_credential_runbook_supersedes_the_unsafe_external_creator() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert (
        "scripts/provision-global-hot-owner-credentials.ps1` is the only supported creator"
        in runbook
    )
    assert (
        "D:\\yoomarks\\governed-plans\\global-hot-owner-r1\\provision-owner-secrets.ps1"
        in runbook
    )
    assert "is superseded for all\nnew credential creation" in runbook
    assert "Do not re-ACL, overwrite, rotate, or delete the current files" in runbook
