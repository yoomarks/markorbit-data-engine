from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.global_trademarks.hot_global_admission import (
    CONTRACT_VERSION,
    FULL_BASELINE_CONTRACT_VERSION,
    DDL,
    MAPPING_VERSION,
    SOURCE,
    TABLE,
    HotGlobalAdmissionError,
    admit,
    install_hot_global_schema,
    normalize,
    require_hot_global_ready,
)


@dataclass
class Result:
    result_rows: list


class HotGlobalClient:
    def __init__(self, *, ready=True, full_ready=False):
        self.ready = ready
        self.full_ready = full_ready
        self.rows: dict[tuple[str, str], str] = {}
        self.insert_calls: list = []
        self.commands: list[str] = []

    def query(self, sql, parameters=None):
        if "FROM system.disks" in sql:
            return Result([("hot_global", 1000, 500)] if self.ready else [])
        if "FROM system.storage_policies" in sql:
            return Result([("hot_global_only", "main", ["hot_global"])] if self.ready else [])
        if "FROM system.columns" in sql and "page_index" in sql:
            return Result([("UInt16" if self.full_ready else "UInt8",)])
        if sql.startswith("SHOW CREATE TABLE"):
            return Result([(DDL,)] if self.ready else [])
        if "SELECT source_record_id, record_sha256" in sql:
            return Result(
                [
                    (identity, sha)
                    for (source_sha, identity), sha in self.rows.items()
                    if source_sha == parameters["response_sha"]
                ]
            )
        raise AssertionError("Unexpected ClickHouse query " + sql[:100])

    def insert(self, table, rows, column_names):
        assert table == TABLE
        assert "source_record_id" in column_names
        self.insert_calls.append((table, rows, column_names))
        for row in rows:
            data = dict(zip(column_names, row, strict=True))
            self.rows[(data["source_response_sha256"], data["source_record_id"])] = data[
                "record_sha256"
            ]

    def command(self, sql):
        self.commands.append(sql)


def package(*, page=1, kind="LIST_PAGE"):
    ids = ["LA" + str(54000 + i + (page - 1) * 100) for i in range(50)]
    records = [
        {
            "source_record_id": identity,
            "nice_classes": [],
            "application_number": None,
            "registration_number": None,
        }
        for identity in ids
    ]
    if kind == "DETAIL":
        records = [
            {
                "source_record_id": "LA55159",
                "application_number": None,
                "registration_number": None,
                "mark_text": "GF",
                "source_status_raw": "Filed",
                "normalized_status": None,
                "applicant_name": "Lao Applicant",
                "nice_classes": [30],
                "filing_date": "2026-09-10",
                "logo_url": "https://online.dip.gov.la/wopublish-search/service/trademarks/application/LA55159/logo?noLogo=true",
                "source_language": "lo",
                "source_native_fields": {"source_status": "Filed"},
            }
        ]
    return {
        "contract_version": CONTRACT_VERSION,
        "mapping_version": MAPPING_VERSION,
        "source_owner": "MARKORBIT_KNOWLEDGE",
        "jurisdiction": "LA",
        "source_id": SOURCE,
        "observation_kind": kind,
        "page_index": 0 if kind == "DETAIL" else page,
        "source_uri": (
            "https://online.dip.gov.la/wopublish-search/public/detail/trademarks?id=LA55159"
            if kind == "DETAIL"
            else "https://online.dip.gov.la/wopublish-search/public/trademarks?0"
        ),
        "evidence_canonical_uri": (
            "la-dipo://wopublish/trademarks/detail/LA55159/redacted-response"
            if kind == "DETAIL"
            else "la-dipo://wopublish/trademarks/list/page/" + str(page) + "/redacted-response"
        ),
        "evidence_sha256": "a" * 64,
        "source_response_sha256": "b" * 63 + str(page if kind == "LIST_PAGE" else 3),
        "observed_at": "2026-09-24T12:00:00Z",
        "records": records,
    }


