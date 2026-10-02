<#
.SYNOPSIS
Creates DPAPI-protected Global Hot owner API credentials with verified private Windows ACLs.

.DESCRIPTION
The target directory must not exist and its parent must not let untrusted identities delete
children. The current Windows identity must match ExpectedServiceIdentitySid because Export-Clixml
binds the credential values to that identity. Credential values are never written to output.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$CredentialDirectory,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^S-1-')]
    [string]$ExpectedServiceIdentitySid
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$script:SystemSid = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$script:AdministratorsSid = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$script:FullControl = [System.Security.AccessControl.FileSystemRights]::FullControl

function Get-CurrentServiceSid {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    if ($null -eq $identity.User) {
        throw 'The current Windows identity has no security identifier.'
    }
    return $identity.User
}

function New-PrivateDirectoryAcl {
    param([System.Security.Principal.SecurityIdentifier]$ServiceSid)

    $acl = [System.Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner($ServiceSid)
    $inheritance = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    $propagation = [System.Security.AccessControl.PropagationFlags]::None
    foreach ($sid in @($ServiceSid, $script:SystemSid)) {
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            $script:FullControl,
            $inheritance,
            $propagation,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        [void]$acl.AddAccessRule($rule)
    }
    return $acl
}

function New-PrivateFileAcl {
    param([System.Security.Principal.SecurityIdentifier]$ServiceSid)

    $acl = [System.Security.AccessControl.FileSecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner($ServiceSid)
    foreach ($sid in @($ServiceSid, $script:SystemSid)) {
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            $script:FullControl,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        [void]$acl.AddAccessRule($rule)
    }
    return $acl
}

function Test-PrivateAcl {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][System.Security.Principal.SecurityIdentifier]$ServiceSid
    )

    $acl = Get-Acl -LiteralPath $LiteralPath
    if (-not $acl.AreAccessRulesProtected) {
        return $false
    }
    $ownerSid = $acl.GetOwner([System.Security.Principal.SecurityIdentifier])
    if ($ownerSid.Value -ne $ServiceSid.Value) {
        return $false
    }

    $expectedSids = @(
        $ServiceSid.Value,
        $script:SystemSid.Value
    )
    $rules = @($acl.GetAccessRules(
        $true,
        $true,
        [System.Security.Principal.SecurityIdentifier]
    ))
    if ($rules.Count -ne $expectedSids.Count) {
        return $false
    }
    foreach ($rule in $rules) {
        if (
            $rule.IsInherited -or
            $rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or
            $expectedSids -notcontains $rule.IdentityReference.Value -or
            (($rule.FileSystemRights -band $script:FullControl) -ne $script:FullControl)
        ) {
            return $false
        }
    }
    foreach ($sid in $expectedSids) {
        if (@($rules | Where-Object { $_.IdentityReference.Value -eq $sid }).Count -ne 1) {
            return $false
        }
    }
    return $true
}

function Assert-ParentCannotDeleteChildren {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][System.Security.Principal.SecurityIdentifier]$ServiceSid
    )

    $trustedSids = @(
        $ServiceSid.Value,
        $script:SystemSid.Value,
        $script:AdministratorsSid.Value
    )
    $acl = Get-Acl -LiteralPath $LiteralPath
    $rules = @($acl.GetAccessRules(
        $true,
        $true,
        [System.Security.Principal.SecurityIdentifier]
    ))
    $unsafeDeletionRights = (
        [System.Security.AccessControl.FileSystemRights]::Delete -bor
        [System.Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles
    )
    $unsafe = @($rules | Where-Object {
        $_.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
        $trustedSids -notcontains $_.IdentityReference.Value -and
        (($_.FileSystemRights -band $unsafeDeletionRights) -ne 0)
    })
    if ($unsafe.Count -gt 0) {
        throw 'Credential parent grants untrusted identities permission to delete child artifacts.'
    }
}

function New-ApiKey {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
        return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    }
    finally {
        [Array]::Clear($bytes, 0, $bytes.Length)
        $generator.Dispose()
    }
}

if ($PSVersionTable.PSVersion.Major -lt 5) {
    throw 'Windows PowerShell 5.1 or newer is required.'
}

$serviceSid = Get-CurrentServiceSid
if ($serviceSid.Value -ne $ExpectedServiceIdentitySid) {
    throw 'The current Windows identity does not match ExpectedServiceIdentitySid.'
}

$target = [System.IO.Path]::GetFullPath($CredentialDirectory)
if (Test-Path -LiteralPath $target) {
    throw 'CredentialDirectory already exists; refusing to overwrite or rotate credentials.'
}
$parent = Split-Path -Parent $target
if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    throw 'CredentialDirectory parent must already exist.'
}
Assert-ParentCannotDeleteChildren -LiteralPath $parent -ServiceSid $serviceSid

$leaf = Split-Path -Leaf $target
$staging = Join-Path $parent ('.{0}.{1}.tmp' -f $leaf, [Guid]::NewGuid().ToString('N'))
$published = $false
$readToken = $null
$writeToken = $null
try {
    [void](New-Item -ItemType Directory -Path $staging)
    Set-Acl -LiteralPath $staging -AclObject (New-PrivateDirectoryAcl -ServiceSid $serviceSid)
    if (-not (Test-PrivateAcl -LiteralPath $staging -ServiceSid $serviceSid)) {
        throw 'Credential staging directory ACL verification failed.'
    }

    $readToken = New-ApiKey
    $writeToken = New-ApiKey
    $readCredential = [System.Management.Automation.PSCredential]::new(
        'integration-api-key',
        (ConvertTo-SecureString $readToken -AsPlainText -Force)
    )
    $writeCredential = [System.Management.Automation.PSCredential]::new(
        'fact-admission-api-key',
        (ConvertTo-SecureString $writeToken -AsPlainText -Force)
    )

    $readPath = Join-Path $staging 'owner-read.credential.xml'
    $writePath = Join-Path $staging 'owner-write.credential.xml'
    $readCredential | Export-Clixml -LiteralPath $readPath
    $writeCredential | Export-Clixml -LiteralPath $writePath
    foreach ($path in @($readPath, $writePath)) {
        Set-Acl -LiteralPath $path -AclObject (New-PrivateFileAcl -ServiceSid $serviceSid)
        if (-not (Test-PrivateAcl -LiteralPath $path -ServiceSid $serviceSid)) {
            throw 'Credential file ACL verification failed.'
        }
    }

    Move-Item -LiteralPath $staging -Destination $target
    $published = $true
    $finalReadPath = Join-Path $target 'owner-read.credential.xml'
    $finalWritePath = Join-Path $target 'owner-write.credential.xml'
    foreach ($path in @($target, $finalReadPath, $finalWritePath)) {
        if (-not (Test-PrivateAcl -LiteralPath $path -ServiceSid $serviceSid)) {
            throw 'Published credential ACL verification failed.'
        }
    }

    [pscustomobject]@{
        credential_directory = $target
        service_identity_sid = $serviceSid.Value
        acl_verified = $true
        credential_values_persisted_in_output = $false
    }
}
catch {
    if ($published -and (Test-Path -LiteralPath $target)) {
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
    }
    elseif (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
    throw
}
finally {
    $readToken = $null
    $writeToken = $null
    Clear-Variable readCredential, writeCredential -ErrorAction SilentlyContinue
}
