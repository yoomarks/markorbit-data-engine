from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from app.cn.trademark_gazette_admission import (
    ADMISSION_CONTRACT_VERSION,
    ANNOUNCEMENT_TABLE,
    ISSUE_TABLE,
    GazetteAdmissionError,
    admit_cn_trademark_gazette_package,
    normalize_cn_trademark_gazette_package,
)


def package() -> dict:
    return {
        "contract_version": ADMISSION_CONTRACT_VERSION,
        "source_authority": "CNIPA",
        "completeness": "COMPLETE",
        "announcement_issue": 75,
        "announcement_date": "1983-08-15",
        "record_count": 2,
        "page_count": 1,
        "page_size": 100,
        "source_capture_schema": "mo-cnipa-gazette-small-complete-v1",
        "source_dataset_sha256": "a" * 64,
        "source_uri": "https://pub.sbj.cnipa.gov.cn/toas-pub-prod/pub-prod-api/public/web/anncInfo/searchEsTmgg",
        "collected_at": "2026-09-19T06:00:00.000Z",
        "records": [
            {
                "source_row_id": "0123456789abcdef0123456789abcdef",
                "source_search_id": "0123456789abcdef0123456789abcdef",
                "registration_number": "100001",
                "announcement_issue": 75,
                "announcement_type_code": "TMZCSQ",
                "announcement_type_name": "商标注册申请初步审定公告",
                "detail_page_no": 1,
                "announcement_page_count": 100,
                "detail_file_id": "file-1",
                "detail_asset_path": "/group/1983/75/page1.jpg",
                "announcement_detail_url": "",
            },
            {
                "source_row_id": "0123456789abcdef0123456789abcdee",
                "source_search_id": "0123456789abcdef0123456789abcdee",
                "registration_number": "100002",
                "announcement_issue": 75,
                "announcement_type_code": "TMZCSQ",
                "announcement_type_name": "商标注册申请初步审定公告",
                "detail_page_no": 1,
                "announcement_page_count": 100,
                "detail_file_id": "file-1",
                "detail_asset_path": "/group/1983/75/page1.jpg",
                "announcement_detail_url": "",
            },
        ],
    }


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class Client:
    def __init__(self):
        self.issue_rows: list[dict] = []
        self.announcement_rows: list[dict] = []
        self.inserts: list[tuple[str, list[list], list[str]]] = []

    def query(self, sql, *, parameters):
        if ISSUE_TABLE in sql:
            matches = [
                row
                for row in self.issue_rows
                if row["announcement_issue"] == parameters["issue"]
                and row["source_dataset_sha256"] == parameters["dataset_sha"]
            ]
            return Result([(row["record_count"],) for row in matches[:1]])
        if ANNOUNCEMENT_TABLE in sql:
            count = sum(
                1
                for row in self.announcement_rows
                if row["announcement_issue"] == parameters["issue"]
                and row["source_dataset_sha256"] == parameters["dataset_sha"]
            )
            return Result([(count,)])
        raise AssertionError(sql)

    def insert(self, table, rows, *, column_names):
        self.inserts.append((table, rows, column_names))
        target = self.issue_rows if table == ISSUE_TABLE else self.announcement_rows
        for values in rows:
            target.append(dict(zip(column_names, values, strict=True)))


def test_normalizes_minimal_complete_package():
    result = normalize_cn_trademark_gazette_package(package())

    assert result.announcement_issue == 75
    assert result.announcement_date == date(1983, 8, 15)
    assert result.record_count == 2
    assert result.page_count == 1
    assert result.page_size == 100
    assert result.collected_at == datetime(2026, 9, 19, 6, 0, tzinfo=timezone.utc)
    assert result.records[0].registration_number == "100001"


def test_admits_issue_and_minimal_announcement_rows_then_replays_exact_dataset():
    client = Client()
    value = package()

    first = admit_cn_trademark_gazette_package(value, client=client)
    replay = admit_cn_trademark_gazette_package(value, client=client)

    assert first.replayed is False
    assert replay.replayed is True
    assert first.announcement_issue == 75
    assert len(client.issue_rows) == 1
    assert len(client.announcement_rows) == 2

    issue = client.issue_rows[0]
    assert issue["capture_completeness"] == "COMPLETE"
    assert issue["record_count"] == 2

    row = client.announcement_rows[0]
    assert row["registration_number"] == "100001"
    assert row["detail_resolution_status"] == "SOURCE_LOCATOR_OBSERVED"
    assert "applicant_name" not in row
    assert "trademark_name" not in row
    assert "nice_class" not in row
    assert "application_date" not in row


