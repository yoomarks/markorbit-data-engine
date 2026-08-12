# Contact Task Control Center

Version: `CONTACT_TASK_CONTROL_V1`

## Purpose

Contact ingestion now has a managed incoming-folder workflow instead of requiring an operator to point the importer at an arbitrary file path every time.

The canonical directories are:

```text
raw_data/incoming/contacts/
raw_data/archive/contacts/
```

Supported source files:

- `.xlsx`
- `.csv`
- `.tsv`
- `.json`
- `.jsonl`
- `.ndjson`
- `.zip`

## Automatic discovery

The Data Engine API starts a lightweight contact discovery loop. The default interval is 60 seconds and is configurable through `CONTACT_SCAN_INTERVAL_SECONDS`.

Discovery performs only these actions:

1. list supported files under `incoming/contacts`;
2. compute SHA-256;
3. run the existing `CONTACT_INGEST_V1` planner;
4. persist a task and plan summary in PostgreSQL;
5. classify the task as `READY` or `INVALID`.

Discovery **never applies the contact import automatically**.

A PostgreSQL advisory lock prevents multiple API processes from registering the same discovery cycle concurrently. The task table is also unique by source SHA-256, so the same source content remains idempotent.

## Task statuses

| Status | Meaning |
|---|---|
| `READY` | The source parsed successfully and is available for explicit import. |
| `PROCESSING` | An operator-triggered import is running. |
| `SUCCESS` | Import completed and the source was moved to `archive/contacts`. |
| `FAILED` | Import was attempted and failed; it can be retried. |
| `INVALID` | Discovery could not build a valid import plan. |
| `MISSING_FILE` | The source disappeared before an explicit import could begin. |

## Explicit apply boundary

The scanner only creates work. Actual writes into `entity.*` and `contact.*` happen only when an operator clicks **执行导入** in the Contacts page or calls the apply API directly.

Before apply, the source SHA-256 is recomputed. A file that changed after discovery is rejected and must be discovered as a new task.

After successful import, the exact source file is moved to:

```text
raw_data/archive/contacts/<task_id>__<original_filename>
```

The task retains both the original incoming path and the final archive path as operational evidence.

## Control Center

Open:

```text
http://<data-engine-host>/contacts
```

The page provides:

- incoming folder path and scan interval;
- READY / PROCESSING / SUCCESS / error counts;
- total entities, people, channels, and observations;
- detected source profile and source size;
- plan metrics: rows, entities, people, channels, skipped rows;
- field mapping detail for each sheet/member;
- import-run history;
- manual scan;
- explicit import/retry actions.

The page refreshes its read model periodically, while file recognition is performed by the server-side discovery loop.

## API

```text
GET  /api/admin/contacts/summary
GET  /api/admin/contacts/tasks
GET  /api/admin/contacts/tasks/{task_id}
POST /api/admin/contacts/scan
POST /api/admin/contacts/tasks/{task_id}/apply
```

These routes belong to the Data Engine admin/control plane. They are not part of the public `/api/v1` source-fact integration contract.

## Safety boundary

This feature does not:

- auto-send email, WhatsApp, or other outreach;
- create marketing campaigns;
- redefine CN/US trademark source facts;
- mutate ClickHouse trademark fact tables;
- bypass the existing contact entity-match ambiguity protections.
