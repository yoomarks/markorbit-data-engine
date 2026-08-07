# CN Core Field Dictionary — M1.5

## `cn_case_current`

| Field | Meaning |
|---|---|
| `application_number` | Full CN case number, suffix included |
| `case_family_root` | Root CN case number |
| `suffix_path` | A/B/AA… candidate path |
| `filing_route` | `CN_DIRECT`, `MADRID_DESIGNATION_CN`, `UNKNOWN` |
| `international_registration_number` | WIPO IR number without CN `G` and suffix |
| `is_derived_case` | Structural suffix relation observed |
| `derivation_reason` | Legal reason; remains `UNKNOWN` without evidence |
| `classes` | All classes under the legal case |
| `source_rank` | Source precedence key, not ingestion time |
| `source_file` / `source_start_line` | Official package evidence location |

## `cn_case_scope_current`

| Field | Meaning |
|---|---|
| `source_item_count` | All source goods rows |
| `interpreted_active_item_count` | Items mapped to active by current mapping |
| `interpreted_inactive_item_count` | Items explicitly mapped inactive |
| `unmapped_status_item_count` | Items whose status meaning is unverified |
| `effective_item_count` | Active count only when interpretation is complete |
| `interpretation_complete` | Whether all source status codes are mapped |
| `goods_status_mapping_version` | Auditable mapping rule version |
| `goods_items_compact` | Single compact payload; no permanent raw-detail duplication |

## `cn_case_party_current`

| Field | Meaning |
|---|---|
| `mention_id` | Source-specific party mention |
| `entity_id` | Exact entity candidate where safely resolved |
| `relation_key` | Stable case-role-party relation key |
| `is_current` | Current relation under source precedence |
| `relation_status` | Observed current or superseded |
| `valid_from` / `valid_to` | Evidence-based relation interval |

## `cn_observed_event`

| Field | Meaning |
|---|---|
| `event_type` | Explainable observed change/event |
| `evidence_level` | Official observation or structural inference |
| `legal_effect` | Defaults to `NOT_DETERMINED` |
| `old_value_compact` / `new_value_compact` | Human-readable compact difference |
| `source_file` / `source_row` | Source evidence |

## `cn_case_relation_current`

A `DERIVED_CASE` relation means the number structure establishes a family link.
It does not establish whether the cause was partial refusal, review, assignment,
voluntary division or another official procedure.
