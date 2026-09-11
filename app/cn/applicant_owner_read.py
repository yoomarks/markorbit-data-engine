from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
import uuid

from app.applicant_owner_read import (
    APPLICANT_CANDIDATE_TYPE, TRADEMARK_CANDIDATE_TYPE,
    OwnerReadConflict, OwnerReadInvalid, OwnerReadUnavailable,
    applicant_query, applicant_reference, applicant_revalidation_query,
    assert_exact_source_reference, max_observed, next_portfolio_cursor,
    page_payload, portfolio_cursor_state, request_context, sha256_ref,
    source_reference, source_snapshot,
)
from app.cn.research_filing_to_prelim_duration import _serving_epoch
from app.version import engine_version

APPLICANT_PREFIX = "cn:applicant:"
APPLICANT_SOURCE_PREFIX = "CN_APPLICANT:"
TRADEMARK_PREFIX = "cn:trademark:"
TRADEMARK_SOURCE_PREFIX = "CN_TRADEMARK:"
PARTY_ROLES = ("OWNER", "CO_OWNER")
READ_SETTINGS = {"max_threads": 1, "max_rows_to_read": 5_000_000, "read_overflow_mode": "throw"}

@dataclass(frozen=True, slots=True)
class OwnerReadResult:
    fact_state: str
    payload: dict[str, Any] | None


