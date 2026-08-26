# M1.7 release promotion evidence

## Purpose

`VERSION` must not move from `M1.6` to `M1.7` merely because static CI is green or because issue #247 is closed. Promotion requires two machine-readable inputs:

1. the committed operator decision that the previously completed real target-host validation of `2023_5.zip` is accepted and must not be repeated; and
2. a current `CN_M16_LIGHTWEIGHT_SERVING_CHECKPOINT_V1` report from the target host.

The authoritative release contract is `MARKORBIT_M17_RELEASE_PROMOTION_V1` in `app.release_promotion`.

## Pinned prior-runtime provenance

The committed record is:

`evidence/release/cn_m16_prior_runtime_operator_acceptance.json`

It references `yoomarks/markorbit-data-engine#247`, comment `5421693252`, and pins the SHA-256 of the exact operator-decision text. The validator recomputes that text hash. Issue closure by itself is not evidence.

This record explicitly does **not** claim that a fresh full-corpus semantic acceptance was executed, does not reconstruct missing target-host details, and does not authorize replaying or rescanning `2023_5.zip`.

## Current target-host evidence

The current evidence is produced by the existing lightweight operator:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-cn-serving-state.ps1 -ExpectedFileName 2023_5.zip
```

This reads only PostgreSQL control state and ClickHouse `system.*` metadata. It does not replay the package, scan the corpus, run the M1.6 final checkpoint, or manage the persistent worker lifecycle.

Preserve the generated JSON report. Do not edit its claims. The promotion contract requires the lightweight evidence boundaries to remain explicit:

- `full_corpus_scan=false`
- `package_reprocessed=false`
- `full_corpus_semantic_acceptance_claimed=false`

## Promotion evaluation

Evaluate the preserved serving-state JSON without querying either database:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-platformization-m17-promotion.ps1 -CnServingCheckpointPath <report.json>
```

The evaluator is persisted-evidence-only. It does not run Docker, PostgreSQL, ClickHouse, package import, replay, or full-corpus acceptance. `PASS` yields `READY_FOR_M1_7`; a valid disk-pressure `WARN` yields `READY_FOR_M1_7_WITH_WARNINGS`. `BLOCKED`, malformed evidence, provenance drift, schema drift, an active CN package, missing critical serving parts, or any full-corpus/reprocessing overclaim fails closed.

## VERSION promotion procedure

A passing promotion report still does not change `VERSION`. The accepted target report must first be reviewed and then preserved as:

`evidence/release/cn_m16_lightweight_serving_checkpoint.json`

Only after that evidence is committed may a **separate** PR change root `VERSION` from `M1.6` to `M1.7`. `app.platformization_checkpoint` then recomputes `MARKORBIT_M17_RELEASE_PROMOTION_V1` from the committed operator evidence plus the committed serving-state evidence and rejects an unsupported M1.7 version.

Until the current target-host report exists and is accepted, the repository intentionally remains `M1.6` and reports `CODE_READY_PENDING_RUNTIME_ACCEPTANCE`.

## Scope exclusions

M1.7 release promotion does not authorize new large-country imports. Storage capacity work in #260/#262 remains a separate rollout gate before US/SG/global full-corpus expansion. It also does not enable recurring Singapore scheduling or resolve repository branch protection in #250.
