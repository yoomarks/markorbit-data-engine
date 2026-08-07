# M1.1 Windows Script Patch

- PowerShell scripts are ASCII/CRLF for Windows PowerShell 5.1 compatibility.
- Docker native command exit codes are checked explicitly.
- Reset/start no longer print false success after image pull or build failure.
- Added `scripts/check-docker.ps1` for Docker Hub DNS, TCP and HTTPS diagnostics.
