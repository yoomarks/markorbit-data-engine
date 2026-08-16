# MarkOrbit Global Fact / Event Envelope V1

`MARKORBIT_FACT_EVENT_ENVELOPE_V1`

The envelope gives MarkOrbit consumers and future jurisdiction adapters a common outer structure for **source facts and source-observed/source-derived events** without pretending that different trademark systems have the same law.

## Core rule

```text
common envelope != common legal meaning
```

CNIPA, USPTO, WIPO, EUIPO, JPO and other authorities may expose facts that can be grouped into broad navigation families such as publication, registration, priority, ownership recordation, or proceedings. That grouping exists so Data Engine, MO Brain and downstream products can navigate heterogeneous source facts consistently. It is **not** a declaration that two source codes, statuses, deadlines, procedures, rights, or legal effects are equivalent across jurisdictions.

The normalization semantic is permanently explicit:

```text
NAVIGATION_GROUPING_ONLY_NOT_CROSS_JURISDICTION_LEGAL_EQUIVALENCE
```

## Layer boundary

M1.7 freezes the following separation:

```text
L0  SOURCE
    official/raw authoritative material
        ↓
L1  FACT
    durable source facts
        ↓
L2  SOURCE EVENT
    observed source-fact change / source procedural observation
        ↓
L3  SIGNAL / INTELLIGENCE
    Alert Engine, Brain interpretation, risk, deadline reasoning, recommendations
        ↓
L4  BUSINESS ACTION
    Matter, Task, Reminder, outreach, filing, provider execution
```

`MARKORBIT_FACT_EVENT_ENVELOPE_V1` covers L1 and L2 only.

An Alert/Signal is not implicitly promoted back to a source fact merely because it contains source provenance. Brain reasoning and business workflow remain outside this envelope.

## Required outer sections

Every envelope carries:

- `jurisdiction`
- `resource_kind`: `FACT`, `EVENT`, `RELATION`, or `SNAPSHOT`
- `semantic_family`
- `subject`
- `provenance`
- `observation`
- `normalization`
- source-specific `payload`

The source-specific payload remains authoritative for domain meaning. The common envelope is additive metadata, not a replacement fact model.

## Provenance

The common provenance reference can retain:

- source authority and source domain;
- source package ID and source rank;
- source effective date/time;
- source file;
- source start/end line;
- source row hash.

This keeps normalization reversible back to evidence and allows MO Brain or another consumer to distinguish “what Data Engine grouped it as” from “what the source actually said.”

## Observation and normalization

Source meaning is preserved separately from normalization:

```json
{
  "observation": {
    "source_type": "...",
    "source_code": "...",
    "source_text": "..."
  },
  "normalization": {
    "normalized_type": "...",
    "confidence": 0.95,
    "cross_jurisdiction_legal_equivalence": false
  }
}
```

A normalized type is rejected when all raw source type/code/text fields are empty. This prevents a normalized label from becoming the only surviving meaning.

## Semantic families

V1 intentionally uses broad fact-navigation families rather than legal conclusions:

- `APPLICATION`
- `EXAMINATION`
- `PUBLICATION`
- `REGISTRATION`
- `GOODS_SERVICES`
- `PARTY`
- `REPRESENTATION`
- `PRIORITY`
- `MADRID`
- `OWNERSHIP_RECORDATION`
- `MAINTENANCE_RENEWAL`
- `PROCEEDING`
- `CASE_RELATION`
- `SOURCE_QUALITY`
- `OTHER`

For example, a USPTO recorded assignment and another jurisdiction's ownership-related record may share the `OWNERSHIP_RECORDATION` family for navigation. That does not mean either record proves legal title, and it does not mean the two systems have equivalent legal effect.

## Permanent guards

Every V1 envelope freezes:

```text
authority = DATA_ENGINE_SOURCE_FACT
legal_conclusion = false
actionability = SOURCE_FACT_ONLY
cross_jurisdiction_legal_equivalence = false
consumer_writeback = false
```

The envelope itself cannot authorize a filing, deadline action, legal-status conclusion, ownership conclusion, TTAB/outcome conclusion, customer outreach, or other business action.

## Adoption policy

This M1.7 slice introduces the contract and builder without changing existing `/api/v1` business payloads. Existing consumer contracts remain stable.

Future domain adapters may emit this envelope at their published source-fact/event boundary. Existing CN and US resources can be admitted incrementally only where the source-specific payload and provenance can be preserved exactly and compatibility tests prove no semantic loss.
