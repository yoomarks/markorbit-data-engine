from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.cn.applicant_owner_read as cn_owner
import app.us.applicant_owner_read as us_owner
from app.applicant_owner_read import OwnerReadConflict, OwnerReadInvalid, OwnerReadUnavailable
from app.us.applicant_candidate_backfill_control import USApplicantServingEpoch
from app.us.applicant_candidate_index import applicant_candidate_key


class FakeResult:
    def __init__(self, rows: list[dict]):
        self.column_names = list(rows[0].keys()) if rows else []
        self.result_rows = [tuple(row[name] for name in self.column_names) for row in rows]


class FakeClient:
    def __init__(self, responder):
        self.responder = responder
        self.queries: list[str] = []

    def query(self, sql: str, settings=None):
        self.queries.append(sql)
        return FakeResult(self.responder(sql))


def _ts(day: int) -> datetime:
    return datetime(2026, 9, day, 1, 2, 3, tzinfo=timezone.utc)


def _cn_party_row(application: str = "10001") -> dict:
    return {
        "application_number": application,
        "role": "OWNER",
        "relation_key": "a" * 64,
        "raw_name": "Example CN Co., Ltd.",
        "normalized_name": "example cn co ltd",
        "source_row_hash": "b" * 64,
        "record_hash": "c" * 64,
        "source_rank": 7,
        "ingested_at": _ts(1),
    }


def _cn_case_row(application: str = "10001") -> dict:
    return {
        "application_number": application,
        "mark_name_raw": "EXAMPLE",
        "classes": [9, 35],
        "source_row_hash": "d" * 64,
        "record_hash": "e" * 64,
        "source_rank": 8,
        "ingested_at": _ts(2),
    }


def _cn_epoch(token: str = "cn-epoch-1"):
    return SimpleNamespace(watermark=token)


def _cn_source(entity_id: str, rows: list[dict], epoch=None) -> dict:
    epoch = epoch or _cn_epoch()
    return cn_owner._candidate_projection(
        entity_id=entity_id,
        rows=rows,
        source_version=epoch.watermark,
    )[1]


CN_ENTITY_ID = "11111111-1111-1111-1111-111111111111"
CN_CANDIDATE_ID = f"cn:applicant:{CN_ENTITY_ID}"


def test_cn_exact_applicant_rejects_stale_source_fingerprint():
    rows = [_cn_party_row()]
    expected = _cn_source(CN_ENTITY_ID, rows)
    expected = {**expected, "source_fingerprint_sha256": "sha256:" + "0" * 64}
    client = FakeClient(lambda sql: rows if "cn_case_party_current" in sql else [])
    with pytest.raises(OwnerReadConflict):
        cn_owner.read_applicant_exact(
            client=client,
            workspace_id="ws-1",
            request_id="req-1",
            applicant_candidate_id=CN_CANDIDATE_ID,
            expected_source=expected,
            serving_epoch_getter=_cn_epoch,
        )


def test_cn_rejects_candidate_without_stable_entity_anchor():
    with pytest.raises(OwnerReadInvalid):
        cn_owner.read_applicant_exact(
            client=FakeClient(lambda _sql: []),
            workspace_id="ws-1",
            request_id="req-1",
            applicant_candidate_id="cn:applicant:name-hash-is-not-an-entity",
            expected_source={"bad": "shape"},
            serving_epoch_getter=_cn_epoch,
        )


def test_cn_epoch_drift_is_unavailable():
    rows = [_cn_party_row()]
    expected = _cn_source(CN_ENTITY_ID, rows)
    epochs = iter([_cn_epoch("cn-epoch-1"), _cn_epoch("cn-epoch-2")])
    with pytest.raises(OwnerReadUnavailable):
        cn_owner.read_applicant_exact(
            client=FakeClient(lambda sql: rows if "cn_case_party_current" in sql else []),
            workspace_id="ws-1",
            request_id="req-1",
            applicant_candidate_id=CN_CANDIDATE_ID,
            expected_source=expected,
            serving_epoch_getter=lambda: next(epochs),
        )