def test_generic_normalized_fields_distinguish_source_application_and_registration():
    normalized = normalize(package(kind="DETAIL"))
    item = normalized.records[0]
    assert item["source_record_id"] == "LA55159"
    assert item["application_number"] is None
    assert item["registration_number"] is None
    assert item["source_status_raw"] == "Filed"
    assert item["normalized_status"] is None
    assert item["nice_classes"] == [30]
    assert str(item["filing_date"]) == "2026-09-10"
    assert normalized.observed_at.tzinfo is not None


@pytest.mark.parametrize(
    "change",
    [
        {"jurisdiction": "CN"},
        {"source_owner": "MARKORBIT_DATA_ENGINE"},
        {"observation_kind": "BULK"},
        {"page_index": 3},
        {"evidence_canonical_uri": "https://untrusted.example/data"},
        {
            "source_uri": "https://online.dip.gov.la/wopublish-search/public/trademarks;jsessionid=SECRET?0"
        },
        {"source_response_sha256": "nope"},
    ],
)
def test_rejects_unauthorized_or_unbounded_source(change):
    with pytest.raises(HotGlobalAdmissionError):
        normalize({**package(), **change})


def test_rejects_deduced_registration_and_unverified_normalized_legal_status():
    value = package(kind="DETAIL")
    value["records"][0]["registration_number"] = "LA55159"
    with pytest.raises(HotGlobalAdmissionError, match="cannot be inferred"):
        normalize(value)
    value = package(kind="DETAIL")
    value["records"][0]["normalized_status"] = "REGISTERED"
    with pytest.raises(HotGlobalAdmissionError, match="unverified"):
        normalize(value)


def test_rejects_duplicate_records_sensitive_native_fields_and_more_than_two_pages():
    value = package()
    value["records"][1] = value["records"][0].copy()
    with pytest.raises(HotGlobalAdmissionError, match="duplicate"):
        normalize(value)
    value = package(kind="DETAIL")
    value["records"][0]["source_native_fields"] = {"JSESSIONID": "secret"}
    with pytest.raises(HotGlobalAdmissionError, match="authentication"):
        normalize(value)
    value = package(page=3)
    with pytest.raises(HotGlobalAdmissionError, match="page_index"):
        normalize(value)


def test_100_unique_pilot_ids_and_detail_are_idempotent_hot_global_observations():
    client = HotGlobalClient()
    first = admit(package(page=1), client=client)
    second = admit(package(page=2), client=client)
    detail = admit(package(kind="DETAIL"), client=client)
    assert [first["record_count"], second["record_count"], detail["record_count"]] == [50, 50, 1]
    assert len(client.rows) == 101
    assert all(item["storage_placement"] == "hot_global" for item in (first, second, detail))
    assert all(item["current_state_verified"] is False for item in (first, second, detail))
    assert admit(package(page=1), client=client)["replayed"] is True
    assert len(client.insert_calls) == 3
    assert "storage_policy = 'hot_global_only'" in DDL


def test_identical_source_evidence_with_conflicting_mapping_fails_closed():
    client = HotGlobalClient()
    admit(package(page=1), client=client)
    conflicted = package(page=1)
    conflicted["records"][0]["source_native_fields"] = {"different": True}
    with pytest.raises(HotGlobalAdmissionError, match="conflicting mapped facts"):
        admit(conflicted, client=client)
    assert len(client.insert_calls) == 1


def test_partial_insert_recovery_only_inserts_missing_identities():
    client = HotGlobalClient()
    admit(package(page=1), client=client)
    first_row = next(iter(client.rows))
    client.rows.pop(first_row)
    repaired = admit(package(page=1), client=client)
    assert repaired["inserted_count"] == 1
    assert len(client.rows) == 50


def test_hot_global_not_ready_rejects_schema_install_and_admission():
    client = HotGlobalClient(ready=False)
    with pytest.raises(RuntimeError, match="hot_global"):
        require_hot_global_ready(client)
    with pytest.raises(RuntimeError, match="hot_global"):
        install_hot_global_schema(client)
    with pytest.raises(RuntimeError, match="hot_global"):
        admit(package(), client=client)
    assert client.commands == []
    assert client.insert_calls == []


def test_explicit_schema_install_only_with_target_hot_global_disk_policy():
    client = HotGlobalClient()
    install_hot_global_schema(client)
    assert client.commands == ["CREATE DATABASE IF NOT EXISTS markorbit_facts", DDL]


