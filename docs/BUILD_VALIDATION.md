# M1.6 Build Validation

M1.6 uses two validation layers: repository CI for deterministic code contracts, and local Docker/data gates for the user's loaded CN source corpus.

## Repository CI

Every pull request and push to `main` runs on Python 3.12 and must pass:

- editable package installation;
- Ruff across `app` and `tests`;
- the complete pytest suite.

The tests freeze, among other contracts:

- CN package/source precedence and interrupted-ingest recovery;
- quote-aware CSV boundaries;
- strict M1.6 goods identity and monthly omission preservation;
- M1.6 ClickHouse SQL compatibility and memory-safety assumptions;
- case-status inference evidence boundaries;
- historical audit data-clock behavior and deterministic sampling;
- manual ground-truth packet provenance and scoring;
- runtime release metadata and M1.6 documentation/validation entry points.

## Local runtime/schema validation

A clean M1.6 replay requires the user's Docker environment and authoritative raw ZIP files. Keep the persistent worker stopped and run:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\reset-m16.ps1
powershell.exe -ExecutionPolicy Bypass -File .\scripts\validate-m16.ps1
powershell.exe -ExecutionPolicy Bypass -File .\scripts\validate-cn-contract.ps1
powershell.exe -ExecutionPolicy Bypass -File .\scripts\validate-cn-fixture.ps1
powershell.exe -ExecutionPolicy Bypass -File .\scripts\validate-m16-goods.ps1
```

`validate-m16.ps1` verifies that API health reports `M1.6`, PostgreSQL and ClickHouse are healthy, and the durable goods item/observation/lifecycle schema is present.

## Real-data acceptance

Repository CI cannot substitute for multi-million-row ClickHouse replay. After the fast gates pass, replay the authoritative packages with the worker still stopped and run the M1.6 acceptance/identity/monthly audits.

Case-status inference validation is a separate downstream activity. It must use a stable loaded-data snapshot, source coverage as its clock, deterministic review samples, and manual official-evidence ground truth before any rule can be considered for promotion beyond `EMPIRICAL`.
