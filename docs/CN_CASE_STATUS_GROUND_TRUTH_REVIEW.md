# CN Case Status Inference — Ground-Truth Review V1

Status: MANUAL VALIDATION WORKFLOW

This workflow turns a historical inference audit report into a reviewer-friendly CSV and then scores completed labels. It does not write inferred status, reviewer opinions, or validation labels into official fact tables.

## Workflow

1. Run the historical audit with the persistent worker stopped:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-cn-case-status-inference.ps1 `
  -SamplePerRule 50
```

2. Build the review packet from the generated JSON report:

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  .\scripts\build-cn-case-status-review-packet.ps1 `
  -AuditPath .\reports\cn_case_status_inference_<timestamp>.json
```

3. Open the generated UTF-8 CSV in Excel or another spreadsheet editor. Fill the reviewer fields only; do not change the frozen inference/evidence columns.

4. Score the completed review packet:

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  .\scripts\score-cn-case-status-review-packet.ps1 `
  -ReviewPath .\reports\cn_case_status_inference_<timestamp>_review.csv
```

## Review labels

Use exactly one of these values in `review_label`:

- `NOT_REVIEWED` — not checked yet;
- `CONFIRMED` — official evidence supports the heuristic candidate;
- `REJECTED` — official evidence contradicts or materially disproves the heuristic candidate;
- `INSUFFICIENT_EVIDENCE` — available official evidence is not enough to decide.

`CONFIRMED` and `REJECTED` are decisive labels and require `official_source_ref`. This prevents unsupported reviewer opinion from being counted as ground truth.

Recommended reviewer fields:

- `official_status` — official procedural/status wording if available;
- `official_cause` — official cause such as withdrawal, refusal, opposition result, invalidation, cancellation, non-use cancellation, non-renewal, etc.;
- `official_event_date` — date of the official event/decision when available;
- `official_source_ref` — CNIPA notice/publication/document identifier or a stable internal evidence reference;
- `reviewer`, `reviewed_at`, `notes` — audit trail for the manual decision.

## Scoring

The score JSON reports overall and per-rule counts:

- reviewed / unreviewed;
- decisive reviews;
- confirmed / rejected;
- insufficient evidence;
- precision = `CONFIRMED / (CONFIRMED + REJECTED)`;
- review coverage.

`INSUFFICIENT_EVIDENCE` is intentionally excluded from precision and retained as an evidence-gap signal.

## Promotion boundary

No score automatically changes a rule from `EMPIRICAL` to `VALIDATED`. The scoring output always ends at `MANUAL_MODEL_REVIEW_REQUIRED`. Promotion requires human review of sample size, precision, evidence quality, contradiction patterns, and rule overlap.

## Determinism and traceability

Each row receives a deterministic `review_id` derived from model version, data coverage date, application number, rule ID, and inferred cause. Duplicate samples collapse to one review row. A scoring file cannot mix multiple model versions, and duplicate review IDs are rejected.

The packet uses UTF-8 with BOM so Chinese evidence notes remain practical to review in Excel on Windows.
