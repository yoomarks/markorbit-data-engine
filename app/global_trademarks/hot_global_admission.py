from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit, parse_qsl

CONTRACT_VERSION = "GLOBAL_TRADEMARK_STRUCTURED_ADMISSION_V1"
MAPPING_VERSION = "GLOBAL_TRADEMARK_NORMALIZED_V1"
TABLE = "markorbit_facts.global_trademark_hot_observation"
SOURCE = "LA_DIPO_WOPUBLISH_TRADEMARKS"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_LA_ID = re.compile(r"^LA[0-9]{3,10}$")
_ALLOWED_RECORD = frozenset(
    {
        "source_record_id",
        "application_number",
        "registration_number",
        "mark_text",
        "source_status_raw",
        "normalized_status",
        "applicant_name",
        "nice_classes",
        "filing_date",
        "logo_url",
        "source_language",
        "source_native_fields",
    }
)
_ALLOWED_PACKAGE = frozenset(
    {
        "contract_version",
        "mapping_version",
        "source_owner",
        "jurisdiction",
        "source_id",
        "observation_kind",
        "page_index",
        "source_uri",
        "evidence_canonical_uri",
        "evidence_sha256",
        "source_response_sha256",
        "observed_at",
        "records",
    }
)
DDL = """
CREATE TABLE IF NOT EXISTS markorbit_facts.global_trademark_hot_observation
(
    jurisdiction LowCardinality(String),
    source_id LowCardinality(String),
    source_record_id String,
    observation_kind LowCardinality(String),
    page_index UInt8,
    mapping_version String,
    source_response_sha256 FixedString(64),
    evidence_sha256 FixedString(64),
    evidence_canonical_uri String,
    source_uri String,
    observed_at DateTime64(3, 'UTC'),
    application_number Nullable(String),
    registration_number Nullable(String),
    mark_text Nullable(String),
    source_status_raw Nullable(String),
    normalized_status Nullable(String),
    applicant_name Nullable(String),
    nice_classes Array(UInt8),
    filing_date Nullable(Date32),
    logo_url Nullable(String),
    source_language Nullable(String),
    source_native_json String,
    record_sha256 FixedString(64),
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY jurisdiction
ORDER BY (jurisdiction, source_id, source_record_id, observation_kind,
          source_response_sha256, mapping_version)
SETTINGS storage_policy = 'hot_global_only'
""".strip()
COLUMNS = (
    "jurisdiction",
    "source_id",
    "source_record_id",
    "observation_kind",
    "page_index",
    "mapping_version",
    "source_response_sha256",
    "evidence_sha256",
    "evidence_canonical_uri",
    "source_uri",
    "observed_at",
    "application_number",
    "registration_number",
    "mark_text",
    "source_status_raw",
    "normalized_status",
    "applicant_name",
    "nice_classes",
    "filing_date",
    "logo_url",
    "source_language",
    "source_native_json",
    "record_sha256",
)


class HotGlobalAdmissionError(ValueError):
    pass


