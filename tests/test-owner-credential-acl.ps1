$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $repoRoot 'scripts\provision-global-hot-owner-credentials.ps1'
$serviceSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$systemSid = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$administratorsSid = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    'markorbit-owner-credential-acl-{0}' -f [Guid]::NewGuid().ToString('N')
)

function Set-TestRootAcl {
    param([string]$LiteralPath)

    $acl = [System.Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner($serviceSid)
    $inheritance = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    $propagation = [System.Security.AccessControl.PropagationFlags]::None
    foreach ($sid in @($serviceSid, $systemSid, $administratorsSid)) {
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            $propagation,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        [void]$acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $LiteralPath -AclObject $acl
}

function Assert-PrivateAcl {
    param([string]$LiteralPath)

    $expected = @($serviceSid.Value, $systemSid.Value)
    $acl = Get-Acl -LiteralPath $LiteralPath
    if (-not $acl.AreAccessRulesProtected) {
        throw "$LiteralPath still inherits access rules."
    }
    $owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier])
    if ($owner.Value -ne $serviceSid.Value) {
        throw "$LiteralPath has the wrong owner."
    }
    $rules = @($acl.GetAccessRules(
        $true,
        $true,
        [System.Security.Principal.SecurityIdentifier]
    ))
    if ($rules.Count -ne 2) {
        throw "$LiteralPath has unexpected access rules."
    }
    foreach ($rule in $rules) {
        if (
            $rule.IsInherited -or
            $rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or
            $expected -notcontains $rule.IdentityReference.Value -or
            (($rule.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::FullControl) -ne
                [System.Security.AccessControl.FileSystemRights]::FullControl)
        ) {
            throw "$LiteralPath has a non-private access rule."
        }
    }
}

try {
    [void](New-Item -ItemType Directory -Path $testRoot)
    Set-TestRootAcl -LiteralPath $testRoot

    $credentialDirectory = Join-Path $testRoot 'credentials'
    $result = & $scriptPath `
        -CredentialDirectory $credentialDirectory `
        -ExpectedServiceIdentitySid $serviceSid.Value
    if (-not $result.acl_verified -or $result.credential_values_persisted_in_output) {
        throw 'Provisioning success receipt did not preserve the credential safety contract.'
    }

    $readPath = Join-Path $credentialDirectory 'owner-read.credential.xml'
    $writePath = Join-Path $credentialDirectory 'owner-write.credential.xml'
    foreach ($path in @($credentialDirectory, $readPath, $writePath)) {
        Assert-PrivateAcl -LiteralPath $path
    }

    $readCredential = Import-Clixml -LiteralPath $readPath
    $writeCredential = Import-Clixml -LiteralPath $writePath
    $readValue = $readCredential.GetNetworkCredential().Password
    $writeValue = $writeCredential.GetNetworkCredential().Password
    if ($readValue.Length -lt 32 -or $writeValue.Length -lt 32 -or $readValue -eq $writeValue) {
        throw 'Provisioned credentials do not satisfy the runtime key contract.'
    }
    $readValue = $null
    $writeValue = $null

    $wrongIdentityDirectory = Join-Path $testRoot 'wrong-identity'
    $wrongIdentityRejected = $false
    try {
        & $scriptPath `
            -CredentialDirectory $wrongIdentityDirectory `
            -ExpectedServiceIdentitySid $systemSid.Value | Out-Null
    }
    catch {
        $wrongIdentityRejected = $true
    }
    if (-not $wrongIdentityRejected -or (Test-Path -LiteralPath $wrongIdentityDirectory)) {
        throw 'Wrong service identity was not rejected before credential creation.'
    }

    $unsafeParent = Join-Path $testRoot 'unsafe-parent'
    [void](New-Item -ItemType Directory -Path $unsafeParent)
    $unsafeAcl = Get-Acl -LiteralPath $unsafeParent
    $authenticatedUsers = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-11')
    $unsafeRule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $authenticatedUsers,
        [System.Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
    [void]$unsafeAcl.AddAccessRule($unsafeRule)
    Set-Acl -LiteralPath $unsafeParent -AclObject $unsafeAcl
    $unsafeParentDirectory = Join-Path $unsafeParent 'credentials'
    $unsafeParentRejected = $false
    try {
        & $scriptPath `
            -CredentialDirectory $unsafeParentDirectory `
            -ExpectedServiceIdentitySid $serviceSid.Value | Out-Null
    }
    catch {
        $unsafeParentRejected = $true
    }
    if (-not $unsafeParentRejected -or (Test-Path -LiteralPath $unsafeParentDirectory)) {
        throw 'Unsafe parent deletion rights were not rejected before credential creation.'
    }

    $existingDirectory = Join-Path $testRoot 'existing'
    [void](New-Item -ItemType Directory -Path $existingDirectory)
    $existingRejected = $false
    try {
        & $scriptPath `
            -CredentialDirectory $existingDirectory `
            -ExpectedServiceIdentitySid $serviceSid.Value | Out-Null
    }
    catch {
        $existingRejected = $true
    }
    if (-not $existingRejected -or -not (Test-Path -LiteralPath $existingDirectory)) {
        throw 'Existing credential directory was not preserved and rejected.'
    }

    Write-Output 'OWNER_CREDENTIAL_ACL_WINDOWS_PASS'
}
finally {
    Clear-Variable readCredential, writeCredential -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $testRoot) {
        Remove-Item -LiteralPath $testRoot -Recurse -Force
    }
}
