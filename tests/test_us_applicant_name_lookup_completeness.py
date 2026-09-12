from __future__ import annotations

from app.applicant_name_lookup import us_applicant_name_lookup_row
from app.us.applicant_candidate_index import IDENTITY_FIELDS, applicant_candidate_key
from app.us.applicant_name_lookup_completeness import verify_us_applicant_name_lookup
from app.us.publisher import APPLICANT_INDEX_COLUMNS


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class FakeClient:
    def __init__(self, *, lookup_rows=3, checksum="99", bad_name=False):
        self.lookup_rows = lookup_rows
        self.checksum = checksum
        self.bad_name = bad_name

    def query(self, sql, settings=None):
        if "FROM system.tables" in sql:
            return Result(
                [("ReplacingMergeTree", "normalized_name, candidate_key, serial_number, owner_key")]
            )
        if "count()" in sql:
            if "us_applicant_candidate_current" in sql:
                return Result([(3, "99", "17")])
            return Result([(self.lookup_rows, self.checksum, "17")])
        source = {column: "" for column in APPLICANT_INDEX_COLUMNS}
        source["party_name_norm"] = "Acme LLC"
        source["candidate_key"] = applicant_candidate_key(
            {field: source[field] for field in IDENTITY_FIELDS}
        )
        name = us_applicant_name_lookup_row(source)[0]
        if self.bad_name:
            name = "wrong"
        return Result([(name, *(source[column] for column in APPLICANT_INDEX_COLUMNS))])


def test_complete_receipt_checks_schema_bindings_and_sample_normalization():
    receipt = verify_us_applicant_name_lookup(FakeClient())
    assert receipt["complete"] is True
    assert receipt["schema_match"] is True
    assert receipt["binding_checksum_match"] is True
    assert receipt["sample_mismatches"] == 0


def test_bad_normalized_name_fails_closed():
    receipt = verify_us_applicant_name_lookup(FakeClient(bad_name=True))
    assert receipt["complete"] is False
    assert receipt["sample_mismatches"] == 1


def test_count_or_checksum_mismatch_fails_closed():
    assert verify_us_applicant_name_lookup(FakeClient(lookup_rows=2))["complete"] is False
    assert verify_us_applicant_name_lookup(FakeClient(checksum="100"))["complete"] is False