def test_cn_exact_trademark_rejects_wrong_applicant_binding():
    party_rows = [_cn_party_row()]
    expected_applicant = _cn_source(CN_ENTITY_ID, party_rows)
    case = _cn_case_row("20002")

    def respond(sql: str):
        if "cn_case_party_current" in sql and "GROUP BY application_number" not in sql:
            return party_rows
        if "cn_case_party_current" in sql and "GROUP BY application_number" in sql:
            return []
        if "cn_case_current" in sql:
            return [case]
        return []

    with pytest.raises(OwnerReadConflict):
        cn_owner.read_trademark_exact(
            client=FakeClient(respond),
            workspace_id="ws-1",
            request_id="req-1",
            applicant_candidate_id=CN_CANDIDATE_ID,
            expected_applicant_source=expected_applicant,
            trademark_candidate_id="cn:trademark:20002",
            expected_trademark_source={
                "owner": "MARKORBIT_DATA_ENGINE",
                "authority": "DATA_ENGINE_FACT_READ_MODEL",
                "jurisdiction": "CN",
                "source_kind": "TRADEMARK_RECORD",
                "source_id": "CN_TRADEMARK:20002",
                "source_version": "cn-epoch-1",
                "source_fingerprint_sha256": "sha256:" + "1" * 64,
                "observed_at": "2026-09-02T01:02:03Z",
            },
            serving_epoch_getter=_cn_epoch,
        )


def _us_owner_row(serial: str = "90000001", *, address: str = "1 Main St") -> dict:
    row = {
        "owner_key": "f" * 64,
        "serial_number": serial,
        "entry_number": 1,
        "party_type": "10",
        "legal_entity_type_code": "03",
        "entity_statement": "",
        "party_name": "Example Holdings LLC",
        "party_name_norm": "example holdings llc",
        "nationality_country": "US",
        "nationality_state": "DE",
        "nationality_other": "",
        "address_1": address,
        "address_2": "",
        "city": "Wilmington",
        "state": "DE",
        "country": "US",
        "postcode": "19801",
        "dba_aka_text": "",
        "composed_of_statement": "",
        "source_row_hash": "1" * 64,
        "record_hash": "2" * 64,
        "source_rank": 99,
        "ingested_at": _ts(3),
        "is_deleted": 0,
    }
    row["candidate_key"] = applicant_candidate_key(row)
    return row


def _us_case_row(serial: str = "90000001") -> dict:
    return {
        "serial_number": serial,
        "registration_number": "7000001",
        "mark_identification": "EXAMPLE",
        "source_row_hash": "3" * 64,
        "record_hash": "4" * 64,
        "source_rank": 100,
        "ingested_at": _ts(4),
        "is_deleted": 0,
    }


def _us_class_row(serial: str = "90000001") -> dict:
    return {
        "serial_number": serial,
        "classification_key": "5" * 64,
        "primary_code": "009",
        "international_codes": ["009", "035"],
        "source_row_hash": "6" * 64,
        "record_hash": "7" * 64,
        "source_rank": 101,
        "ingested_at": _ts(5),
    }


def _us_epoch() -> USApplicantServingEpoch:
    return USApplicantServingEpoch(
        bulk_run_id="run-complete",
        plan_sha256="a" * 64,
        checkpoint_sequence=310,
        final_audit_version="audit-v1",
    )


def _patch_us_epoch(monkeypatch, values=None):
    if values is None:
        monkeypatch.setattr(us_owner, "current_us_applicant_serving_epoch", _us_epoch)
    else:
        iterator = iter(values)
        monkeypatch.setattr(us_owner, "current_us_applicant_serving_epoch", lambda: next(iterator))
    monkeypatch.setattr(us_owner, "applicant_index_ready_for_epoch", lambda _epoch: True)


def test_us_same_name_different_address_stays_distinct_review_candidate():
    first = _us_owner_row(address="1 Main St")
    second = _us_owner_row(address="2 Main St")
    assert first["party_name_norm"] == second["party_name_norm"]
    assert first["candidate_key"] != second["candidate_key"]


def test_us_exact_applicant_rejects_stale_source_fingerprint(monkeypatch):
    _patch_us_epoch(monkeypatch)
    rows = [_us_owner_row()]
    candidate_id = f"us:applicant:{rows[0]['candidate_key']}"
    expected = us_owner._applicant_source(rows[0]["candidate_key"], rows, _us_epoch())
    expected = {**expected, "source_fingerprint_sha256": "sha256:" + "0" * 64}
    client = FakeClient(lambda sql: rows if "us_applicant_candidate_current" in sql else [])
    with pytest.raises(OwnerReadConflict):
        us_owner.revalidate_applicant(
            client,
            workspace_id="ws-1",
            request_id="req-1",
            candidate_id=candidate_id,
            expected_source=expected,
        )


