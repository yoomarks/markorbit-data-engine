from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.applicant_owner_read import (
    APPLICANT_CANDIDATE_TYPE, TRADEMARK_CANDIDATE_TYPE,
    OwnerReadConflict, OwnerReadInvalid, OwnerReadUnavailable,
    applicant_query, applicant_reference, applicant_revalidation_query,
    assert_exact_source_reference, max_observed, next_portfolio_cursor,
    page_payload, portfolio_cursor_state, request_context, sha256_ref,
    source_reference, source_snapshot,
)
from app.us.applicant_candidate_backfill_control import (
    applicant_index_ready_for_epoch,
    current_us_applicant_serving_epoch,
)
from app.us.applicant_candidate_index import (
    APPLICANT_INDEX_TABLE, APPLICANT_CANDIDATE_PREFIX, APPLICANT_SOURCE_PREFIX,
    applicant_candidate_key,
)
from app.version import engine_version

TRADEMARK_PREFIX = "us:trademark:"
TRADEMARK_SOURCE_PREFIX = "US_TRADEMARK:"
READ_SETTINGS = {"max_threads": 1, "max_rows_to_read": 1_000_000, "read_overflow_mode": "throw"}

@dataclass(frozen=True, slots=True)
class OwnerReadResult:
    fact_state: str
    payload: dict[str, Any] | None


