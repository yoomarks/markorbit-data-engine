from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from app.db import postgres_conn
from app.us.event_reference import (
    active_reference_metadata as active_event_reference,
    lookup_active_event_codes,
)
from app.us.reference_evidence import verify_payload_source_file, verify_source_evidence


EVENT_ROLE_PAYLOAD_SCHEMA = "MARKORBIT_US_EVENT_ROLE_RULESET_V1"
ALLOWED_ROLES = {
    "OFFICE_ACTION_NONFINAL_ISSUED",
    "OFFICE_ACTION_FINAL_ISSUED",
    "OFFICE_ACTION_RESPONSE_FILED",
    "NOTICE_OF_ALLOWANCE_ISSUED",
    "STATEMENT_OF_USE_FILED",
    "ITU_EXTENSION_GRANTED",
    "OPPOSITION_EXTENSION_30_GRANTED",
    "OPPOSITION_EXTENSION_90_GRANTED",
    "OPPOSITION_EXTENSION_150_GRANTED",
}
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
_RULE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_EVENT_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{0,63}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _clean(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def normalize_event_role_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != EVENT_ROLE_PAYLOAD_SCHEMA:
        raise ValueError(f"schema must be {EVENT_ROLE_PAYLOAD_SCHEMA}")
    ruleset_version = _clean(payload.get("ruleset_version"))
    if not _VERSION_RE.fullmatch(ruleset_version):
        raise ValueError("ruleset_version has an invalid format")
    event_reference_version = _clean(payload.get("event_reference_version"))
    if not event_reference_version:
        raise ValueError("event_reference_version is required")

    source = payload.get("source")
    if not isinstance(source, dict):
        raise ValueError("source must be an object")
    document_name = _clean(source.get("document_name"))
    source_sha = _clean(source.get("sha256")).lower()
    if not document_name:
        raise ValueError("source.document_name is required")
    if not _SHA_RE.fullmatch(source_sha):
        raise ValueError("source.sha256 must be a 64-character hexadecimal SHA-256")

    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError("rules must be a non-empty array")

    seen_ids: set[str] = set()
    seen_codes: set[str] = set()
    rules: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_rules, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"rules[{index}] must be an object")
        rule_id = _clean(raw.get("rule_id"))
        event_code = _clean(raw.get("event_code")).upper()
        role = _clean(raw.get("role")).upper()
        rationale = _clean(raw.get("rationale"))
        source_refs = raw.get("source_refs")
        if not _RULE_ID_RE.fullmatch(rule_id):
            raise ValueError(f"rules[{index}].rule_id has an invalid format")
        if rule_id in seen_ids:
            raise ValueError(f"duplicate rule_id: {rule_id}")
        if not _EVENT_CODE_RE.fullmatch(event_code):
            raise ValueError(f"rules[{index}].event_code has an invalid format")
        if event_code in seen_codes:
            raise ValueError(f"duplicate event_code mapping: {event_code}")
        if role not in ALLOWED_ROLES:
            raise ValueError(f"rules[{index}].role is not allowed: {role}")
        if not rationale:
            raise ValueError(f"rules[{index}].rationale is required")
        if not isinstance(source_refs, list) or not source_refs:
            raise ValueError(f"rules[{index}].source_refs must be a non-empty array")
        cleaned_refs = [_clean(value) for value in source_refs if _clean(value)]
        if not cleaned_refs:
            raise ValueError(f"rules[{index}].source_refs must contain evidence")
        seen_ids.add(rule_id)
        seen_codes.add(event_code)
        rules.append(
            {
                "rule_id": rule_id,
                "event_code": event_code,
                "role": role,
                "rationale": rationale,
                "source_refs": cleaned_refs,
            }
        )

    rules.sort(key=lambda item: item["event_code"])
    normalized = {
        "schema": EVENT_ROLE_PAYLOAD_SCHEMA,
        "ruleset_version": ruleset_version,
        "event_reference_version": event_reference_version,
        "source": {
            "document_name": document_name,
            "sha256": source_sha,
            "evidence_note": _clean(source.get("evidence_note")),
        },
        "rules": rules,
    }
    normalized["normalized_payload_sha256"] = hashlib.sha256(
        _canonical_json(normalized)
    ).hexdigest()
    return normalized


