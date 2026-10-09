from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.reference_data.wipo_mgs_admission import (
    CONTRACT_VERSION,
    DDL,
    MAPPING_VERSION,
    SOURCE,
    TABLE,
    WipoMgsAdmissionError,
    admit,
    normalize,
)
from app.reference_data.wipo_mgs_read import (
    WipoMgsReadError,
    get_current_term,
    search_current_terms,
)


def sha(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def term(*, identity: str, language: str, text: str, accepted=(), rejected=()):
    raw = {
        "id": int(identity),
        "cls": 1,
        "lng": language.upper(),
        "seq": 15,
        "src": "NICE",
        "txt": text,
        "acc": ",".join(accepted),
        "rej": ",".join(rejected),
        "prf": None,
    }
    accepted = sorted(accepted)
    rejected = sorted(rejected)
    codes = sorted(set(accepted + rejected))
    return {
        "sourceTermId": identity,
        "niceClass": 1,
        "language": language,
        "termText": text,
        "seq": raw["seq"],
        "src": raw["src"],
        "prf": raw["prf"],
        "accRaw": raw["acc"],
        "rejRaw": raw["rej"],
        "acceptedJurisdictions": accepted,
        "rejectedJurisdictions": rejected,
        "jurisdictionStatuses": [
            {
                "jurisdictionCode": code,
                "status": (
                    "conflict"
                    if code in accepted and code in rejected
                    else "accepted"
                    if code in accepted
                    else "rejected"
                ),
            }
            for code in codes
        ],
        "contentHash": sha(
            json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ),
        "rawPayload": raw,
    }


def package(*, language="en", observed_at="2026-10-09T12:00:00Z", terms=None):
    records = terms or [
        term(identity="768723", language=language, text="2-naphthol", accepted=("AU",))
    ]
    raw_sha = sha(language + observed_at + json.dumps(records, ensure_ascii=False))
    return {
        "contract_version": CONTRACT_VERSION,
        "mapping_version": MAPPING_VERSION,
        "source_owner": "MARKORBIT_KNOWLEDGE",
        "source_id": SOURCE,
        "source_uri": (
            "https://webaccess.wipo.int/mgs/process.jsp#action=load&lang=" + language + "&class=1"
        ),
        "evidence_canonical_uri": f"wipo-mgs://{language}/class/01/raw",
        "evidence_sha256": raw_sha,
        "observed_at": observed_at,
        "snapshot": {
            "schemaVersion": "WIPO_MGS_SNAPSHOT_V1",
            "source": SOURCE,
            "requestLanguage": language,
            "localeCode": language,
            "niceClass": 1,
            "sourceVersion": None,
            "responseSha256": raw_sha,
            "recordCount": len(records),
            "records": records,
            "anomalies": [],
        },
    }


class Client:
    def __init__(self):
        self.rows: dict[tuple[str, str, int, str, str], dict[str, object]] = {}
        self.insert_calls = []

    def query(self, sql, parameters=None):
        parameters = parameters or {}
        if "system.disks" in sql:
            return SimpleNamespace(result_rows=[("hot_global", 1000, 800)])
        if "system.storage_policies" in sql:
            return SimpleNamespace(result_rows=[("hot_global_only", "main", ["hot_global"])])
        if sql.startswith("SHOW CREATE TABLE"):
            return SimpleNamespace(result_rows=[(DDL,)])
        scope = (
            parameters.get("request_language"),
            parameters.get("language"),
            parameters.get("nice_class"),
        )
        scoped = [value for key, value in self.rows.items() if key[:3] == scope]
        if "ORDER BY observed_at DESC LIMIT 1" in sql:
            if not scoped:
                return SimpleNamespace(result_rows=[])
            latest = max(scoped, key=lambda value: value["observed_at"])
            return SimpleNamespace(
                result_rows=[(latest["observed_at"], latest["source_response_sha256"])]
            )
        matching = [
            value
            for value in scoped
            if value["source_response_sha256"] == parameters["response_sha"]
            and value["mapping_version"] == parameters["mapping"]
        ]
        return SimpleNamespace(
            result_rows=[(value["source_term_id"], value["record_sha256"]) for value in matching]
        )

    def insert(self, table, rows, column_names):
        assert table == TABLE
        self.insert_calls.append((table, rows, column_names))
        for values in rows:
            row = dict(zip(column_names, values, strict=True))
            key = (
                row["request_language"],
                row["language"],
                row["nice_class"],
                row["source_response_sha256"],
                row["source_term_id"],
            )
            self.rows[key] = row


def test_admits_unicode_and_language_specific_acceptance_into_hot_global_idempotently():
    client = Client()
    english = package(
        terms=[term(identity="768723", language="en", text="2-naphthol", accepted=("AU", "US"))]
    )
    chinese = package(
        language="zh",
        observed_at="2026-10-09T12:01:00Z",
        terms=[term(identity="768723", language="zh", text="2-萘酚", rejected=("US",))],
    )

    first = admit(english, client=client)
    second = admit(chinese, client=client)
    replay = admit(english, client=client)

    assert first["outcome"] == "WIPO_MGS_SNAPSHOT_ADMITTED"
    assert first["storage_placement"] == "hot_global"
    assert second["language"] == "zh"
    assert replay["replayed"] is True
    assert replay["inserted_count"] == 0
    assert len(client.rows) == 2
    en = next(value for key, value in client.rows.items() if key[1] == "en")
    zh = next(value for key, value in client.rows.items() if key[1] == "zh")
    assert en["accepted_jurisdictions"] == ["AU", "US"]
    assert zh["accepted_jurisdictions"] == []
    assert zh["rejected_jurisdictions"] == ["US"]
    assert zh["term_text"] == "2-萘酚"
    assert len(client.insert_calls) == 2
    assert "storage_policy = 'hot_global_only'" in DDL


def test_rejects_out_of_order_snapshot_and_mismatched_evidence_without_writes():
    client = Client()
    current = package(observed_at="2026-10-10T00:00:00Z")
    admit(current, client=client)
    older = package(observed_at="2026-10-09T00:00:00Z")
    with pytest.raises(WipoMgsAdmissionError, match="already admitted"):
        admit(older, client=client)
    forged = package()
    forged["evidence_sha256"] = "a" * 64
    with pytest.raises(WipoMgsAdmissionError, match="evidence SHA"):
        normalize(forged)
    assert len(client.insert_calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"source_owner": "MARKORBIT_DATA_ENGINE"},
        {"source_id": "OTHER"},
        {"source_uri": "https://example.test/mgs#action=load&lang=en&class=1"},
        {"evidence_canonical_uri": "wipo-mgs://en/class/02/raw"},
    ],
)
def test_rejects_wrong_owner_source_or_scope(change):
    with pytest.raises(WipoMgsAdmissionError):
        normalize({**package(), **change})


