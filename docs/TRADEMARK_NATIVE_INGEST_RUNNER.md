# Trademark Native Ingest Runner

`TRADEMARK_NATIVE_INGEST_RUNNER_V1` is the reusable execution layer between a reviewed
source-native parser and `TRADEMARK_NATIVE_STORE_BUNDLE_V1`.

## Contract

A parser emits `NativeRecordEnvelope` objects with:

- `source_index`: contiguous 1-based logical record sequence for this source/pipeline;
- `record_key`: jurisdiction/source-native durable record identity;
- `native`: parsed authority fields;
- optional `source_payload`: raw/native evidence to preserve with observations.

The runner receives an already registered `source_object_id`, an explicit versioned `pipeline_id`,
reviewed `parser_version`, a `NativeStoreBundle`, and the record iterator.

It deliberately does not download data, register source objects/manifests, create native tables,
construct trademark identity, choose current-state winners, or interpret legal status.

## Durable resume

For every source object + pipeline, the runner reuses
`acquisition.global_trademark_ingest_run`.

A batch transaction contains both:

1. all mapped native observation writes for every bundle binding; and
2. the ingest-run checkpoint update.

Therefore a committed checkpoint cannot point past a partially committed record/party/goods/event
family. An exception rolls back the current batch and marks the durable run `FAILED`; a later call
resumes from the last committed checkpoint.

`max_records` limits newly committed logical source records in one invocation. If additional source
records exist, status remains `PARTIAL`; EOF promotes the run to `COMPLETE`.

## Parser sequence rules

Resume supports either:

- replay from `source_index=1`, in which case already checkpointed records are skipped; or
- a seekable parser starting exactly at `checkpoint + 1`.

After the first emitted record, indices must be contiguous and strictly increasing. Gaps,
duplicates and backward movement fail closed rather than silently skipping source evidence.

Because V1 uses contiguous logical record indexes, `rows_committed == checkpoint` is a durable ledger
invariant.

## Contract pinning

Before mutation the runner computes a deterministic contract hash over:

- runner version;
- jurisdiction/source/pipeline;
- parser version;
- country store schema;
- bundle/table/domain/native-column definitions;
- mapping version, identity targets, selectors, required/repeated flags and transform IDs.

Human notes and harmless declaration ordering do not affect the hash. If a source/pipeline already
has a runner-owned ingest run with a different contract hash, resume is refused. Parser/mapping/table
semantics must instead receive a reviewed versioned pipeline/parser/mapping identity.

Transform callable bytecode is not hashed; changing transform behavior requires an explicit mapping
version/transform-ID review.

## Safety boundaries

The runner verifies that the source object jurisdiction/source matches the bundle. It accepts no
legal status, renewal, brand-family or customer-intent semantics. Current-state projection remains a
separate jurisdiction contract.

A `COMPLETE` native ingest run means the source object was processed under this exact durable
pipeline contract. It does not mean the source release or jurisdiction is accepted/current or
trusted for silence.
