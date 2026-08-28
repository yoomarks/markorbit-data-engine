param(
    [string]$OldHotPath = "E:\MarkOrbitData\hot\clickhouse-cs",
    [string]$NewHotPath = "E:\MarkOrbitData\hot\clickhouse",
    [string]$ColdPath = "F:\MarkOrbitData\cold\clickhouse",
    [string]$LogPath = "E:\MarkOrbitData\hot\clickhouse-logs"
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-FsutilCaseSensitiveEnabled([string]$Path) {
    $lines = @(& fsutil.exe file queryCaseSensitiveInfo $Path 2>&1)
    $exitCode = $LASTEXITCODE
    $text = ($lines -join "`n").Trim()
    if ($exitCode -ne 0) {
        throw "fsutil could not query case sensitivity for $Path (exit=$exitCode): $text"
    }
    Write-Host $text
    if ($text -match '(?i)\bis enabled\b' -or $text -match '已启用' -or $text -match '已啟用') {
        return
    }
    if ($text -match '(?i)\bis disabled\b' -or $text -match '已禁用' -or $text -match '已停用' -or $text -match '未启用' -or $text -match '未啟用') {
        throw "Directory is not case-sensitive: $Path"
    }
    throw "Could not positively identify the fsutil case-sensitivity state for $Path."
}

if (-not (Test-IsAdministrator)) {
    throw "Run this Hot-path rename operator from an elevated Administrator PowerShell."
}
Write-Host "ADMINISTRATOR_OK"

Assert-FsutilCaseSensitiveEnabled $OldHotPath
Write-Host "FSUTIL_OLD_HOT_CASE_SENSITIVE_OK"

# The accepted target host returns ERROR_ACCESS_DENIED from the newer
# GetFileInformationByName case-sensitivity query even while elevated, while
# Microsoft's supported fsutil query succeeds. Load a process-local compatibility
# shim before invoking the original fail-closed operator. The original operator
# keeps all Docker, mount, serving-state, profile, and row-equivalence gates.
if (-not ("MarkOrbit.NativeCaseSensitivity" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace MarkOrbit {
    public static class NativeCaseSensitivity {
        [StructLayout(LayoutKind.Sequential)]
        public struct FILE_CASE_SENSITIVE_INFORMATION {
            public UInt32 Flags;
        }

        public static bool GetFileInformationByName(
            string fileName,
            int fileInformationClass,
            out FILE_CASE_SENSITIVE_INFORMATION fileInfoBuffer,
            UInt32 fileInfoBufferSize)
        {
            fileInfoBuffer = new FILE_CASE_SENSITIVE_INFORMATION();
            if (fileInformationClass != 2 || fileInfoBufferSize < 4) {
                return false;
            }

            var escaped = fileName.Replace("\"", "\\\"");
            var start = new ProcessStartInfo {
                FileName = "fsutil.exe",
                Arguments = "file queryCaseSensitiveInfo \"" + escaped + "\"",
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true
            };

            using (var process = Process.Start(start)) {
                if (process == null) {
                    return false;
                }
                string stdout = process.StandardOutput.ReadToEnd();
                string stderr = process.StandardError.ReadToEnd();
                process.WaitForExit();
                string text = stdout + "\n" + stderr;
                if (process.ExitCode != 0) {
                    return false;
                }
                string lower = text.ToLowerInvariant();
                if (lower.Contains("is enabled") || text.Contains("已启用") || text.Contains("已啟用")) {
                    fileInfoBuffer.Flags = 0x00000001;
                    return true;
                }
                if (lower.Contains("is disabled") || text.Contains("已禁用") || text.Contains("已停用") || text.Contains("未启用") || text.Contains("未啟用")) {
                    fileInfoBuffer.Flags = 0;
                    return true;
                }
                return false;
            }
        }
    }
}
'@
}

$operator = Join-Path $PSScriptRoot "rename-clickhouse-hot-path.ps1"
if (-not (Test-Path -LiteralPath $operator -PathType Leaf)) {
    throw "Underlying Hot-path rename operator not found: $operator"
}

& $operator `
    -OldHotPath $OldHotPath `
    -NewHotPath $NewHotPath `
    -ColdPath $ColdPath `
    -LogPath $LogPath

if ($LASTEXITCODE -ne 0) {
    throw "Underlying Hot-path rename operator failed with exit code $LASTEXITCODE."
}
