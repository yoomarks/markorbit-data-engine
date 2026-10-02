# Global Hot owner credential provisioning

`scripts/provision-global-hot-owner-credentials.ps1` is the only supported creator for new Global
Hot owner API credential artifacts. Operators must invoke that script from a reviewed Data Engine
checkout. Generated or ad-hoc owner runtime packages must not implement their own `Export-Clixml`
credential creation path.

The previously used external creator at
`D:\yoomarks\governed-plans\global-hot-owner-r1\provision-owner-secrets.ps1` is superseded for all
new credential creation. It is not source-controlled and exported DPAPI credential files without
hardening or verifying their inherited ACLs. This change does not modify that production runtime or
its existing credentials.

## Supported operator contract

Run the repository provisioner as the exact Windows identity that will run the owner service. Pin
that identity by SID and use a new credential directory whose parent has already been approved for
private secret storage:

```powershell
$serviceSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\provision-global-hot-owner-credentials.ps1 `
  -CredentialDirectory <new-approved-private-credential-directory> `
  -ExpectedServiceIdentitySid $serviceSid
```

The command intentionally refuses to run when:

- the current identity does not match the expected service SID;
- the target already exists;
- the target parent lets an untrusted identity delete child artifacts;
- directory or file ACL hardening cannot be applied and verified.

Success means the new directory and both DPAPI files have protected ACLs owned by the service SID,
with full control granted only to that SID and `SYSTEM`. The success object contains paths, the
service SID, and an ACL verification marker; it never contains credential values.

## Current exposure and remediation boundary

As audited on 2026-10-02, both existing `global-hot-owner-r1` credential files inherit
`Authenticated Users: Modify` and `BUILTIN\Users: ReadAndExecute`. Their content is Windows
user-scoped DPAPI ciphertext, so the ACL evidence does not by itself prove plaintext disclosure to
another Windows identity. It does prove that unauthorized identities can read the ciphertext and
can replace or corrupt the credential artifacts, creating direct integrity and availability risk.

The current `D:\yoomarks\governed-plans` parent also grants untrusted delete-child rights. The
canonical provisioner therefore fails before creating any credential when a new target below that
parent is attempted. Do not weaken this guard.

Remediation of `global-hot-owner-r1` is a separate production change requiring explicit authority:

1. approve a private credential root and the exact owner service SID;
2. provision a new versioned credential directory with the canonical repository script;
3. rotate the corresponding provider and consumer API keys without printing them;
4. update the owner service to load the new files and validate loopback-only read/write behavior;
5. retain the old artifacts only for the approved rollback window, then handle them under a
   separately reviewed deletion or quarantine decision.

Do not re-ACL, overwrite, rotate, or delete the current files as part of a read-only audit.
