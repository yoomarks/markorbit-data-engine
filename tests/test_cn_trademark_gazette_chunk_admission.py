from __future__ import annotations

from datetime import date

import pytest

from app.cn.trademark_gazette_admission import (
    ANNOUNCEMENT_TABLE,
    ISSUE_TABLE,
    GazetteAdmissionError,
)
from app.cn.trademark_gazette_chunk_admission import (
    CHUNK_ADMISSION_CONTRACT_VERSION,
    CHUNK_FINALIZE_CONTRACT_VERSION,
    CHUNK_STAGE_TABLE,
    admit_cn_trademark_gazette_chunk,
    finalize_cn_trademark_gazette_chunks,
    normalize_cn_trademark_gazette_chunk,
)


DATASET_SHA = "b" * 64
SOURCE_URI = (
    "https://pub.sbj.cnipa.gov.cn/toas-pub-prod/pub-prod-api/"
    "public/web/anncInfo/searchEsTmgg"
)
SCHEMA = "CNIPA_GAZETTE_CHECKPOINT_ARTIFACT_V1"


def record(index: int) -> dict:
    row_id = f"row-{index:04d}"
    return {
        "source_row_id": row_id,
        "source_search_id": row_id,
        "registration_number": str(200000 + index),
        "announcement_issue": 75,
        "announcement_type_code": "TMZCSQ",
        "announcement_type_name": "鍟嗘爣鍒濇瀹″畾鍏憡",
        "detail_page_no": index + 1,
        "announcement_page_count": 97,
        "detail_file_id": f"file-{index // 5}",
        "detail_asset_path": f"/group/page-{index // 5}.jpg",
        "announcement_detail_url": "",
    }


def chunk(start_page: int, end_page: int, start_record: int, count: int) -> dict:
    page_counts = []
    for page_index in range(start_page, end_page + 1):
        page_counts.append(
            {
                "page_index": page_index,
                "row_count": 50 if page_index == 3 else 100,
            }
        )
    return {
        "contract_version": CHUNK_ADMISSION_CONTRACT_VERSION,
        "source_authority": "CNIPA",
        "query_scope": {
            "announcement_type_selection": "ALL",
            "annc_type": "",
        },
        "announcement_issue": 75,
        "announcement_date": "1983-08-15",
        "source_record_count": 250,
        "source_page_count": 3,
        "page_size": 100,
        "range_start_page": start_page,
        "range_end_page": end_page,
        "page_row_counts": page_counts,
        "chunk_row_count": count,
        "source_capture_schema": SCHEMA,
        "source_dataset_sha256": DATASET_SHA,
        "source_uri": SOURCE_URI,
        "collected_at": "2026-09-19T07:00:00.000Z",
        "records": [record(i) for i in range(start_record, start_record + count)],
    }


