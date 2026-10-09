from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit

CONTRACT_VERSION = "WIPO_MGS_STRUCTURED_ADMISSION_V1"
MAPPING_VERSION = "WIPO_MGS_NORMALIZED_V1"
SNAPSHOT_SCHEMA_VERSION = "WIPO_MGS_SNAPSHOT_V1"
SOURCE = "WIPO_MGS"
TABLE = "markorbit_facts.wipo_mgs_term_observation"
MAX_RECORDS = 100_000
MAX_NORMALIZED_PAYLOAD_BYTES = 128 * 1024 * 1024
_SHA = re.compile(r"^[0-9a-f]{64}$")
_LANGUAGE = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
_JURISDICTION = re.compile(r"^[A-Z][A-Z0-9-]{1,7}$")
_ALLOWED_PACKAGE = frozenset(
    {
        "contract_version",
        "mapping_version",
        "source_owner",
        "source_id",
        "source_uri",
        "evidence_canonical_uri",
        "evidence_sha256",
        "observed_at",
        "snapshot",
    }
)
_ALLOWED_SNAPSHOT = frozenset(
    {
        "schemaVersion",
        "source",
        "requestLanguage",
        "localeCode",
        "niceClass",
        "sourceVersion",
        "responseSha256",
        "recordCount",
        "records",
        "anomalies",
    }
)
_ALLOWED_TERM = frozenset(
    {
        "sourceTermId",
        "niceClass",
        "language",
        "termText",
        "seq",
        "src",
        "prf",
        "accRaw",
        "rejRaw",
        "acceptedJurisdictions",
        "rejectedJurisdictions",
        "jurisdictionStatuses",
        "contentHash",
        "rawPayload",
    }
)
_ALLOWED_STATUS = frozenset({"jurisdictionCode", "status"})

DDL = """
CREATE TABLE IF NOT EXISTS markorbit_facts.wipo_mgs_term_observation
(
    source_id LowCardinality(String),
    source_term_id String,
    nice_class UInt8,
    language LowCardinality(String),
    request_language LowCardinality(String),
    source_version Nullable(String),
    mapping_version String,
    source_response_sha256 FixedString(64),
    evidence_sha256 FixedString(64),
    evidence_canonical_uri String,
    source_uri String,
    observed_at DateTime64(3, 'UTC'),
    term_text String,
    seq_json String,
    src_json String,
    prf_json String,
    acc_raw_json String,
    rej_raw_json String,
    accepted_jurisdictions Array(String),
    rejected_jurisdictions Array(String),
    jurisdiction_statuses_json String,
    raw_payload_json String,
    content_sha256 FixedString(64),
    record_sha256 FixedString(64),
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY nice_class
ORDER BY (nice_class, language, source_term_id, observed_at,
          source_response_sha256, mapping_version)
SETTINGS storage_policy = 'hot_global_only'
""".strip()

COLUMNS = (
    "source_id",
    "source_term_id",
    "nice_class",
    "language",
    "request_language",
    "source_version",
    "mapping_version",
    "source_response_sha256",
    "evidence_sha256",
    "evidence_canonical_uri",
    "source_uri",
    "observed_at",
    "term_text",
    "seq_json",
    "src_json",
    "prf_json",
    "acc_raw_json",
    "rej_raw_json",
    "accepted_jurisdictions",
    "rejected_jurisdictions",
    "jurisdiction_statuses_json",
    "raw_payload_json",
    "content_sha256",
    "record_sha256",
)


class WipoMgsAdmissionError(ValueError):
    pass