def _sql_text(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _dict_rows(result: Any) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def _entity_id(candidate_id: str) -> str:
    if not candidate_id.startswith(APPLICANT_PREFIX):
        raise OwnerReadInvalid("CN applicant candidate id has the wrong prefix")
    try:
        return str(uuid.UUID(candidate_id[len(APPLICANT_PREFIX):]))
    except ValueError as exc:
        raise OwnerReadInvalid("CN applicant candidate id is malformed") from exc


def _source_id(entity_id: str) -> str:
    return f"{APPLICANT_SOURCE_PREFIX}{entity_id}"


def _epoch_version(epoch: Any) -> str:
    return epoch.watermark


def _guard_epoch(serving_epoch_getter: Any) -> Any:
    try:
        return serving_epoch_getter()
    except RuntimeError as exc:
        raise OwnerReadUnavailable(str(exc)) from exc


def _candidate_rows(client: Any, entity_id: str) -> list[dict[str, Any]]:
    roles = ",".join(_sql_text(role) for role in PARTY_ROLES)
    return _dict_rows(client.query(f"""
        SELECT application_number, role, relation_key, raw_name, normalized_name,
               source_row_hash, record_hash, source_rank, ingested_at
        FROM markorbit_facts.cn_case_party_current FINAL
        WHERE is_deleted = 0 AND is_current = 1
          AND role IN ({roles})
          AND entity_id = toUUID({_sql_text(entity_id)})
        ORDER BY application_number, role, relation_key
        LIMIT 100001
    """, settings=READ_SETTINGS))


def _candidate_material(entity_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) > 100_000:
        raise OwnerReadUnavailable("CN applicant candidate exceeds owner-read contribution bound")
    return {
        "jurisdiction": "CN",
        "entity_id": entity_id,
        "contributors": [
            {
                "application_number": str(row["application_number"]),
                "role": str(row["role"]),
                "relation_key": str(row["relation_key"]),
                "source_row_hash": str(row["source_row_hash"]),
                "record_hash": str(row["record_hash"]),
                "source_rank": int(row["source_rank"]),
            }
            for row in rows
        ],
    }


def _candidate_projection(
    *, entity_id: str, rows: list[dict[str, Any]], source_version: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not rows:
        raise KeyError(entity_id)
    names = sorted({str(row.get("raw_name") or "").strip() for row in rows if str(row.get("raw_name") or "").strip()})
    display_name = names[0] if names else str(rows[0].get("normalized_name") or "").strip()
    if not display_name:
        raise OwnerReadUnavailable("CN applicant candidate has no display name")
    observed_at = max_observed([row.get("ingested_at") for row in rows])
    source = source_reference(
        jurisdiction="CN", source_kind="APPLICANT_IDENTITY",
        source_id=_source_id(entity_id), source_version=source_version,
        fingerprint=sha256_ref(_candidate_material(entity_id, rows)), observed_at=observed_at,
    )
    candidate_id = f"{APPLICANT_PREFIX}{entity_id}"
    candidate = {
        "candidate_type": APPLICANT_CANDIDATE_TYPE,
        "applicant_candidate_id": candidate_id,
        "display_name": display_name,
        "alternate_names": names[:20],
        "source_reference": source,
        "match_kind": "EXACT_SOURCE_REFERENCE",
        "review_required": True,
        "verified_legal_identity": False,
        "customer_relationship_established": False,
    }
    return candidate, source


def _stable_epoch_read(
    *, client: Any, applicant_candidate_id: str, expected_source: Mapping[str, Any],
    serving_epoch_getter: Any,
) -> tuple[Any, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    before = _guard_epoch(serving_epoch_getter)
    source_version = _epoch_version(before)
    entity_id = _entity_id(applicant_candidate_id)
    rows = _candidate_rows(client, entity_id)
    after = _guard_epoch(serving_epoch_getter)
    if after != before:
        raise OwnerReadUnavailable("CN serving epoch changed during owner read")
    if not rows:
        raise KeyError(entity_id)
    candidate, source = _candidate_projection(
        entity_id=entity_id, rows=rows, source_version=source_version,
    )
    assert_exact_source_reference(expected_source, source)
    return before, candidate, source, rows


def read_applicant_exact(
    *, client: Any, workspace_id: str, request_id: str,
    applicant_candidate_id: str, expected_source: Mapping[str, Any],
    serving_epoch_getter: Any = _serving_epoch,
) -> OwnerReadResult:
    context = request_context(workspace_id, request_id)
    try:
        epoch, candidate, _source, _rows = _stable_epoch_read(
            client=client, applicant_candidate_id=applicant_candidate_id,
            expected_source=expected_source, serving_epoch_getter=serving_epoch_getter,
        )
    except KeyError:
        return OwnerReadResult("not_found", None)
    query = applicant_revalidation_query(
        context=context, jurisdiction="CN", applicant_candidate_id=applicant_candidate_id,
    )
    snapshot = source_snapshot(_epoch_version(epoch), candidate["source_reference"]["observed_at"])
    return OwnerReadResult("observed", page_payload(
        query=query, snapshot=snapshot, results=[candidate], next_cursor=None,
        engine_version=engine_version(),
    ))


def _portfolio_bindings(
    client: Any, entity_id: str, after_application: str, limit: int,
) -> list[dict[str, Any]]:
    roles = ",".join(_sql_text(role) for role in PARTY_ROLES)
    after_clause = f" AND application_number > {_sql_text(after_application)}" if after_application else ""
    return _dict_rows(client.query(f"""
        SELECT application_number,
               arraySort(groupArray(concat(
                   role, '|', relation_key, '|', source_row_hash, '|', record_hash,
                   '|', toString(source_rank)
               ))) AS applicant_binding_hashes,
               max(ingested_at) AS applicant_observed_at
        FROM markorbit_facts.cn_case_party_current FINAL
        WHERE is_deleted = 0 AND is_current = 1
          AND role IN ({roles})
          AND entity_id = toUUID({_sql_text(entity_id)}){after_clause}
        GROUP BY application_number
        ORDER BY application_number
        LIMIT {int(limit)}
    """, settings=READ_SETTINGS))


def _case_rows(client: Any, applications: list[str]) -> dict[str, dict[str, Any]]:
    if not applications:
        return {}
    clause = ",".join(_sql_text(item) for item in applications)
    rows = _dict_rows(client.query(f"""
        SELECT application_number, mark_name_raw, classes,
               source_row_hash, record_hash, source_rank, ingested_at
        FROM markorbit_facts.cn_case_current FINAL
        WHERE is_deleted = 0 AND application_number IN ({clause})
        ORDER BY application_number
    """, settings=READ_SETTINGS))
    return {str(row["application_number"]): row for row in rows}


def _trademark_projection(
    *, application_number: str, binding: Mapping[str, Any], case: Mapping[str, Any],
    applicant: Mapping[str, Any], source_version: str,
) -> dict[str, Any]:
    candidate_id = f"{TRADEMARK_PREFIX}{application_number}"
    observed_at = max_observed([binding.get("applicant_observed_at"), case.get("ingested_at")])
    material = {
        "jurisdiction": "CN",
        "application_number": application_number,
        "case": {
            "mark_name_raw": str(case.get("mark_name_raw") or ""),
            "classes": [int(item) for item in (case.get("classes") or [])],
            "source_row_hash": str(case.get("source_row_hash") or ""),
            "record_hash": str(case.get("record_hash") or ""),
            "source_rank": int(case.get("source_rank") or 0),
        },
        "applicant_binding_hashes": list(binding.get("applicant_binding_hashes") or []),
    }
    source = source_reference(
        jurisdiction="CN", source_kind="TRADEMARK_RECORD",
        source_id=f"{TRADEMARK_SOURCE_PREFIX}{application_number}",
        source_version=source_version, fingerprint=sha256_ref(material), observed_at=observed_at,
    )
    return {
        "candidate_type": TRADEMARK_CANDIDATE_TYPE,
        "trademark_candidate_id": candidate_id,
        "applicant": dict(applicant),
        "jurisdiction": "CN",
        "mark_text": str(case.get("mark_name_raw") or "") or None,
        "application_number": application_number,
        "registration_number": None,
        "classes": sorted({int(item) for item in (case.get("classes") or []) if 1 <= int(item) <= 45}),
        "source_reference": source,
        "official_truth_verified": False,
        "legal_conclusion_created": False,
        "workspace_relationship_established": False,
    }


def _after_application(candidate_id: str) -> str:
    if not candidate_id:
        return ""
    if not candidate_id.startswith(TRADEMARK_PREFIX):
        raise OwnerReadInvalid("CN portfolio cursor candidate id is malformed")
    return candidate_id[len(TRADEMARK_PREFIX):]


def read_portfolio(
    *, client: Any, workspace_id: str, request_id: str,
    applicant_candidate_id: str, expected_source: Mapping[str, Any],
    page_size: int = 50, cursor: str | None = None,
    serving_epoch_getter: Any = _serving_epoch,
) -> OwnerReadResult:
    context = request_context(workspace_id, request_id)
    try:
        epoch, _candidate, source, _rows = _stable_epoch_read(
            client=client, applicant_candidate_id=applicant_candidate_id,
            expected_source=expected_source, serving_epoch_getter=serving_epoch_getter,
        )
    except KeyError:
        return OwnerReadResult("not_found", None)
    source_version = _epoch_version(epoch)
    applicant = applicant_reference(applicant_candidate_id, source)
    query = applicant_query(
        context=context, jurisdiction="CN", applicant_candidate_id=applicant_candidate_id,
        applicant_source=source, page_size=page_size,
    )
    after_id, page_number, emitted_before = portfolio_cursor_state(
        token=cursor, query=query, source_version=source_version,
    )
    remaining = 500 - emitted_before
    capacity = min(page_size, remaining)
    if capacity <= 0:
        raise OwnerReadInvalid("Applicant Portfolio result hard bound is exhausted")
    entity_id = _entity_id(applicant_candidate_id)
    bindings = _portfolio_bindings(client, entity_id, _after_application(after_id), capacity + 1)
    page_bindings = bindings[:capacity]
    applications = [str(row["application_number"]) for row in page_bindings]
    cases = _case_rows(client, applications)
    results: list[dict[str, Any]] = []
    for binding in page_bindings:
        application = str(binding["application_number"])
        case = cases.get(application)
        if case is None:
            raise OwnerReadUnavailable("CN applicant binding has no current trademark case")
        results.append(_trademark_projection(
            application_number=application, binding=binding, case=case,
            applicant=applicant, source_version=source_version,
        ))
    after_epoch = _guard_epoch(serving_epoch_getter)
    if after_epoch != epoch:
        raise OwnerReadUnavailable("CN serving epoch changed during portfolio read")
    has_more = len(bindings) > capacity and emitted_before + len(results) < 500
    emitted = emitted_before + len(results)
    next_cursor = None
    if has_more and results:
        next_cursor = next_portfolio_cursor(
            query=query, source_version=source_version,
            last_candidate_id=results[-1]["trademark_candidate_id"],
            page_number=page_number, emitted_count=emitted,
        )
    observed_values = [source["observed_at"], *[item["source_reference"]["observed_at"] for item in results]]
    snapshot = source_snapshot(source_version, max_observed(observed_values))
    return OwnerReadResult("observed", page_payload(
        query=query, snapshot=snapshot, results=results, next_cursor=next_cursor,
        engine_version=engine_version(),
    ))


def _exact_binding(client: Any, entity_id: str, application_number: str) -> dict[str, Any] | None:
    roles = ",".join(_sql_text(role) for role in PARTY_ROLES)
    rows = _dict_rows(client.query(f"""
        SELECT application_number,
               arraySort(groupArray(concat(
                   role, '|', relation_key, '|', source_row_hash, '|', record_hash,
                   '|', toString(source_rank)
               ))) AS applicant_binding_hashes,
               max(ingested_at) AS applicant_observed_at
        FROM markorbit_facts.cn_case_party_current FINAL
        WHERE is_deleted = 0 AND is_current = 1
          AND role IN ({roles})
          AND entity_id = toUUID({_sql_text(entity_id)})
          AND application_number = {_sql_text(application_number)}
        GROUP BY application_number
        LIMIT 1
    """, settings=READ_SETTINGS))
    return rows[0] if rows else None


def _application_from_trademark_id(candidate_id: str) -> str:
    if not candidate_id.startswith(TRADEMARK_PREFIX):
        raise OwnerReadInvalid("CN trademark candidate id has the wrong prefix")
    application = candidate_id[len(TRADEMARK_PREFIX):].strip()
    if not application:
        raise OwnerReadInvalid("CN trademark candidate id is malformed")
    return application


def read_trademark_exact(
    *, client: Any, workspace_id: str, request_id: str,
    applicant_candidate_id: str, expected_applicant_source: Mapping[str, Any],
    trademark_candidate_id: str, expected_trademark_source: Mapping[str, Any],
    serving_epoch_getter: Any = _serving_epoch,
) -> OwnerReadResult:
    context = request_context(workspace_id, request_id)
    try:
        epoch, _candidate, applicant_source, _rows = _stable_epoch_read(
            client=client, applicant_candidate_id=applicant_candidate_id,
            expected_source=expected_applicant_source, serving_epoch_getter=serving_epoch_getter,
        )
    except KeyError:
        return OwnerReadResult("not_found", None)
    source_version = _epoch_version(epoch)
    applicant = applicant_reference(applicant_candidate_id, applicant_source)
    application = _application_from_trademark_id(trademark_candidate_id)
    binding = _exact_binding(client, _entity_id(applicant_candidate_id), application)
    case = _case_rows(client, [application]).get(application)
    after = _guard_epoch(serving_epoch_getter)
    if after != epoch:
        raise OwnerReadUnavailable("CN serving epoch changed during exact trademark read")
    if case is None:
        return OwnerReadResult("not_found", None)
    if binding is None:
        raise OwnerReadConflict("CN trademark is not currently bound to the supplied Applicant candidate")
    candidate = _trademark_projection(
        application_number=application, binding=binding, case=case,
        applicant=applicant, source_version=source_version,
    )
    if candidate["trademark_candidate_id"] != trademark_candidate_id:
        raise OwnerReadConflict("CN trademark candidate id does not resolve exactly")
    assert_exact_source_reference(expected_trademark_source, candidate["source_reference"])
    query = applicant_query(
        context=context, jurisdiction="CN", applicant_candidate_id=applicant_candidate_id,
        applicant_source=applicant_source, page_size=1,
    )
    snapshot = source_snapshot(
        source_version,
        max_observed([applicant_source["observed_at"], candidate["source_reference"]["observed_at"]]),
    )
    return OwnerReadResult("observed", page_payload(
        query=query, snapshot=snapshot, results=[candidate], next_cursor=None,
        engine_version=engine_version(),
    ))
