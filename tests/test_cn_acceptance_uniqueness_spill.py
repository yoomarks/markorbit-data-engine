from app.cn.audit_acceptance_m16 import (
    CNAcceptanceResourceClient,
    CN_ACCEPTANCE_PARTY_UNIQUENESS_BUCKETS,
)


class _Result:
    def __init__(self, value):
        self.result_rows = [(value,)]


class _Delegate:
    def __init__(self):
        self.queries = []

    def query(self, sql, *args, **kwargs):
        self.queries.append((sql, kwargs))
        if "cn_case_current" in sql and "cn_case_scope_current" not in sql:
            return _Result(2)
        if "cn_case_scope_current" in sql:
            return _Result(3)
        if "cn_case_party_current" in sql:
            return _Result(1)
        raise AssertionError(sql)


def test_acceptance_uniqueness_rewrites_uniqexact_to_spillable_group_by():
    delegate = _Delegate()
    client = CNAcceptanceResourceClient(delegate)

    result = client.query(
        """
        SELECT
            (SELECT count() - uniqExact(application_number)
             FROM markorbit_facts.cn_case_current FINAL WHERE is_deleted = 0) AS duplicate_cases,
            (SELECT count() - uniqExact(tuple(application_number, class_no))
             FROM markorbit_facts.cn_case_scope_current FINAL WHERE is_deleted = 0) AS duplicate_scopes,
            (SELECT count() - uniqExact(tuple(application_number, role, relation_key))
             FROM markorbit_facts.cn_case_party_current FINAL
             WHERE is_deleted = 0 AND is_current = 1) AS duplicate_current_parties
        """
    )

    assert result.result_rows == [(2, 3, CN_ACCEPTANCE_PARTY_UNIQUENESS_BUCKETS)]
    assert len(delegate.queries) == 2 + CN_ACCEPTANCE_PARTY_UNIQUENESS_BUCKETS

    party_queries = []
    for sql, kwargs in delegate.queries:
        assert "uniqExact" not in sql
        assert "GROUP BY" in sql
        assert "HAVING group_count > 1" in sql
        assert "sum(group_count - 1)" in sql
        settings = kwargs["settings"]
        assert settings["max_threads"] == 1
        assert settings["max_memory_usage"] == 8_589_934_592
        assert settings["max_bytes_before_external_group_by"] == 67_108_864
        if "cn_case_party_current" in sql:
            party_queries.append(sql)

    assert len(party_queries) == CN_ACCEPTANCE_PARTY_UNIQUENESS_BUCKETS
    for bucket, sql in enumerate(party_queries):
        assert (
            "cityHash64(application_number, role, relation_key) % "
            f"{CN_ACCEPTANCE_PARTY_UNIQUENESS_BUCKETS} = {bucket}"
        ) in sql


def test_acceptance_client_leaves_non_uniqueness_queries_unchanged():
    class _PassthroughDelegate:
        def query(self, sql, *args, **kwargs):
            self.sql = sql
            self.kwargs = kwargs
            return _Result(7)

    delegate = _PassthroughDelegate()
    client = CNAcceptanceResourceClient(delegate)
    result = client.query("SELECT 7")

    assert result.result_rows == [(7,)]
    assert delegate.sql == "SELECT 7"
