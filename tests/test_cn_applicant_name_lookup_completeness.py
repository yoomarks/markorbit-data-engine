from __future__ import annotations

from app.cn.applicant_name_lookup_completeness import verify_cn_applicant_name_lookup


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class Client:
    def __init__(self, lookup_rows=2, checksum="9", bad_name=False):
        self.lookup_rows, self.checksum, self.bad_name = lookup_rows, checksum, bad_name

    def query(self, sql, settings=None):
        if "FROM system.tables" in sql:
            return Result(
                [
                    (
                        "cn_applicant_name_lookup_current",
                        "ReplacingMergeTree",
                        "normalized_name, entity_id, application_number, relation_key",
                    ),
                    ("cn_applicant_name_lookup_from_case_party_mv", "MaterializedView", ""),
                ]
            )
        if "count()" in sql:
            return (
                Result([(2, "9", "7")])
                if "cn_case_party_current" in sql
                else Result([(self.lookup_rows, self.checksum, "7")])
            )
        return (
            Result([("错误", "北京示例（有限）公司")])
            if self.bad_name
            else Result([("北京示例有限公司", "北京示例（有限）公司")])
        )


def test_complete_receipt_requires_projection_bindings_and_normalization():
    receipt = verify_cn_applicant_name_lookup(Client())
    assert receipt["complete"] is True
    assert receipt["schema_match"] is True
    assert receipt["sample_mismatches"] == 0


def test_mismatch_fails_closed():
    assert verify_cn_applicant_name_lookup(Client(lookup_rows=1))["complete"] is False
    assert verify_cn_applicant_name_lookup(Client(checksum="10"))["complete"] is False
    assert verify_cn_applicant_name_lookup(Client(bad_name=True))["complete"] is False