def test_observed_at_is_timezone_aware_and_table_partition_is_bounded():
    normalized = normalize(package())
    assert normalized.observed_at == datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    assert "PARTITION BY nice_class" in DDL


def test_global_hot_owner_registers_the_versioned_mgs_fact_admission_route():
    from app.global_trademarks.owner_api import app

    route = next(
        item
        for item in app.routes
        if item.path == "/api/admin/v2/fact-admissions/reference/wipo-mgs/snapshots"
    )
    assert route.methods == {"POST"}
    read_paths = {
        item.path for item in app.routes if item.path.startswith("/api/v1/reference/wipo-mgs")
    }
    assert read_paths == {
        "/api/v1/reference/wipo-mgs/terms",
        "/api/v1/reference/wipo-mgs/terms/{source_term_id}",
    }


class ReadClient:
    def __init__(self):
        self.calls = []
        self.result_rows = [
            (
                "768723",
                1,
                "zh",
                "zh",
                None,
                "2-萘酚",
                "15",
                '"NICE"',
                "null",
                '"CN"',
                '"US"',
                ["CN"],
                ["US"],
                '[{"jurisdictionCode":"CN","status":"accepted"},'
                '{"jurisdictionCode":"US","status":"rejected"}]',
                '{"id":768723,"txt":"2-萘酚"}',
                b"a" * 64,
                b"b" * 64,
                "wipo-mgs://zh/class/01/raw",
                b"b" * 64,
                "https://webaccess.wipo.int/mgs/process.jsp#action=load&lang=zh&class=1",
                datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc),
            )
        ]

    def query(self, sql, parameters=None):
        parameters = parameters or {}
        if "system.disks" in sql:
            return SimpleNamespace(result_rows=[("hot_global", 1000, 800)])
        if "system.storage_policies" in sql:
            return SimpleNamespace(result_rows=[("hot_global_only", "main", ["hot_global"])])
        if sql.startswith("SHOW CREATE TABLE"):
            return SimpleNamespace(result_rows=[(DDL,)])
        self.calls.append((sql, parameters))
        return SimpleNamespace(result_rows=self.result_rows)


def test_search_and_multilingual_detail_read_only_the_latest_admitted_scope():
    client = ReadClient()
    result = search_current_terms(
        client=client,
        q="萘酚",
        nice_class=1,
        language="zh",
        jurisdiction_code="US",
        acceptance_status="rejected",
        limit=10,
    )
    assert result["current_state_basis"] == "LATEST_ADMITTED_LANGUAGE_CLASS_SNAPSHOT"
    assert result["items"][0]["term_text"] == "2-萘酚"
    assert result["items"][0]["accepted_jurisdictions"] == ["CN"]
    assert result["items"][0]["rejected_jurisdictions"] == ["US"]
    assert result["items"][0]["source_response_sha256"] == "b" * 64
    search_sql, params = client.calls[0]
    assert "argMax(tuple(request_language, source_response_sha256)" in search_sql
    assert "positionCaseInsensitiveUTF8" in search_sql
    assert "has(observation.rejected_jurisdictions" in search_sql
    assert params["jurisdiction"] == "US"

    detail = get_current_term(client=client, source_term_id="768723", nice_class=1)
    assert detail["source_term_id"] == "768723"
    assert detail["localized_terms"][0]["language"] == "zh"


def test_acceptance_filter_requires_a_jurisdiction():
    with pytest.raises(WipoMgsReadError, match="requires jurisdiction_code"):
        search_current_terms(client=ReadClient(), acceptance_status="unknown")