def finalize_request() -> dict:
    return {
        "contract_version": CHUNK_FINALIZE_CONTRACT_VERSION,
        "source_authority": "CNIPA",
        "query_scope": {
            "announcement_type_selection": "ALL",
            "annc_type": "",
        },
        "announcement_issue": 75,
        "announcement_date": "1983-08-15",
        "record_count": 250,
        "page_count": 3,
        "page_size": 100,
        "source_capture_schema": SCHEMA,
        "source_dataset_sha256": DATASET_SHA,
        "source_uri": SOURCE_URI,
        "collected_at": "2026-09-19T07:05:00.000Z",
    }


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class Client:
    def __init__(self):
        self.issue_rows: list[dict] = []
        self.announcement_rows: list[dict] = []
        self.stage_rows: list[dict] = []
        self.insert_order: list[str] = []

    def query(self, sql, *, parameters):
        if "SELECT chunk_fingerprint, chunk_row_count" in sql:
            matches = [
                row
                for row in self.stage_rows
                if row["announcement_issue"] == parameters["issue"]
                and row["source_dataset_sha256"] == parameters["dataset_sha"]
                and row["range_start_page"] == parameters["range_start"]
                and row["range_end_page"] == parameters["range_end"]
            ]
            return Result(
                [
                    (row["chunk_fingerprint"], row["chunk_row_count"])
                    for row in matches[:1]
                ]
            )

        if f"FROM {ISSUE_TABLE} FINAL" in sql:
            matches = [
                row
                for row in self.issue_rows
                if row["announcement_issue"] == parameters["issue"]
                and row["source_dataset_sha256"] == parameters["dataset_sha"]
            ]
            return Result([(row["record_count"],) for row in matches[:1]])

        if "SELECT count()" in sql and CHUNK_STAGE_TABLE in sql:
            matches = [
                row
                for row in self.stage_rows
                if row["announcement_issue"] == parameters["issue"]
                and row["source_dataset_sha256"] == parameters["dataset_sha"]
            ]
            return Result([(len(matches),)])

        if "SELECT\n            range_start_page" in sql:
            matches = sorted(
                [
                    row
                    for row in self.stage_rows
                    if row["announcement_issue"] == parameters["issue"]
                    and row["source_dataset_sha256"] == parameters["dataset_sha"]
                ],
                key=lambda row: (row["range_start_page"], row["range_end_page"]),
            )
            return Result(
                [
                    (
                        row["range_start_page"],
                        row["range_end_page"],
                        row["source_record_count"],
                        row["source_page_count"],
                        row["page_size"],
                        row["chunk_row_count"],
                        row["announcement_date"],
                        row["query_scope"],
                        row["source_capture_schema"],
                        row["source_uri"],
                    )
                    for row in matches
                ]
            )

        if "uniqExact(source_row_id)" in sql:
            ids = {
                row["source_row_id"]
                for row in self.announcement_rows
                if row["announcement_issue"] == parameters["issue"]
                and row["source_dataset_sha256"] == parameters["dataset_sha"]
            }
            return Result([(len(ids),)])

        raise AssertionError(sql)

    def insert(self, table, rows, *, column_names):
        self.insert_order.append(table)
        target = {
            ANNOUNCEMENT_TABLE: self.announcement_rows,
            CHUNK_STAGE_TABLE: self.stage_rows,
            ISSUE_TABLE: self.issue_rows,
        }[table]
        for values in rows:
            target.append(dict(zip(column_names, values, strict=True)))


def test_normalizes_bounded_chunk_and_freezes_all_scope():
    normalized = normalize_cn_trademark_gazette_chunk(chunk(1, 2, 0, 200))

    assert normalized.announcement_issue == 75
    assert normalized.announcement_date == date(1983, 8, 15)
    assert normalized.source_record_count == 250
    assert normalized.source_page_count == 3
    assert normalized.range_start_page == 1
    assert normalized.range_end_page == 2
    assert normalized.page_row_counts == ((1, 100), (2, 100))
    assert normalized.chunk_row_count == 200
    assert len(normalized.records) == 200
    assert len(normalized.chunk_fingerprint) == 64


@pytest.mark.parametrize(
    "mutation,match",
    [
        (
            lambda value: value["query_scope"].update({"annc_type": "TMZCSQ"}),
            "query_scope",
        ),
        (
            lambda value: value.update({"range_end_page": 4}),
            "exceeds source_page_count",
        ),
        (
            lambda value: value["page_row_counts"][0].update({"row_count": 99}),
            "expected 100",
        ),
        (
            lambda value: value.update({"chunk_row_count": 199}),
            "page_row_counts",
        ),
    ],
)
def test_chunk_contract_fails_closed_on_scope_and_range_drift(mutation, match):
    value = chunk(1, 2, 0, 200)
    mutation(value)
    with pytest.raises(GazetteAdmissionError, match=match):
        normalize_cn_trademark_gazette_chunk(value)


def test_zero_record_chunk_uses_one_empty_page():
    value = chunk(1, 1, 0, 0)
    value["source_record_count"] = 0
    value["source_page_count"] = 1
    value["page_row_counts"] = [{"page_index": 1, "row_count": 0}]
    value["records"] = []

    normalized = normalize_cn_trademark_gazette_chunk(value)

    assert normalized.source_record_count == 0
    assert normalized.source_page_count == 1
    assert normalized.chunk_row_count == 0
    assert normalized.records == ()