def _exact(value: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    extra = set(value) - allowed
    if extra:
        raise WipoMgsAdmissionError(
            label + " contains unsupported fields: " + ",".join(sorted(extra))
        )


def _text(value: Any, label: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise WipoMgsAdmissionError(label + " must be a bounded non-empty string")
    return value.strip()


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise WipoMgsAdmissionError(label + " must be lowercase SHA-256")
    return value


def _json(value: Any, label: str, maximum: int = 65_536) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise WipoMgsAdmissionError(label + " must be JSON-compatible") from exc
    if len(encoded.encode("utf-8")) > maximum:
        raise WipoMgsAdmissionError(label + " exceeds its JSON size bound")
    return encoded


def _string_list(value: Any, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > 500
        or any(not isinstance(item, str) or not _JURISDICTION.fullmatch(item) for item in value)
        or value != sorted(set(value))
    ):
        raise WipoMgsAdmissionError(label + " must be sorted unique jurisdiction codes")
    return value


def _statuses(value: Any, accepted: list[str], rejected: list[str]) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 500:
        raise WipoMgsAdmissionError("jurisdictionStatuses must be a bounded array")
    observed: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise WipoMgsAdmissionError("jurisdictionStatuses must contain objects")
        _exact(item, _ALLOWED_STATUS, "jurisdictionStatus")
        code = item.get("jurisdictionCode")
        status = item.get("status")
        if not isinstance(code, str) or not _JURISDICTION.fullmatch(code):
            raise WipoMgsAdmissionError("jurisdictionStatus code is invalid")
        expected = (
            "conflict"
            if code in accepted and code in rejected
            else "accepted"
            if code in accepted
            else "rejected"
            if code in rejected
            else None
        )
        if status != expected:
            raise WipoMgsAdmissionError("jurisdictionStatus conflicts with accepted/rejected sets")
        observed.append({"jurisdictionCode": code, "status": status})
    expected_codes = sorted(set(accepted + rejected))
    if [item["jurisdictionCode"] for item in observed] != expected_codes:
        raise WipoMgsAdmissionError(
            "jurisdictionStatuses must cover the parsed source sets exactly"
        )
    return observed


def _source_uri(value: Any, request_language: str, nice_class: int) -> str:
    uri = _text(value, "source_uri")
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "webaccess.wipo.int"
        or parsed.path != "/mgs/process.jsp"
        or parsed.query
    ):
        raise WipoMgsAdmissionError("source_uri must identify the fixed WIPO MGS HTTPS endpoint")
    params = parse_qsl(parsed.fragment, keep_blank_values=True)
    expected = [("action", "load"), ("lang", request_language), ("class", str(nice_class))]
    if params != expected:
        raise WipoMgsAdmissionError(
            "source_uri fragment does not match the admitted snapshot scope"
        )
    return uri


def _term(value: Any, nice_class: int, language: str, request_language: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WipoMgsAdmissionError("records must contain JSON objects")
    _exact(value, _ALLOWED_TERM, "term")
    source_term_id = _text(value.get("sourceTermId"), "sourceTermId", 128)
    if value.get("niceClass") != nice_class or value.get("language") != language:
        raise WipoMgsAdmissionError("term class/language must match the snapshot scope")
    term_text = _text(value.get("termText"), "termText", 32_768)
    content_hash = _sha(value.get("contentHash"), "contentHash")
    accepted = _string_list(value.get("acceptedJurisdictions"), "acceptedJurisdictions")
    rejected = _string_list(value.get("rejectedJurisdictions"), "rejectedJurisdictions")
    statuses = _statuses(value.get("jurisdictionStatuses"), accepted, rejected)
    raw = value.get("rawPayload")
    if not isinstance(raw, dict):
        raise WipoMgsAdmissionError("rawPayload must be an object")
    raw_id = raw.get("id")
    if str(raw_id).strip() != source_term_id or str(raw.get("cls")).strip() != str(nice_class):
        raise WipoMgsAdmissionError("rawPayload identity/class does not match the normalized term")
    if raw.get("txt") != value.get("termText"):
        raise WipoMgsAdmissionError("rawPayload text does not match the normalized term")
    raw_language = raw.get("lng")
    if not isinstance(raw_language, str) or raw_language.casefold() not in {
        language.casefold(),
        request_language.casefold(),
    }:
        raise WipoMgsAdmissionError("rawPayload language does not match the snapshot scope")
    for normalized_name, raw_name in (
        ("seq", "seq"),
        ("src", "src"),
        ("prf", "prf"),
        ("accRaw", "acc"),
        ("rejRaw", "rej"),
    ):
        if value.get(normalized_name) != raw.get(raw_name):
            raise WipoMgsAdmissionError(
                normalized_name + " does not preserve the corresponding rawPayload field"
            )
    return {
        "source_term_id": source_term_id,
        "nice_class": nice_class,
        "language": language,
        "term_text": term_text,
        "seq_json": _json(value.get("seq"), "seq", 16_384),
        "src_json": _json(value.get("src"), "src", 16_384),
        "prf_json": _json(value.get("prf"), "prf", 16_384),
        "acc_raw_json": _json(value.get("accRaw"), "accRaw", 65_536),
        "rej_raw_json": _json(value.get("rejRaw"), "rejRaw", 65_536),
        "accepted_jurisdictions": accepted,
        "rejected_jurisdictions": rejected,
        "jurisdiction_statuses_json": _json(statuses, "jurisdictionStatuses", 65_536),
        "raw_payload_json": _json(raw, "rawPayload", 256_000),
        "content_sha256": content_hash,
    }


@dataclass(frozen=True)
class NormalizedAdmission:
    request_language: str
    language: str
    nice_class: int
    source_version: str | None
    source_uri: str
    evidence_canonical_uri: str
    evidence_sha256: str
    source_response_sha256: str
    observed_at: datetime
    records: tuple[dict[str, Any], ...]


def normalize(package: Mapping[str, Any]) -> NormalizedAdmission:
    if not isinstance(package, dict):
        raise WipoMgsAdmissionError("admission package must be a JSON object")
    _exact(package, _ALLOWED_PACKAGE, "package")
    required = {
        "contract_version": CONTRACT_VERSION,
        "mapping_version": MAPPING_VERSION,
        "source_owner": "MARKORBIT_KNOWLEDGE",
        "source_id": SOURCE,
    }
    for key, expected in required.items():
        if package.get(key) != expected:
            raise WipoMgsAdmissionError(key + " must equal " + expected)
    snapshot = package.get("snapshot")
    if not isinstance(snapshot, dict):
        raise WipoMgsAdmissionError("snapshot must be an object")
    _exact(snapshot, _ALLOWED_SNAPSHOT, "snapshot")
    if snapshot.get("schemaVersion") != SNAPSHOT_SCHEMA_VERSION or snapshot.get("source") != SOURCE:
        raise WipoMgsAdmissionError("snapshot schema/source is invalid")
    request_language = _text(snapshot.get("requestLanguage"), "requestLanguage", 32)
    language = _text(snapshot.get("localeCode"), "localeCode", 32)
    if not _LANGUAGE.fullmatch(request_language) or not _LANGUAGE.fullmatch(language):
        raise WipoMgsAdmissionError("snapshot language codes are invalid")
    nice_class = snapshot.get("niceClass")
    if isinstance(nice_class, bool) or not isinstance(nice_class, int) or not 1 <= nice_class <= 45:
        raise WipoMgsAdmissionError("niceClass must be an integer from 1 to 45")
    source_version = snapshot.get("sourceVersion")
    if source_version is not None:
        source_version = _text(source_version, "sourceVersion", 128)
    response_sha = _sha(snapshot.get("responseSha256"), "responseSha256")
    evidence_sha = _sha(package.get("evidence_sha256"), "evidence_sha256")
    if response_sha != evidence_sha:
        raise WipoMgsAdmissionError("evidence SHA must equal the archived source-response SHA")
    canonical = _text(package.get("evidence_canonical_uri"), "evidence_canonical_uri")
    expected_canonical = (
        "wipo-mgs://" + request_language + "/class/" + str(nice_class).zfill(2) + "/raw"
    )
    if canonical != expected_canonical:
        raise WipoMgsAdmissionError("evidence_canonical_uri does not match the snapshot scope")
    source_uri = _source_uri(package.get("source_uri"), request_language, nice_class)
    observed = _text(package.get("observed_at"), "observed_at", 48)
    try:
        observed_at = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WipoMgsAdmissionError("observed_at must be an ISO instant") from exc
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise WipoMgsAdmissionError("observed_at requires timezone")
    observed_at = observed_at.astimezone(timezone.utc)
    values = snapshot.get("records")
    count = snapshot.get("recordCount")
    if (
        not isinstance(values, list)
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 1
        or count > MAX_RECORDS
        or count != len(values)
    ):
        raise WipoMgsAdmissionError("snapshot recordCount does not match a bounded records array")
    anomalies = snapshot.get("anomalies")
    if not isinstance(anomalies, list) or len(anomalies) > MAX_RECORDS:
        raise WipoMgsAdmissionError("snapshot anomalies must be a bounded array")
    _json(anomalies, "anomalies", 4_000_000)
    records = tuple(_term(item, nice_class, language, request_language) for item in values)
    if len({item["source_term_id"] for item in records}) != len(records):
        raise WipoMgsAdmissionError("snapshot contains duplicate source term identities")
    normalized_bytes = sum(
        len(item["term_text"].encode("utf-8"))
        + len(item["raw_payload_json"].encode("utf-8"))
        + len(item["jurisdiction_statuses_json"].encode("utf-8"))
        for item in records
    )
    if normalized_bytes > MAX_NORMALIZED_PAYLOAD_BYTES:
        raise WipoMgsAdmissionError("normalized snapshot exceeds the admission payload bound")
    return NormalizedAdmission(
        request_language=request_language,
        language=language,
        nice_class=nice_class,
        source_version=source_version,
        source_uri=source_uri,
        evidence_canonical_uri=canonical,
        evidence_sha256=evidence_sha,
        source_response_sha256=response_sha,
        observed_at=observed_at,
        records=records,
    )


def _fingerprint(row: dict[str, Any], admission: NormalizedAdmission) -> str:
    material = json.dumps(
        {
            **row,
            "_request_language": admission.request_language,
            "_source_version": admission.source_version,
            "_source_response_sha256": admission.source_response_sha256,
            "_evidence_sha256": admission.evidence_sha256,
            "_evidence_canonical_uri": admission.evidence_canonical_uri,
            "_source_uri": admission.source_uri,
            "_observed_at": admission.observed_at.isoformat(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _require_hot_global_storage(client: Any) -> None:
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


def require_wipo_mgs_ready(client: Any) -> None:
    _require_hot_global_storage(client)
    current = client.query("SHOW CREATE TABLE " + TABLE).result_rows
    if len(current) != 1 or "hot_global_only" not in str(current[0][0]):
        raise RuntimeError("WIPO MGS table is missing or not bound to hot_global_only")


def install_wipo_mgs_schema(client: Any) -> None:
    _require_hot_global_storage(client)
    client.command("CREATE DATABASE IF NOT EXISTS markorbit_facts")
    client.command(DDL)
    require_wipo_mgs_ready(client)


def _persisted_sha(value: Any) -> str:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("ascii")
    return _sha(value, "persisted record_sha256")


def admit(package: Mapping[str, Any], *, client: Any) -> dict[str, Any]:
    normalized = normalize(package)
    require_wipo_mgs_ready(client)
    latest = client.query(
        "SELECT observed_at, source_response_sha256 FROM "
        + TABLE
        + " WHERE request_language = %(request_language)s AND language = %(language)s "
        "AND nice_class = %(nice_class)s ORDER BY observed_at DESC LIMIT 1",
        parameters={
            "request_language": normalized.request_language,
            "language": normalized.language,
            "nice_class": normalized.nice_class,
        },
    ).result_rows
    if latest:
        latest_at, latest_sha = latest[0]
        if getattr(latest_at, "tzinfo", None) is None:
            latest_at = latest_at.replace(tzinfo=timezone.utc)
        if (
            normalized.source_response_sha256 != _persisted_sha(latest_sha)
            and normalized.observed_at <= latest_at
        ):
            raise WipoMgsAdmissionError(
                "a different snapshot for this language/class was already admitted at or after observed_at"
            )
    query = (
        "SELECT source_term_id, record_sha256 FROM "
        + TABLE
        + " WHERE request_language = %(request_language)s AND language = %(language)s "
        "AND nice_class = %(nice_class)s AND source_response_sha256 = %(response_sha)s "
        "AND mapping_version = %(mapping)s"
    )
    params = {
        "request_language": normalized.request_language,
        "language": normalized.language,
        "nice_class": normalized.nice_class,
        "response_sha": normalized.source_response_sha256,
        "mapping": MAPPING_VERSION,
    }
    present = [
        (identity, _persisted_sha(digest))
        for identity, digest in client.query(query, parameters=params).result_rows
    ]
    seen = dict(present)
    if len(seen) != len(present):
        raise WipoMgsAdmissionError("duplicate persisted rows for one snapshot identity")
    expected = {row["source_term_id"]: _fingerprint(row, normalized) for row in normalized.records}
    if any(
        identity not in expected or expected[identity] != digest
        for identity, digest in seen.items()
    ):
        raise WipoMgsAdmissionError("conflicting mapped terms for the same source evidence")
    missing = [row for row in normalized.records if row["source_term_id"] not in seen]
    if missing:
        client.insert(
            TABLE,
            [
                [
                    SOURCE,
                    row["source_term_id"],
                    normalized.nice_class,
                    normalized.language,
                    normalized.request_language,
                    normalized.source_version,
                    MAPPING_VERSION,
                    normalized.source_response_sha256,
                    normalized.evidence_sha256,
                    normalized.evidence_canonical_uri,
                    normalized.source_uri,
                    normalized.observed_at,
                    row["term_text"],
                    row["seq_json"],
                    row["src_json"],
                    row["prf_json"],
                    row["acc_raw_json"],
                    row["rej_raw_json"],
                    row["accepted_jurisdictions"],
                    row["rejected_jurisdictions"],
                    row["jurisdiction_statuses_json"],
                    row["raw_payload_json"],
                    row["content_sha256"],
                    expected[row["source_term_id"]],
                ]
                for row in missing
            ],
            column_names=list(COLUMNS),
        )
    readback = [
        (identity, _persisted_sha(digest))
        for identity, digest in client.query(query, parameters=params).result_rows
    ]
    after = dict(readback)
    if len(after) != len(readback) or after != expected:
        raise RuntimeError("WIPO MGS read-back incomplete; receipt must not claim success")
    return {
        "contract_version": CONTRACT_VERSION,
        "outcome": "WIPO_MGS_SNAPSHOT_ADMITTED",
        "source_id": SOURCE,
        "request_language": normalized.request_language,
        "language": normalized.language,
        "nice_class": normalized.nice_class,
        "record_count": len(expected),
        "inserted_count": len(missing),
        "replayed": not missing,
        "source_response_sha256": normalized.source_response_sha256,
        "evidence_sha256": normalized.evidence_sha256,
        "storage_placement": "hot_global",
    }