def load_event_role_payload(path: Path, *, verify_source_file: bool = True) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read event-role payload {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("event-role payload root must be an object")
    normalized = normalize_event_role_payload(payload)
    if verify_source_file:
        verify_payload_source_file(normalized, path)
    return normalized


def import_event_role_ruleset(
    payload: dict[str, Any],
    *,
    activate: bool = True,
) -> dict[str, Any]:
    normalized = normalize_event_role_payload(payload)
    event_ref = active_event_reference()
    if not event_ref:
        raise RuntimeError("No active official USPTO event reference is available")
    if event_ref["reference_version"] != normalized["event_reference_version"]:
        raise RuntimeError(
            "Event-role ruleset is not bound to the active official event reference"
        )

    codes = [rule["event_code"] for rule in normalized["rules"]]
    lookup = lookup_active_event_codes(codes)
    missing = sorted(set(codes) - set(lookup["mappings"]))
    if missing:
        raise RuntimeError(
            "Event-role ruleset contains event codes absent from the active official "
            f"reference: {', '.join(missing)}"
        )

    version = normalized["ruleset_version"]
    source = normalized["source"]
    normalized_sha = normalized["normalized_payload_sha256"]
    rules = normalized["rules"]

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext('markorbit:us:event-role-ruleset-import'))"
            )
            cur.execute(
                """
                SELECT source_document_sha256, normalized_payload_sha256,
                       rule_count, is_active
                FROM interpretation.us_event_role_ruleset_version
                WHERE ruleset_version = %s
                """,
                (version,),
            )
            existing = cur.fetchone()
            if existing:
                if (
                    str(existing["source_document_sha256"]).lower() != source["sha256"]
                    or str(existing["normalized_payload_sha256"]).lower()
                    != normalized_sha
                    or int(existing["rule_count"]) != len(rules)
                ):
                    raise RuntimeError(
                        "Event-role ruleset version already exists with different evidence"
                    )
                action = "ALREADY_IMPORTED"
                if activate and not bool(existing["is_active"]):
                    cur.execute(
                        """
                        UPDATE interpretation.us_event_role_ruleset_version
                        SET is_active = false
                        WHERE is_active = true AND ruleset_version <> %s
                        """,
                        (version,),
                    )
                    cur.execute(
                        """
                        UPDATE interpretation.us_event_role_ruleset_version
                        SET is_active = true
                        WHERE ruleset_version = %s
                        """,
                        (version,),
                    )
                    action = "ACTIVATED_EXISTING"
                conn.commit()
                return {
                    "status": action,
                    "ruleset_version": version,
                    "rule_count": len(rules),
                    "active": activate or bool(existing["is_active"]),
                }

            cur.execute(
                """
                INSERT INTO interpretation.us_event_role_ruleset_version (
                    ruleset_version, event_reference_version,
                    source_document_name, source_document_sha256,
                    normalized_payload_sha256, rule_count, is_active, evidence_note
                )
                VALUES (%s, %s, %s, %s, %s, %s, false, %s)
                """,
                (
                    version,
                    normalized["event_reference_version"],
                    source["document_name"],
                    source["sha256"],
                    normalized_sha,
                    len(rules),
                    source["evidence_note"],
                ),
            )
            for rule in rules:
                cur.execute(
                    """
                    INSERT INTO interpretation.us_event_role_rule (
                        ruleset_version, rule_id, event_code, role,
                        rationale, source_refs
                    )
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        version,
                        rule["rule_id"],
                        rule["event_code"],
                        rule["role"],
                        rule["rationale"],
                        json.dumps(rule["source_refs"]),
                    ),
                )
            if activate:
                cur.execute(
                    """
                    UPDATE interpretation.us_event_role_ruleset_version
                    SET is_active = false
                    WHERE is_active = true AND ruleset_version <> %s
                    """,
                    (version,),
                )
                cur.execute(
                    """
                    UPDATE interpretation.us_event_role_ruleset_version
                    SET is_active = true
                    WHERE ruleset_version = %s
                    """,
                    (version,),
                )
        conn.commit()

    return {
        "status": "IMPORTED",
        "ruleset_version": version,
        "rule_count": len(rules),
        "active": activate,
    }


def active_event_role_ruleset() -> dict[str, Any] | None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ruleset_version, event_reference_version,
                       source_document_name, source_document_sha256,
                       normalized_payload_sha256, rule_count,
                       imported_at, evidence_note
                FROM interpretation.us_event_role_ruleset_version
                WHERE is_active = true
                LIMIT 1
                """
            )
            row = cur.fetchone()
            return dict(row) if row else None


def load_active_event_role_map(raw_root: Path) -> dict[str, Any]:
    try:
        ruleset = active_event_role_ruleset()
    except Exception as exc:
        return {
            "status": "NOT_READY",
            "reason": "event_role_schema_not_ready",
            "error_type": type(exc).__name__,
            "ruleset": None,
            "roles": {},
        }
    if ruleset is None:
        return {
            "status": "NOT_READY",
            "reason": "active_event_role_ruleset_missing",
            "ruleset": None,
            "roles": {},
        }
    event_ref = active_event_reference()
    if not event_ref or event_ref["reference_version"] != ruleset[
        "event_reference_version"
    ]:
        return {
            "status": "FAIL",
            "reason": "active_event_reference_version_mismatch",
            "ruleset": ruleset,
            "roles": {},
        }
    ruleset_evidence = verify_source_evidence(
        ruleset,
        raw_root,
        family="interpretation",
    )
    reference_evidence = verify_source_evidence(
        event_ref,
        raw_root,
        family="event",
    )
    if ruleset_evidence["status"] != "PASS":
        return {
            "status": "FAIL" if ruleset_evidence["status"] == "FAIL" else "NOT_READY",
            "reason": "event_role_ruleset_evidence_not_verified",
            "ruleset": ruleset,
            "ruleset_evidence": ruleset_evidence,
            "reference_evidence": reference_evidence,
            "roles": {},
        }
    if reference_evidence["status"] != "PASS":
        return {
            "status": "FAIL" if reference_evidence["status"] == "FAIL" else "NOT_READY",
            "reason": "official_event_reference_evidence_not_verified",
            "ruleset": ruleset,
            "ruleset_evidence": ruleset_evidence,
            "reference_evidence": reference_evidence,
            "roles": {},
        }

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT event_code, role, rule_id, rationale, source_refs
                FROM interpretation.us_event_role_rule
                WHERE ruleset_version = %s
                ORDER BY event_code
                """,
                (ruleset["ruleset_version"],),
            )
            rows = [dict(row) for row in cur.fetchall()]
    roles = {str(row["event_code"]): row for row in rows}
    return {
        "status": "PASS",
        "reason": None,
        "ruleset": ruleset,
        "official_event_reference": event_ref,
        "ruleset_evidence": ruleset_evidence,
        "reference_evidence": reference_evidence,
        "roles": roles,
    }