def test_chunk_fingerprint_binds_source_schema_and_uri():
    baseline = normalize_cn_trademark_gazette_chunk(chunk(1, 2, 0, 200))
    changed_uri = chunk(1, 2, 0, 200)
    changed_uri["source_uri"] = SOURCE_URI + "?mirror=1"
    changed_schema = chunk(1, 2, 0, 200)
    changed_schema["source_capture_schema"] = SCHEMA + "_ALT"

    assert (
        normalize_cn_trademark_gazette_chunk(changed_uri).chunk_fingerprint
        != baseline.chunk_fingerprint
    )
    assert (
        normalize_cn_trademark_gazette_chunk(changed_schema).chunk_fingerprint
        != baseline.chunk_fingerprint
    )


def test_chunk_rejects_duplicated_trademark_entity_fields():
    value = chunk(3, 3, 200, 50)
    value["records"][0]["tmName"] = "must-not-enter-gazette"
    with pytest.raises(GazetteAdmissionError, match="duplicates trademark-entity fields"):
        normalize_cn_trademark_gazette_chunk(value)


def test_chunk_commit_marker_is_written_after_announcement_rows_and_replay_is_idempotent():
    client = Client()
    value = chunk(1, 2, 0, 200)

    first = admit_cn_trademark_gazette_chunk(value, client=client)
    replay = admit_cn_trademark_gazette_chunk(value, client=client)

    assert first.replayed is False
    assert replay.replayed is True
    assert len(client.announcement_rows) == 200
    assert len(client.stage_rows) == 1
    assert client.insert_order[:2] == [ANNOUNCEMENT_TABLE, CHUNK_STAGE_TABLE]


def test_failed_row_insert_cannot_publish_chunk_commit_marker():
    class FailingClient(Client):
        def insert(self, table, rows, *, column_names):
            if table == ANNOUNCEMENT_TABLE:
                raise RuntimeError("simulated row insert failure")
            super().insert(table, rows, column_names=column_names)

    client = FailingClient()
    with pytest.raises(RuntimeError, match="simulated row insert failure"):
        admit_cn_trademark_gazette_chunk(chunk(1, 2, 0, 200), client=client)

    assert client.stage_rows == []


def test_finalize_requires_complete_contiguous_chunks_and_unique_rows():
    client = Client()
    admit_cn_trademark_gazette_chunk(chunk(1, 2, 0, 200), client=client)
    admit_cn_trademark_gazette_chunk(chunk(3, 3, 200, 50), client=client)

    receipt = finalize_cn_trademark_gazette_chunks(finalize_request(), client=client)

    assert receipt.replayed is False
    assert receipt.record_count == 250
    assert receipt.page_count == 3
    assert receipt.chunk_count == 2
    assert len(client.issue_rows) == 1
    assert client.issue_rows[0]["capture_completeness"] == "COMPLETE"
    assert client.insert_order[-1] == ISSUE_TABLE

    replay = finalize_cn_trademark_gazette_chunks(finalize_request(), client=client)
    assert replay.replayed is True
    assert len(client.issue_rows) == 1


def test_finalize_rejects_gap_without_publishing_issue_marker():
    client = Client()
    admit_cn_trademark_gazette_chunk(chunk(3, 3, 200, 50), client=client)

    with pytest.raises(GazetteAdmissionError, match="gap/overlap"):
        finalize_cn_trademark_gazette_chunks(finalize_request(), client=client)
    assert client.issue_rows == []


def test_finalize_rejects_missing_unique_rows_without_publishing_issue_marker():
    client = Client()
    admit_cn_trademark_gazette_chunk(chunk(1, 2, 0, 200), client=client)
    admit_cn_trademark_gazette_chunk(chunk(3, 3, 200, 50), client=client)
    client.announcement_rows.pop()

    with pytest.raises(GazetteAdmissionError, match="unique admitted row count 249"):
        finalize_cn_trademark_gazette_chunks(finalize_request(), client=client)
    assert client.issue_rows == []


def test_finalize_rejects_staged_metadata_drift():
    client = Client()
    admit_cn_trademark_gazette_chunk(chunk(1, 2, 0, 200), client=client)
    admit_cn_trademark_gazette_chunk(chunk(3, 3, 200, 50), client=client)
    client.stage_rows[1]["announcement_date"] = date(1983, 8, 16)

    with pytest.raises(GazetteAdmissionError, match="metadata drifted"):
        finalize_cn_trademark_gazette_chunks(finalize_request(), client=client)
    assert client.issue_rows == []
