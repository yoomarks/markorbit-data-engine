# M1.5 Test Plan

## Automated tests

- CN direct and G-number parsing;
- `G602365A -> G602365 / A / IR 602365`;
- filing-year partition and monthly-patch precedence;
- numeric goods codes remain unmapped;
- exact entity candidate boundaries;
- pre-1970 Date32 parsing;
- malformed CSV and continuation recovery;
- ClickHouse 24.8 `FINAL` alias order;
- permanent schema contract.

## Local Docker validation

The supplied development machine must perform the final ClickHouse 24.8 SQL
execution test because the build environment used to assemble the ZIP does not
run the user's Docker daemon.

Run:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\reset-m15.ps1
docker compose stop worker
powershell.exe -ExecutionPolicy Bypass -File .\scripts\run-cn.ps1
```

Then inspect:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\check-cn-counts.ps1
powershell.exe -ExecutionPolicy Bypass -File .\scripts\inspect-cn-case.ps1 -ApplicationNumber G602365A
powershell.exe -ExecutionPolicy Bypass -File .\scripts\export-cn-field-audit.ps1
```