def test_rejects_non_complete_package_before_persistence():
    client = Client()
    value = package()
    value["completeness"] = "INCOMPLETE"

    with pytest.raises(GazetteAdmissionError, match="only COMPLETE"):
        admit_cn_trademark_gazette_package(value, client=client)
    assert client.inserts == []


def test_rejects_record_count_and_page_count_inconsistency():
    value = package()
    value["record_count"] = 101
    value["records"] = value["records"] * 51
    value["records"] = value["records"][:101]
    for index, row in enumerate(value["records"]):
        row = deepcopy(row)
        row["source_row_id"] = f"id-{index}"
        row["source_search_id"] = f"id-{index}"
        value["records"][index] = row

    with pytest.raises(
        GazetteAdmissionError,
        match="record_count exceeds page_count \\* page_size|page_count is inconsistent",
    ):
        normalize_cn_trademark_gazette_package(value)


def test_rejects_duplicate_or_mismatched_official_ids():
    value = package()
    value["records"][1]["source_row_id"] = value["records"][0]["source_row_id"]
    value["records"][1]["source_search_id"] = value["records"][0]["source_search_id"]
    with pytest.raises(GazetteAdmissionError, match="duplicate source_row_id"):
        normalize_cn_trademark_gazette_package(value)

    value = package()
    value["records"][0]["source_search_id"] = "different"
    with pytest.raises(GazetteAdmissionError, match="must match source_row_id"):
        normalize_cn_trademark_gazette_package(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("registration_number", ""),
        ("announcement_type_code", ""),
        ("announcement_issue", 76),
    ],
)
def test_rejects_missing_or_cross_issue_event_identity(field, value):
    payload = package()
    payload["records"][0][field] = value
    with pytest.raises(GazetteAdmissionError):
        normalize_cn_trademark_gazette_package(payload)


@pytest.mark.parametrize(
    "field",
    [
        "applicant_name",
        "registerCnName",
        "trademark_name",
        "tmName",
        "nice_class",
        "intlCls",
        "application_date",
        "applyDate",
        "agentName",
    ],
)
def test_rejects_duplicated_trademark_entity_fields(field):
    value = package()
    value["records"][0][field] = "should-not-be-here"

    with pytest.raises(GazetteAdmissionError, match="duplicates trademark-entity fields"):
        normalize_cn_trademark_gazette_package(value)


def test_rejects_unknown_fields_fail_closed():
    value = package()
    value["workspace_id"] = "workspace-1"
    with pytest.raises(GazetteAdmissionError, match="unsupported fields"):
        normalize_cn_trademark_gazette_package(value)

    value = package()
    value["records"][0]["customer_id"] = "customer-1"
    with pytest.raises(GazetteAdmissionError, match="unsupported fields"):
        normalize_cn_trademark_gazette_package(value)


def test_complete_issue_marker_is_written_after_announcement_rows():
    class FailingAnnouncementClient(Client):
        def insert(self, table, rows, *, column_names):
            if table == ANNOUNCEMENT_TABLE:
                raise RuntimeError("simulated announcement insert failure")
            super().insert(table, rows, column_names=column_names)

    client = FailingAnnouncementClient()
    with pytest.raises(RuntimeError, match="simulated announcement insert failure"):
        admit_cn_trademark_gazette_package(package(), client=client)

    assert client.issue_rows == []


def test_retry_after_precommit_partial_rows_can_finish_and_publish_complete_marker():
    client = Client()
    value = package()
    normalized = normalize_cn_trademark_gazette_package(value)

    # Simulate announcement observations left by a failed pre-commit attempt.
    client.announcement_rows.append(
        {
            "announcement_issue": normalized.announcement_issue,
            "source_dataset_sha256": normalized.source_dataset_sha256,
        }
    )

    result = admit_cn_trademark_gazette_package(value, client=client)

    assert result.replayed is False
    assert len(client.issue_rows) == 1
    assert client.issue_rows[0]["capture_completeness"] == "COMPLETE"


def test_dataset_hash_and_authority_are_strict():
    value = package()
    value["source_dataset_sha256"] = "not-a-sha"
    with pytest.raises(GazetteAdmissionError, match="64 hexadecimal"):
        normalize_cn_trademark_gazette_package(value)

    value = package()
    value["source_authority"] = "OTHER"
    with pytest.raises(GazetteAdmissionError, match="must be CNIPA"):
        normalize_cn_trademark_gazette_package(value)
