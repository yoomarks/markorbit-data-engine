# M1.6 Build Validation

M1.6 uses two validation layers: repository CI for deterministic code/runtime contracts, and local Docker/data gates for the user's loaded CN source corpus.

## Repository CI

Every pull request and push to `main` runs two independent jobs.

### Python contract job

- editable package installation on Python 3.12;
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

### Runtime image job

- validates `docker compose` configuration;
- builds `docker/api.Dockerfile` from the real repository context;
- starts a one-shot Python process inside the built image;
- imports `app.main` and requires the image runtime marker to resolve to `M1.6`.

This catches packaging/context regressions that a source-only Python test cannot detect, including a missing `VERSION` file in the image.

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
