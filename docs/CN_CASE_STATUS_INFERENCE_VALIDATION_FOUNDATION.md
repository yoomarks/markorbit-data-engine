# CN Case Status Inference — Validation Foundation

Status: **EMPIRICAL VALIDATION FOUNDATION**

This phase starts the implementation of `CN_CASE_STATUS_INFERENCE_MODEL_V1` without turning
heuristic results into official facts.

## Scope

- Add a pure deterministic evaluator in `app/cn/case_status_inference.py`.
- Keep official facts, durable goods scope, case-status inference, and cause attribution separate.
- Require reconstructed durable goods counts; a monthly patch is never treated as a complete scope.
- Never interpret goods code `0`, `1`, or `2` directly as a case legal status or legal cause.
- Return explicit `rule_id`, model version, model stage, confidence score/band, scope, summary, and evidence references.
- Allow official cause evidence to supersede heuristic candidates without deleting either layer from the data model.
- Keep R7 renewal timing externally supplied through `renewal_grace_end`; this phase does not hard-code a legal renewal/grace calculation.

## Model version

`CN_CASE_STATUS_INFERENCE_V1_EMPIRICAL`

Rules implemented for validation:

- R1 early total loss after filing;
- R2 preliminary publication + partial final goods loss;
- R3 preliminary publication + no registration + long delay + total final goods loss;
- R4 registration publication + partial final goods loss;
- R5 first final goods loss before the three-year registration mark;
- R6 first final goods loss at/after the three-year registration mark;
- R7 inactivity first observed after an explicitly supplied renewal/grace deadline with no renewal/restoration evidence.

## Non-goals

This phase does **not**:

- write an inferred case status into `cn_case_current`;
- create an official status field from goods codes;
- attribute a definitive legal cause;
- infer deletion from monthly omission;
- calculate CN renewal/grace deadlines in the heuristic engine;
- mark the model as `VALIDATED`.

## Promotion gate

Before production persistence, run the evaluator over a large historical sample and measure:

1. rule hit counts and overlap;
2. filing/registration-year distribution;
3. partial vs total outcomes using the reconstructed durable goods universe;
4. later-fact contradiction rates;
5. manual agreement against known CNIPA notices/decisions;
6. calibration of confidence scores by rule.

Only after those checks should a later phase introduce a persisted inference table/API.
