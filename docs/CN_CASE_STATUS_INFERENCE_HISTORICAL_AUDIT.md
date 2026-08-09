# CN Case Status Inference — Historical Audit V1

Status: EMPIRICAL VALIDATION TOOLING

This audit connects the M1.6 durable goods lifecycle to the case-status inference model without persisting inferred legal status into official fact tables.

## Safety boundaries

1. The audit clock is the latest successfully loaded CN monthly package coverage date, using `dataset_release_date` or `source_period_end`. Wall-clock time is never substituted for missing source coverage.
2. An explicit `--as-of` date may only move the audit backward. It cannot exceed loaded CN data coverage.
3. `FIRST_OBSERVED` goods observations are not treated as legal-event dates. Only a real `STATUS_CHANGED` transition into an inactive operational effect can supply temporal loss evidence.
4. A total-loss date is available only when every currently final-inactive item has a dated status-change lineage.
5. Monthly omission is never deletion. All counts come from the durable M1.6 current goods universe.
6. R2 is restricted to cases without a registration publication; registered partial outcomes remain available to the more specific R4/R5/R6 validation paths instead of double-counting the broad pre-registration rule.
7. Rule hits remain empirical candidates. They do not become official facts and are not production-validated legal conclusions.

## Population

The audit scans cases whose current durable goods lifecycle contains at least one `INACTIVE_HIGH_CONFIDENCE` or `INACTIVE_CONFIRMED` item. It aggregates item state to the complete application-number level before evaluating R1-R7.

The query is keyset-paginated by application number and limits observation joins to the current batch so the validation can run over large historical data without loading the full candidate population into Python memory.

## Output

The JSON report includes:

- loaded-data coverage date and effective `as_of_date`;
- number of cases with inactivity signals;
- rule hit counts for R1-R7;
- inferred cause, scope and confidence distributions;
- number of cases that trigger multiple distinct heuristic causes;
- samples for each rule and for overlapping causes;
- unknown-goods and missing-temporal-lineage counts;
- invalid evidence rows;
- explicit model limitations and promotion decision.

A `PASS` result only means that the audit evidence was internally consistent. It does **not** mean the heuristic rules are legally validated.

## Run

Keep the persistent worker stopped to audit a stable database snapshot:

```powershell
docker compose stop worker
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-cn-case-status-inference.ps1
```

Optional historical cutoff:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-cn-case-status-inference.ps1 `
  -AsOf 2022-12-31 `
  -BatchSize 5000 `
  -SamplePerRule 20
```

The script writes the JSON report under `reports\cn_case_status_inference_<timestamp>.json` and leaves the persistent worker stopped.

## Repository validation

The repository CI gate runs on Python 3.12 and requires both Ruff and the complete pytest suite to pass. The historical ClickHouse audit itself remains a local stable-snapshot validation because CI does not contain the user's loaded CN source database.

## Current known limitation

R7 is intentionally not operational in this audit because renewal/grace deadlines plus renewal/restoration observations have not yet been reconstructed as durable official evidence. `valid_until` alone is not silently converted into a legal renewal deadline.

## Promotion gate

`CN_CASE_STATUS_INFERENCE_V1_EMPIRICAL` must remain `EMPIRICAL` until rule samples are manually checked against official CNIPA notices/events. Required next validation work:

- manually label a stratified sample per rule;
- measure precision by rule and confidence band;
- inspect R4 versus R5/R6 overlap cases;
- identify later official facts that contradict an earlier heuristic candidate;
- revise thresholds/rules and rerun until the evidence supports promotion.
