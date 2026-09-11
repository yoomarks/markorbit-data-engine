from __future__ import annotations

from app.us.applicant_candidate_completeness import verify_us_applicant_candidate_index
from app.us.applicant_candidate_index import IDENTITY_FIELDS, applicant_candidate_key


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class FakeClient:
    def __init__(self, *, index_rows=3, checksum="99", bad_key=False):
        self.index_rows = index_rows
        self.checksum = checksum
        self.bad_key = bad_key
        self.queries = []

    def query(self, sql, settings=None):
        self.queries.append((sql, settings))
        if "FROM system.tables" in sql:
            return Result([("ReplacingMergeTree", "candidate_key, serial_number, owner_key")])
        if "FROM markorbit_facts.us_owner_current FINAL" in sql:
            return Result([(3, "99", "17")])
        if "count() AS visible_rows" in sql:
            return Result([(self.index_rows, self.checksum, "17")])
        owner = {field: "" for field in IDENTITY_FIELDS}
        owner["party_name_norm"] = "acme llc"
        key = applicant_candidate_key(owner)
        if self.bad_key:
            key = "f" * 64
        return Result([(key, *(owner[field] for field in IDENTITY_FIELDS))])


def test_complete_receipt_requires_count_checksum_schema_and_candidate_key_match():
    receipt = verify_us_applicant_candidate_index(FakeClient())
    assert receipt["complete"] is True
    assert receipt["count_match"] is True
    assert receipt["binding_checksum_match"] is True
    assert receipt["schema_match"] is True
    assert receipt["candidate_key_sample_mismatches"] == 0


def test_count_mismatch_fails_completeness():
    receipt = verify_us_applicant_candidate_index(FakeClient(index_rows=2))
    assert receipt["complete"] is False
    assert receipt["count_match"] is False


def test_checksum_mismatch_fails_completeness():
    receipt = verify_us_applicant_candidate_index(FakeClient(checksum="100"))
    assert receipt["complete"] is False
    assert receipt["binding_checksum_match"] is False


def test_candidate_key_mismatch_fails_completeness():
    receipt = verify_us_applicant_candidate_index(FakeClient(bad_key=True))
    assert receipt["complete"] is False
    assert receipt["candidate_key_sample_mismatches"] == 1