def full_page(*, page: int, total: int = 73531):
    value = package(page=page)
    value["contract_version"] = FULL_BASELINE_CONTRACT_VERSION
    value["observation_kind"] = "FULL_INDEX_PAGE"
    value["source_total"] = total
    value["source_response_sha256"] = f"{page:064x}"
    last_page_count = total - (page - 1) * 50
    value["records"] = value["records"][: min(50, max(0, last_page_count))]
    return value


def test_full_index_contract_accepts_actual_last_page_size_and_1471st_page():
    assert "page_index UInt16" in DDL
    assert normalize(full_page(page=1)).source_total == 73531
    last = normalize(full_page(page=1471))
    assert last.kind == "FULL_INDEX_PAGE"
    assert last.page == 1471
    assert len(last.records) == 31
    assert all(item["registration_number"] is None for item in last.records)


def test_full_index_gate_blocks_old_uint8_production_schema_without_writes():
    client = HotGlobalClient(full_ready=False)
    with pytest.raises(RuntimeError, match="UInt16"):
        admit(full_page(page=3), client=client)
    assert client.insert_calls == []
    assert client.rows == {}


def test_full_index_later_page_and_detail_replay_under_uint16_schema():
    client = HotGlobalClient(full_ready=True)
    page = full_page(page=1471)
    receipt = admit(page, client=client)
    assert receipt["contract_version"] == FULL_BASELINE_CONTRACT_VERSION
    assert receipt["record_count"] == 31
    assert receipt["source_total"] == 73531
    assert receipt["page_index"] == 1471
    assert receipt["current_state_verified"] is False
    assert admit(page, client=client)["replayed"] is True
    detail = package(kind="DETAIL")
    detail["contract_version"] = FULL_BASELINE_CONTRACT_VERSION
    detail["observation_kind"] = "FULL_DETAIL"
    assert admit(detail, client=client)["record_count"] == 1
    assert admit(detail, client=client)["replayed"] is True
    assert len(client.rows) == 32


@pytest.mark.parametrize(
    "changed",
    [
        {"source_total": 100},
        {"source_total": 100001},
        {"source_total": True},
        {"source_total": "73531"},
        {"page_index": 1472},
        {"page_index": 65536},
        {"page_index": -1},
        {"page_index": 0},
        {"observation_kind": "LIST_PAGE"},
    ],
)
def test_full_index_rejects_unbounded_or_legacy_pilot_scope(changed):
    with pytest.raises(HotGlobalAdmissionError):
        normalize({**full_page(page=1), **changed})


def test_full_index_rejects_missing_ids_and_declared_total_replay_drift():
    value = full_page(page=1471)
    value["records"] = value["records"][:-1]
    with pytest.raises(HotGlobalAdmissionError, match="record count"):
        normalize(value)
    client = HotGlobalClient(full_ready=True)
    original = full_page(page=3)
    admit(original, client=client)
    conflicted = full_page(page=3, total=73530)
    with pytest.raises(HotGlobalAdmissionError, match="conflicting mapped facts"):
        admit(conflicted, client=client)
    assert len(client.insert_calls) == 1


def test_full_detail_rejects_list_source_total_and_legal_number_guess():
    detail = package(kind="DETAIL")
    detail["contract_version"] = FULL_BASELINE_CONTRACT_VERSION
    detail["observation_kind"] = "FULL_DETAIL"
    with pytest.raises(HotGlobalAdmissionError, match="source_total"):
        normalize({**detail, "source_total": 73531})
    detail["records"][0]["registration_number"] = "LA55159"
    with pytest.raises(HotGlobalAdmissionError, match="cannot be inferred"):
        normalize(detail)


def test_full_index_api_default_off_even_when_signed_pilot_is_valid(monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.global_trademarks import hot_global_api

    monkeypatch.setattr(
        hot_global_api,
        "get_settings",
        lambda: SimpleNamespace(global_hot_full_baseline_enabled=False),
    )
    with pytest.raises(HTTPException) as exc:
        hot_global_api.admit_global_observations(full_page(page=3))
    assert exc.value.status_code == 400
    assert "disabled" in exc.value.detail["message"]