def _sql_text(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _dict_rows(result: Any) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def _candidate_key(candidate_id: str) -> str:
    if not candidate_id.startswith(APPLICANT_CANDIDATE_PREFIX):
        raise OwnerReadInvalid("US applicant candidate id has the wrong prefix")
    key = candidate_id[len(APPLICANT_CANDIDATE_PREFIX):]
    if len(key) != 64 or any(ch not in "0123456789abcdef" for ch in key):
        raise OwnerReadInvalid("US applicant candidate id is malformed")
    return key


def _source_id(candidate_key: str) -> str:
    return f"{APPLICANT_SOURCE_PREFIX}{candidate_key}"


def _epoch_version(epoch: Any) -> str:
    return f"us-serving-epoch:{epoch.token}"


def _guard_epoch() -> Any:
    try:
        epoch = current_us_applicant_serving_epoch()
    except RuntimeError as exc:
        raise OwnerReadUnavailable(str(exc)) from exc
    if not applicant_index_ready_for_epoch(epoch):
        raise OwnerReadUnavailable(
            "US Applicant candidate index is not complete for the durable serving epoch"
        )
    return epoch


def _assert_same_epoch(before: Any) -> None:
    after = _guard_epoch()
    if after != before:
        raise OwnerReadUnavailable("US serving epoch changed during owner read")


def _candidate_rows(client: Any, candidate_key: str) -> list[dict[str, Any]]:
    result = client.query(
        f"""
        SELECT *
        FROM {APPLICANT_INDEX_TABLE} FINAL
        WHERE candidate_key = {_sql_text(candidate_key)}
          AND is_deleted = 0
        ORDER BY serial_number ASC, owner_key ASC
        LIMIT 1000001
        """,
        settings=READ_SETTINGS,
    )
    rows = _dict_rows(result)
    if len(rows) > 1_000_000:
        raise OwnerReadUnavailable("US Applicant candidate exceeds bounded read ceiling")
    return rows


def _applicant_material(candidate_key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise OwnerReadInvalid("US Applicant candidate has no current owner rows")
    names = sorted({str(row.get("party_name") or "").strip() for row in rows if str(row.get("party_name") or "").strip()})
    display_name = names[0] if names else str(rows[0].get("party_name_norm") or "").strip()
    if not display_name:
        raise OwnerReadUnavailable("US Applicant candidate has no displayable owner name")
    bindings = [
        {
            "serial_number": str(row.get("serial_number") or ""),
            "owner_key": str(row.get("owner_key") or ""),
            "record_hash": str(row.get("record_hash") or ""),
            "source_row_hash": str(row.get("source_row_hash") or ""),
        }
        for row in rows
    ]
    return {
        "candidate_key": candidate_key,
        "display_name": display_name,
        "alternate_names": [name for name in names if name != display_name][:20],
        "bindings": bindings,
    }


def _applicant_source(candidate_key: str, rows: list[dict[str, Any]], epoch: Any) -> dict[str, str]:
    material = _applicant_material(candidate_key, rows)
    return source_reference(
        jurisdiction="US",
        source_kind="APPLICANT_IDENTITY",
        source_id=_source_id(candidate_key),
        source_version=_epoch_version(epoch),
        fingerprint=sha256_ref(material),
        observed_at=max_observed([row.get("ingested_at") for row in rows]),
    )


def _applicant_candidate(candidate_id: str, source: Mapping[str, Any], material: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_type": APPLICANT_CANDIDATE_TYPE,
        "applicant_candidate_id": candidate_id,
        "display_name": str(material["display_name"]),
        "alternate_names": list(material["alternate_names"]),
        "source_reference": dict(source),
        "match_kind": "EXACT_SOURCE_REFERENCE",
        "review_required": True,
        "verified_legal_identity": False,
        "customer_relationship_established": False,
    }


def _validated_applicant(
    client: Any,
    *,
    candidate_id: str,
    expected_source: Mapping[str, Any],
    epoch: Any,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    candidate_key = _candidate_key(candidate_id)
    rows = _candidate_rows(client, candidate_key)
    if not rows:
        return {}, {}, []
    actual_source = _applicant_source(candidate_key, rows, epoch)
    if actual_source["source_id"] != _source_id(candidate_key):
        raise OwnerReadConflict("US Applicant candidate/source identity mismatch")
    assert_exact_source_reference(expected_source, actual_source)
    material = _applicant_material(candidate_key, rows)
    return _applicant_candidate(candidate_id, actual_source, material), actual_source, rows


def revalidate_applicant(
    client: Any,
    *,
    workspace_id: str,
    request_id: str,
    candidate_id: str,
    expected_source: Mapping[str, Any],
) -> OwnerReadResult:
    context = request_context(workspace_id, request_id)
    before = _guard_epoch()
    candidate, actual_source, rows = _validated_applicant(
        client, candidate_id=candidate_id, expected_source=expected_source, epoch=before
    )
    if not rows:
        _assert_same_epoch(before)
        return OwnerReadResult("not_found", None)
    query = applicant_revalidation_query(
        context=context, jurisdiction="US", applicant_candidate_id=candidate_id, page_size=1
    )
    snapshot = source_snapshot(_epoch_version(before), actual_source["observed_at"])
    payload = page_payload(
        query=query,
        snapshot=snapshot,
        results=[candidate],
        next_cursor=None,
        engine_version=engine_version(),
    )
    _assert_same_epoch(before)
    return OwnerReadResult("observed", payload)


def _trademark_candidate_id(serial_number: str) -> str:
    serial = str(serial_number or "").strip()
    if not serial:
        raise OwnerReadInvalid("US trademark serial number is required")
    return f"{TRADEMARK_PREFIX}{serial}"


def _serial_from_trademark_id(candidate_id: str) -> str:
    value = str(candidate_id or "").strip()
    if not value.startswith(TRADEMARK_PREFIX):
        raise OwnerReadInvalid("US trademark candidate id has the wrong prefix")
    serial = value[len(TRADEMARK_PREFIX):]
    if not serial or len(serial) > 64:
        raise OwnerReadInvalid("US trademark candidate id is malformed")
    return serial


def _class_numbers(rows: list[dict[str, Any]]) -> list[int]:
    values: set[int] = set()
    for row in rows:
        raw = [row.get("primary_code"), *(row.get("international_codes") or [])]
        for item in raw:
            text = str(item or "").strip()
            if text.isdigit():
                number = int(text)
                if 1 <= number <= 45:
                    values.add(number)
    return sorted(values)


def _trademark_rows(client: Any, serials: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    if not serials:
        return {}, {}
    clause = ",".join(_sql_text(serial) for serial in serials)
    case_result = client.query(
        f"""
        SELECT serial_number, registration_number, mark_identification,
               source_row_hash, record_hash, source_rank, ingested_at, is_deleted
        FROM markorbit_facts.us_case_current FINAL
        WHERE serial_number IN ({clause})
        """,
        settings=READ_SETTINGS,
    )
    cases = {str(row["serial_number"]): row for row in _dict_rows(case_result) if not int(row.get("is_deleted") or 0)}
    class_result = client.query(
        f"""
        SELECT serial_number, classification_key, primary_code, international_codes,
               source_row_hash, record_hash, source_rank, ingested_at
        FROM markorbit_facts.us_classification_current FINAL
        WHERE serial_number IN ({clause}) AND is_deleted = 0
        ORDER BY serial_number ASC, classification_key ASC
        """,
        settings=READ_SETTINGS,
    )
    classes: dict[str, list[dict[str, Any]]] = {}
    for row in _dict_rows(class_result):
        classes.setdefault(str(row["serial_number"]), []).append(row)
    return cases, classes


def _trademark_source(
    *,
    serial: str,
    case_row: Mapping[str, Any],
    class_rows: list[dict[str, Any]],
    binding_rows: list[dict[str, Any]],
    epoch: Any,
) -> dict[str, str]:
    material = {
        "serial_number": serial,
        "case": {
            "record_hash": str(case_row.get("record_hash") or ""),
            "source_row_hash": str(case_row.get("source_row_hash") or ""),
        },
        "applicant_binding": [
            {
                "owner_key": str(row.get("owner_key") or ""),
                "record_hash": str(row.get("record_hash") or ""),
                "source_row_hash": str(row.get("source_row_hash") or ""),
            }
            for row in sorted(binding_rows, key=lambda item: str(item.get("owner_key") or ""))
        ],
        "classifications": [
            {
                "classification_key": str(row.get("classification_key") or ""),
                "record_hash": str(row.get("record_hash") or ""),
                "source_row_hash": str(row.get("source_row_hash") or ""),
            }
            for row in class_rows
        ],
    }
    observed = [case_row.get("ingested_at"), *[r.get("ingested_at") for r in class_rows], *[r.get("ingested_at") for r in binding_rows]]
    return source_reference(
        jurisdiction="US",
        source_kind="TRADEMARK_RECORD",
        source_id=f"{TRADEMARK_SOURCE_PREFIX}{serial}",
        source_version=_epoch_version(epoch),
        fingerprint=sha256_ref(material),
        observed_at=max_observed(observed),
    )


def _trademark_candidate(
    *,
    serial: str,
    applicant_ref: Mapping[str, Any],
    case_row: Mapping[str, Any],
    class_rows: list[dict[str, Any]],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    registration = str(case_row.get("registration_number") or "").strip() or None
    mark_text = str(case_row.get("mark_identification") or "").strip() or None
    return {
        "candidate_type": TRADEMARK_CANDIDATE_TYPE,
        "trademark_candidate_id": _trademark_candidate_id(serial),
        "applicant": dict(applicant_ref),
        "jurisdiction": "US",
        "mark_text": mark_text,
        "application_number": serial,
        "registration_number": registration,
        "classes": _class_numbers(class_rows),
        "source_reference": dict(source),
        "official_truth_verified": False,
        "legal_conclusion_created": False,
        "workspace_relationship_established": False,
    }


def _portfolio_serials(
    rows: list[dict[str, Any]], *, after_candidate_id: str, page_size: int, emitted_before: int
) -> tuple[list[str], bool]:
    after_serial = _serial_from_trademark_id(after_candidate_id) if after_candidate_id else ""
    serials = sorted({str(row.get("serial_number") or "") for row in rows if str(row.get("serial_number") or "") > after_serial})
    remaining = max(500 - emitted_before, 0)
    capacity = min(page_size, remaining)
    selected = serials[:capacity]
    return selected, len(serials) > capacity


def read_portfolio(
    client: Any,
    *,
    workspace_id: str,
    request_id: str,
    candidate_id: str,
    expected_source: Mapping[str, Any],
    page_size: int = 50,
    cursor: str | None = None,
) -> OwnerReadResult:
    context = request_context(workspace_id, request_id)
    before = _guard_epoch()
    _, applicant_source, owner_rows = _validated_applicant(
        client, candidate_id=candidate_id, expected_source=expected_source, epoch=before
    )
    if not owner_rows:
        _assert_same_epoch(before)
        return OwnerReadResult("not_found", None)
    query = applicant_query(
        context=context, jurisdiction="US", applicant_candidate_id=candidate_id,
        applicant_source=applicant_source, page_size=page_size,
    )
    after_id, page_number, emitted_before = portfolio_cursor_state(
        token=cursor, query=query, source_version=_epoch_version(before)
    )
    serials, has_extra = _portfolio_serials(
        owner_rows, after_candidate_id=after_id, page_size=page_size, emitted_before=emitted_before
    )
    cases, classes = _trademark_rows(client, serials)
    applicant_ref = applicant_reference(candidate_id, applicant_source)
    by_serial: dict[str, list[dict[str, Any]]] = {}
    for row in owner_rows:
        by_serial.setdefault(str(row.get("serial_number") or ""), []).append(row)
    results: list[dict[str, Any]] = []
    observed_values: list[Any] = [applicant_source["observed_at"]]
    for serial in serials:
        case_row = cases.get(serial)
        if case_row is None:
            raise OwnerReadUnavailable(
                f"US Applicant index references serial {serial} without a current case row"
            )
        class_rows = classes.get(serial, [])
        binding_rows = by_serial.get(serial, [])
        source = _trademark_source(
            serial=serial,
            case_row=case_row,
            class_rows=class_rows,
            binding_rows=binding_rows,
            epoch=before,
        )
        observed_values.append(source["observed_at"])
        results.append(
            _trademark_candidate(
                serial=serial,
                applicant_ref=applicant_ref,
                case_row=case_row,
                class_rows=class_rows,
                source=source,
            )
        )
    emitted = emitted_before + len(results)
    next_cursor = None
    if has_extra and results and emitted < 500:
        next_cursor = next_portfolio_cursor(
            query=query,
            source_version=_epoch_version(before),
            last_candidate_id=str(results[-1]["trademark_candidate_id"]),
            page_number=page_number,
            emitted_count=emitted,
        )
    snapshot = source_snapshot(_epoch_version(before), max_observed(observed_values))
    payload = page_payload(
        query=query,
        snapshot=snapshot,
        results=results,
        next_cursor=next_cursor,
        engine_version=engine_version(),
    )
    _assert_same_epoch(before)
    return OwnerReadResult("observed", payload)


def _serial_candidate_keys(client: Any, serial: str) -> set[str]:
    result = client.query(
        f"""
        SELECT *
        FROM markorbit_facts.us_owner_current FINAL
        WHERE serial_number = {_sql_text(serial)} AND is_deleted = 0
        ORDER BY owner_key ASC
        LIMIT 10001
        """,
        settings=READ_SETTINGS,
    )
    rows = _dict_rows(result)
    if len(rows) > 10_000:
        raise OwnerReadUnavailable("US trademark owner binding exceeds bounded read ceiling")
    return {applicant_candidate_key(row) for row in rows}


def revalidate_trademark(
    client: Any,
    *,
    workspace_id: str,
    request_id: str,
    applicant_candidate_id: str,
    expected_applicant_source: Mapping[str, Any],
    trademark_candidate_id: str,
    expected_trademark_source: Mapping[str, Any],
) -> OwnerReadResult:
    context = request_context(workspace_id, request_id)
    before = _guard_epoch()
    _, applicant_source, owner_rows = _validated_applicant(
        client,
        candidate_id=applicant_candidate_id,
        expected_source=expected_applicant_source,
        epoch=before,
    )
    if not owner_rows:
        _assert_same_epoch(before)
        return OwnerReadResult("not_found", None)
    serial = _serial_from_trademark_id(trademark_candidate_id)
    cases, classes = _trademark_rows(client, [serial])
    case_row = cases.get(serial)
    if case_row is None:
        _assert_same_epoch(before)
        return OwnerReadResult("not_found", None)
    candidate_key = _candidate_key(applicant_candidate_id)
    binding_rows = [row for row in owner_rows if str(row.get("serial_number") or "") == serial]
    if not binding_rows:
        actual_keys = _serial_candidate_keys(client, serial)
        if actual_keys:
            raise OwnerReadConflict("US trademark is not currently bound to the supplied Applicant candidate")
        raise OwnerReadConflict("US trademark has no current Applicant binding")
    if candidate_key not in _serial_candidate_keys(client, serial):
        raise OwnerReadConflict("US trademark Applicant binding is stale or conflicting")
    trademark_source = _trademark_source(
        serial=serial,
        case_row=case_row,
        class_rows=classes.get(serial, []),
        binding_rows=binding_rows,
        epoch=before,
    )
    assert_exact_source_reference(expected_trademark_source, trademark_source)
    applicant_ref = applicant_reference(applicant_candidate_id, applicant_source)
    result = _trademark_candidate(
        serial=serial,
        applicant_ref=applicant_ref,
        case_row=case_row,
        class_rows=classes.get(serial, []),
        source=trademark_source,
    )
    query = applicant_query(
        context=context,
        jurisdiction="US",
        applicant_candidate_id=applicant_candidate_id,
        applicant_source=applicant_source,
        page_size=1,
    )
    snapshot = source_snapshot(
        _epoch_version(before),
        max_observed([applicant_source["observed_at"], trademark_source["observed_at"]]),
    )
    payload = page_payload(
        query=query,
        snapshot=snapshot,
        results=[result],
        next_cursor=None,
        engine_version=engine_version(),
    )
    _assert_same_epoch(before)
    return OwnerReadResult("observed", payload)
