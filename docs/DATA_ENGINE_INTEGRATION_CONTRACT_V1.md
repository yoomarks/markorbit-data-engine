# MarkOrbit Data Engine Integration Contract V1

## Status

`MARKORBIT_DATA_ENGINE_INTEGRATION_V1`

This contract defines the service boundary between MarkOrbit Data Engine and MarkOrbit products/services. It is additive and does not change ingestion, replay, database schemas, source precedence, or legal-semantics rules.

## Service role

MarkOrbit Data Engine is a **Source Fact Service**. It owns ingestion and read models derived from authoritative public trademark data sources.

Consumers may use versioned API contracts. They must not depend on Data Engine storage implementation.

Permanent boundary:

```text
MarkOrbit consumer
    -> versioned HTTP API / change feed
    -> MarkOrbit Data Engine
    -> Data Engine-owned PostgreSQL / ClickHouse / raw source storage
```

The following is prohibited:

```text
Core / Gateway / MarkReg / Lite
    -> direct SQL to Data Engine PostgreSQL or ClickHouse
    -> direct database-volume or database-file reads
    -> direct mutation of Data Engine source-fact tables
```

## Consumer Query Plane

The stable V1 consumer prefix is:

```text
/api/v1
```

All routes under `/api/v1` are read-only `GET` routes. No ingestion, replay, retry, reset, repair, audit mutation, or source-package mutation may be added under this prefix.

V1 resources:

- `GET /api/v1/contract`
- `GET /api/v1/cn/cases/{application_number}`
- `GET /api/v1/us/cases/{serial_number}`
- `GET /api/v1/us/cases/{serial_number}/360`
- `GET /api/v1/us/cases/{serial_number}/history`
- `GET /api/v1/us/cases/{serial_number}/assignments`
- `GET /api/v1/us/cases/{serial_number}/ttab`
- `GET /api/v1/us/changes`

The V1 layer delegates to existing domain implementations. It does not duplicate SQL or create a second fact model.

## Response authority

V1 responses use a common owner envelope:

```json
{
  "contract_version": "MARKORBIT_DATA_ENGINE_INTEGRATION_V1",
  "engine_version": "M1.6",
  "source_owner": "MARKORBIT_DATA_ENGINE",
  "jurisdiction": "US",
  "resource_kind": "TRADEMARK_CASE",
  "authority": "DATA_ENGINE_FACT_READ_MODEL",
  "legal_conclusion": false,
  "payload": {}
}
```

The envelope establishes owner and contract provenance. Domain payload semantics remain authoritative:

- US application raw status/event/statement facts are not MarkOrbit legal-status conclusions.
- Assignment data is recorded-assignment fact evidence, not a legal-title determination.
- TTAB data is procedural fact evidence, not a substantive-rights or legal-outcome determination.
- Data Engine normalization or aggregation does not authorize downstream business action.

## Change Feed Plane

The V1 change feed is:

```text
GET /api/v1/us/changes
```

It preserves the existing lossless observation cursor model. A change-feed item means Data Engine observed a source-fact change. It does not mean:

- a legal conclusion changed;
- a MarkOrbit Matter changed;
- a task/reminder must be created;
- customer outreach is authorized;
- a filing or provider action is authorized.

Consumers own those decisions in their own service boundaries.

The V1 feed is pull-based HTTP. A future event transport may publish the same owner-produced semantics, but event transport must not create a second authority model.

## Control/Admin Plane

Existing operational endpoints remain outside the consumer contract, including prefixes such as:

```text
/api/admin
/api/jobs
```

These endpoints are for Data Engine operations. Core, Gateway, Lite, MarkReg, Knowledge, or other business consumers must not use them as business integration contracts.

In particular, consumer services must not trigger Data Engine ingestion, replay, retry, reset, or repair as a consequence of normal product behavior.

## Writeback policy

There is **no consumer writeback into source facts**.

Examples that must stay outside Data Engine official/source-fact tables:

- user-confirmed client/entity mapping;
- portfolio membership;
- user annotations;
- legal analysis;
- recommended action;
- Opportunity, Intake, Order, Matter, Task, Reminder, Execution, Payment, provider or filing state.

Those records belong to their proper MarkOrbit Owning Service and may retain Data Engine provenance references.

If a user believes an official/source fact is wrong, MarkOrbit may store a separate user/business annotation or correction claim. It must not silently overwrite the Data Engine source observation.

## Consumer ownership

Data Engine answers questions about source facts. It does not become a universal business-state service.

Typical composition:

```text
Gateway / Product
    -> Core for semantic identity / permission references
    -> Data Engine for trademark source facts
    -> owning business service for workflow state
```

Core is not required to proxy all Data Engine traffic. Gateway or an owning service may consume the V1 API where the product contract requires it.

## Storage independence

Consumers must know service connection configuration, not storage location. Moving Data Engine PostgreSQL/ClickHouse from a Windows Docker volume to another disk, Linux host, NAS, or managed database must not require changes to Core business semantics.

## Versioning

Breaking consumer-contract changes require a new versioned integration surface. Existing unversioned `/api/...` routes remain implementation/legacy surfaces unless explicitly admitted into a versioned contract.

V1 does not authorize removal of existing routes.

## Non-goals

This contract does not:

- change live CN/US replay state;
- mutate existing databases;
- introduce cross-service SQL;
- create Core-side canonical trademark copies;
- add business writeback to Data Engine;
- infer Assignment legal title;
- infer TTAB substantive rights or legal outcome;
- authorize external execution;
- define production authentication or network deployment. Those are deployment/security work layered on this contract.