def test_us_index_not_ready_is_unavailable(monkeypatch):
    monkeypatch.setattr(us_owner, "current_us_applicant_serving_epoch", _us_epoch)
    monkeypatch.setattr(us_owner, "applicant_index_ready_for_epoch", lambda _epoch: False)
    with pytest.raises(OwnerReadUnavailable):
        us_owner.revalidate_applicant(
            FakeClient(lambda _sql: []),
            workspace_id="ws-1",
            request_id="req-1",
            candidate_id="us:applicant:" + "a" * 64,
            expected_source={"bad": "shape"},
        )


def test_us_epoch_drift_is_unavailable(monkeypatch):
    first = _us_epoch()
    second = USApplicantServingEpoch(
        bulk_run_id="run-new",
        plan_sha256="b" * 64,
        checkpoint_sequence=310,
        final_audit_version="audit-v2",
    )
    _patch_us_epoch(monkeypatch, [first, second])
    rows = [_us_owner_row()]
    candidate_id = f"us:applicant:{rows[0]['candidate_key']}"
    expected = us_owner._applicant_source(rows[0]["candidate_key"], rows, first)
    with pytest.raises(OwnerReadUnavailable):
        us_owner.revalidate_applicant(
            FakeClient(lambda sql: rows if "us_applicant_candidate_current" in sql else []),
            workspace_id="ws-1",
            request_id="req-1",
            candidate_id=candidate_id,
            expected_source=expected,
        )


def test_us_portfolio_projects_exact_applicant_lineage(monkeypatch):
    _patch_us_epoch(monkeypatch)
    owner = _us_owner_row()
    rows = [owner]
    case = _us_case_row()
    classification = _us_class_row()
    expected = us_owner._applicant_source(owner["candidate_key"], rows, _us_epoch())
    candidate_id = f"us:applicant:{owner['candidate_key']}"

    def respond(sql: str):
        if "us_applicant_candidate_current" in sql:
            return rows
        if "us_case_current" in sql:
            return [case]
        if "us_classification_current" in sql:
            return [classification]
        return []

    result = us_owner.read_portfolio(
        FakeClient(respond),
        workspace_id="ws-1",
        request_id="req-1",
        candidate_id=candidate_id,
        expected_source=expected,
        page_size=50,
    )
    assert result.fact_state == "observed"
    assert result.payload is not None
    assert result.payload["query"]["request_context"] == {
        "requester_workspace_id": "ws-1",
        "request_id": "req-1",
    }
    assert result.payload["results"][0]["applicant"]["applicant_candidate_id"] == candidate_id
    assert result.payload["results"][0]["classes"] == [9, 35]


def test_us_exact_trademark_rejects_wrong_applicant_binding(monkeypatch):
    _patch_us_epoch(monkeypatch)
    owner = _us_owner_row("90000001", address="1 Main St")
    rows = [owner]
    expected_applicant = us_owner._applicant_source(owner["candidate_key"], rows, _us_epoch())
    candidate_id = f"us:applicant:{owner['candidate_key']}"
    wrong_owner = _us_owner_row("90000002", address="2 Main St")
    case = _us_case_row("90000002")
    classification = _us_class_row("90000002")

    def respond(sql: str):
        if "us_applicant_candidate_current" in sql:
            return rows
        if "us_case_current" in sql:
            return [case]
        if "us_classification_current" in sql:
            return [classification]
        if "us_owner_current" in sql:
            return [wrong_owner]
        return []

    with pytest.raises(OwnerReadConflict):
        us_owner.revalidate_trademark(
            FakeClient(respond),
            workspace_id="ws-1",
            request_id="req-1",
            applicant_candidate_id=candidate_id,
            expected_applicant_source=expected_applicant,
            trademark_candidate_id="us:trademark:90000002",
            expected_trademark_source={
                "owner": "MARKORBIT_DATA_ENGINE",
                "authority": "DATA_ENGINE_FACT_READ_MODEL",
                "jurisdiction": "US",
                "source_kind": "TRADEMARK_RECORD",
                "source_id": "US_TRADEMARK:90000002",
                "source_version": "us-serving-epoch:" + _us_epoch().token,
                "source_fingerprint_sha256": "sha256:" + "8" * 64,
                "observed_at": "2026-09-05T01:02:03Z",
            },
        )