def _exact(value: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    extra = set(value) - allowed
    if extra:
        raise HotGlobalAdmissionError(
            label + " contains unsupported fields: " + ",".join(sorted(extra))
        )


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise HotGlobalAdmissionError(label + " must be lowercase SHA-256")
    return value


def _text(value: Any, label: str, maximum: int = 4096, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise HotGlobalAdmissionError(label + " must be a bounded non-empty string")
    if re.search(r"(?i)jsessionid|psusr|(?:^|[?&])token=", value):
        raise HotGlobalAdmissionError(label + " contains session or authentication material")
    return value.strip()


def _source_uri(value: Any, kind: str) -> str:
    uri = _text(value, "source_uri")
    assert uri is not None
    parsed = urlsplit(uri)
    if parsed.scheme != "https" or parsed.netloc != "online.dip.gov.la" or parsed.fragment:
        raise HotGlobalAdmissionError("source_uri must be an official HTTPS WoPublish URL")
    if ";jsessionid=" in uri.lower():
        raise HotGlobalAdmissionError("source_uri must not include Wicket session identifiers")
    expected_path = "/wopublish-search/public/" + (
        "trademarks" if kind == "LIST_PAGE" else "detail/trademarks"
    )
    if parsed.path != expected_path:
        raise HotGlobalAdmissionError("source_uri path does not match observation_kind")
    params = parse_qsl(parsed.query, keep_blank_values=True)
    if kind == "LIST_PAGE" and parsed.query != "0":
        raise HotGlobalAdmissionError("list source_uri must be the canonical first-page URL")
    if kind == "DETAIL" and (
        len(params) != 1 or params[0][0] != "id" or not _LA_ID.fullmatch(params[0][1])
    ):
        raise HotGlobalAdmissionError("detail source_uri requires one LA source record ID")
    return uri


def _record(value: Any, kind: str, detail_id: str | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HotGlobalAdmissionError("records must contain JSON objects")
    _exact(value, _ALLOWED_RECORD, "record")
    identity = _text(value.get("source_record_id"), "source_record_id", 32)
    if not _LA_ID.fullmatch(identity or "") or (detail_id and identity != detail_id):
        raise HotGlobalAdmissionError("source_record_id does not match official LA source identity")
    names = (
        "application_number",
        "registration_number",
        "mark_text",
        "source_status_raw",
        "normalized_status",
        "applicant_name",
        "logo_url",
        "source_language",
    )
    record: dict[str, Any] = {"source_record_id": identity}
    for name in names:
        record[name] = _text(value.get(name), name, 4096, optional=True)
    if record["logo_url"] is not None:
        logo = urlsplit(record["logo_url"])
        expected_logo = "/wopublish-search/service/trademarks/application/" + identity + "/logo"
        if (
            logo.scheme != "https"
            or logo.netloc != "online.dip.gov.la"
            or logo.path != expected_logo
            or logo.fragment
            or any(key != "noLogo" for key, _ in parse_qsl(logo.query))
        ):
            raise HotGlobalAdmissionError(
                "logo_url must reference the exact official source record"
            )
    if record["normalized_status"] is not None:
        raise HotGlobalAdmissionError(
            "unverified source status cannot establish a normalized legal status"
        )
    if record["application_number"] == identity or record["registration_number"] == identity:
        raise HotGlobalAdmissionError("WoPublish source ID cannot be inferred as a legal number")
    classes = value.get("nice_classes", [])
    if (
        not isinstance(classes, list)
        or len(classes) > 45
        or any(isinstance(n, bool) or not isinstance(n, int) or n < 1 or n > 45 for n in classes)
        or len(set(classes)) != len(classes)
    ):
        raise HotGlobalAdmissionError("nice_classes must be unique integers from 1 to 45")
    record["nice_classes"] = classes
    raw_date = value.get("filing_date")
    if raw_date is None:
        record["filing_date"] = None
    else:
        try:
            record["filing_date"] = date.fromisoformat(_text(raw_date, "filing_date", 10) or "")
        except ValueError as exc:
            raise HotGlobalAdmissionError("filing_date must be ISO calendar date") from exc
    native = value.get("source_native_fields", {})
    if not isinstance(native, dict) or len(json.dumps(native, ensure_ascii=False)) > 12000:
        raise HotGlobalAdmissionError("source_native_fields must be bounded JSON")
    native_json = json.dumps(native, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if re.search(r"(?i)jsessionid|psusr|token|cookie|authorization", native_json):
        raise HotGlobalAdmissionError("source_native_fields cannot contain authentication metadata")
    record["source_native_json"] = native_json
    if kind == "LIST_PAGE" and any(
        record[name] is not None
        for name in (
            "application_number",
            "registration_number",
            "source_status_raw",
            "mark_text",
            "applicant_name",
            "filing_date",
            "logo_url",
        )
    ):
        raise HotGlobalAdmissionError("ID-only list pages must not assert unobserved detail facts")
    return record


@dataclass(frozen=True)
class NormalizedAdmission:
    kind: str
    page: int
    source_uri: str
    evidence_canonical_uri: str
    evidence_sha256: str
    source_response_sha256: str
    observed_at: datetime
    records: tuple[dict[str, Any], ...]


def normalize(package: Mapping[str, Any]) -> NormalizedAdmission:
    if not isinstance(package, dict):
        raise HotGlobalAdmissionError("admission package must be a JSON object")
    _exact(package, _ALLOWED_PACKAGE, "package")
    required = {
        "contract_version": CONTRACT_VERSION,
        "mapping_version": MAPPING_VERSION,
        "source_owner": "MARKORBIT_KNOWLEDGE",
        "jurisdiction": "LA",
        "source_id": SOURCE,
    }
    for key, expected in required.items():
        if package.get(key) != expected:
            raise HotGlobalAdmissionError(key + " must equal " + expected)
    kind = package.get("observation_kind")
    if kind not in ("LIST_PAGE", "DETAIL"):
        raise HotGlobalAdmissionError("only bounded LA LIST_PAGE or DETAIL admitted")
    page = package.get("page_index")
    if isinstance(page, bool) or page not in ((1, 2) if kind == "LIST_PAGE" else (0,)):
        raise HotGlobalAdmissionError("page_index outside LA pilot bound")
    uri = _source_uri(package.get("source_uri"), kind)
    canonical = _text(package.get("evidence_canonical_uri"), "evidence_canonical_uri")
    if (
        not canonical
        or not canonical.startswith("la-dipo://wopublish/trademarks/")
        or "?" in canonical
    ):
        raise HotGlobalAdmissionError("evidence_canonical_uri must reference Knowledge LA artifact")
    expected_canonical = (
        ("la-dipo://wopublish/trademarks/list/page/" + str(page) + "/redacted-response")
        if kind == "LIST_PAGE"
        else (
            "la-dipo://wopublish/trademarks/detail/"
            + dict(parse_qsl(urlsplit(uri).query))["id"]
            + "/redacted-response"
        )
    )
    if canonical != expected_canonical:
        raise HotGlobalAdmissionError(
            "Knowledge evidence URI does not match requested source scope"
        )
    evidence_sha = _sha(package.get("evidence_sha256"), "evidence_sha256")
    response_sha = _sha(package.get("source_response_sha256"), "source_response_sha256")
    timestamp = _text(package.get("observed_at"), "observed_at", 48)
    try:
        instant = datetime.fromisoformat((timestamp or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise HotGlobalAdmissionError("observed_at must be an ISO instant") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise HotGlobalAdmissionError("observed_at requires timezone")
    instant = instant.astimezone(timezone.utc)
    values = package.get("records")
    count = 50 if kind == "LIST_PAGE" else 1
    if not isinstance(values, list) or len(values) != count:
        raise HotGlobalAdmissionError("bounded pilot record count mismatch")
    detail_id = dict(parse_qsl(urlsplit(uri).query)).get("id") if kind == "DETAIL" else None
    rows = tuple(_record(item, kind, detail_id) for item in values)
    if len({row["source_record_id"] for row in rows}) != len(rows):
        raise HotGlobalAdmissionError("duplicate source_record_id in one source response")
    return NormalizedAdmission(
        kind, page, uri, canonical, evidence_sha, response_sha, instant, rows
    )


def _fingerprint(row: dict[str, Any], admission: NormalizedAdmission) -> str:
    material = json.dumps(
        {
            **row,
            "_evidence_sha256": admission.evidence_sha256,
            "_evidence_canonical_uri": admission.evidence_canonical_uri,
            "_source_uri": admission.source_uri,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def require_hot_global_ready(client: Any) -> None:
    disks = client.query(
        "SELECT name, total_space, free_space FROM system.disks WHERE name = 'hot_global'"
    ).result_rows
    if len(disks) != 1:
        raise RuntimeError("hot_global physical disk is missing or ambiguous")
    total, free = int(disks[0][1]), int(disks[0][2])
    if total <= 0 or free * 100 < total * 20:
        raise RuntimeError("hot_global disk is below the 20 percent hard free-space reserve")
    policies = client.query(
        "SELECT policy_name, volume_name, disks FROM system.storage_policies "
        "WHERE policy_name = 'hot_global_only'"
    ).result_rows
    if not policies or {disk for row in policies for disk in row[2]} != {"hot_global"}:
        raise RuntimeError("hot_global_only must resolve exclusively to hot_global")
    current = client.query("SHOW CREATE TABLE " + TABLE).result_rows
    if len(current) != 1 or "hot_global_only" not in str(current[0][0]):
        raise RuntimeError("global trademark table is missing or not bound to hot_global_only")


def install_hot_global_schema(client: Any) -> None:
    """Explicit operator migration only; never called from HTTP requests."""
    disks = client.query(
        "SELECT name, total_space, free_space FROM system.disks WHERE name = 'hot_global'"
    ).result_rows
    if len(disks) != 1 or int(disks[0][1]) <= 0 or (int(disks[0][2]) * 100 < int(disks[0][1]) * 20):
        raise RuntimeError("hot_global disk not ready for migration")
    policies = client.query(
        "SELECT policy_name, volume_name, disks FROM system.storage_policies "
        "WHERE policy_name = 'hot_global_only'"
    ).result_rows
    if not policies or {disk for row in policies for disk in row[2]} != {"hot_global"}:
        raise RuntimeError("hot_global_only policy is missing or ambiguous")
    client.command("CREATE DATABASE IF NOT EXISTS markorbit_facts")
    client.command(DDL)
    require_hot_global_ready(client)


def admit(package: Mapping[str, Any], *, client: Any) -> dict[str, Any]:
    normalized = normalize(package)
    require_hot_global_ready(client)
    query = (
        "SELECT source_record_id, record_sha256 FROM "
        + TABLE
        + " WHERE jurisdiction = 'LA' AND source_id = %(source_id)s "
        "AND observation_kind = %(kind)s AND page_index = %(page)s "
        "AND source_response_sha256 = %(response_sha)s AND mapping_version = %(mapping)s"
    )
    params = {
        "source_id": SOURCE,
        "kind": normalized.kind,
        "page": normalized.page,
        "response_sha": normalized.source_response_sha256,
        "mapping": MAPPING_VERSION,
    }
    present = client.query(query, parameters=params).result_rows
    seen = dict(present)
    if len(seen) != len(present):
        raise HotGlobalAdmissionError("duplicate persisted observations for one evidence identity")
    expected = {
        row["source_record_id"]: _fingerprint(row, normalized) for row in normalized.records
    }
    if any(key not in expected or expected[key] != digest for key, digest in seen.items()):
        raise HotGlobalAdmissionError("conflicting mapped facts for the same source evidence")
    missing = [row for row in normalized.records if row["source_record_id"] not in seen]
    if missing:
        rows = []
        for row in missing:
            rows.append(
                [
                    "LA",
                    SOURCE,
                    row["source_record_id"],
                    normalized.kind,
                    normalized.page,
                    MAPPING_VERSION,
                    normalized.source_response_sha256,
                    normalized.evidence_sha256,
                    normalized.evidence_canonical_uri,
                    normalized.source_uri,
                    normalized.observed_at,
                    row["application_number"],
                    row["registration_number"],
                    row["mark_text"],
                    row["source_status_raw"],
                    row["normalized_status"],
                    row["applicant_name"],
                    row["nice_classes"],
                    row["filing_date"],
                    row["logo_url"],
                    row["source_language"],
                    row["source_native_json"],
                    expected[row["source_record_id"]],
                ]
            )
        client.insert(TABLE, rows, column_names=list(COLUMNS))
    readback = client.query(query, parameters=params).result_rows
    after = dict(readback)
    if len(after) != len(readback) or after != expected:
        raise RuntimeError("hot_global read-back incomplete; receipt must not claim success")
    return {
        "contract_version": CONTRACT_VERSION,
        "outcome": "BOUNDED_OBSERVATIONS_ADMITTED",
        "jurisdiction": "LA",
        "source_id": SOURCE,
        "observation_kind": normalized.kind,
        "page_index": normalized.page,
        "record_count": len(expected),
        "inserted_count": len(missing),
        "replayed": not missing,
        "source_response_sha256": normalized.source_response_sha256,
        "evidence_sha256": normalized.evidence_sha256,
        "storage_placement": "hot_global",
        "current_state_verified": False,
    }
