from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from app.db import postgres_conn
from app.us.event_reference import active_reference_metadata as active_event_reference
from app.us.reference_evidence import verify_source_evidence, verify_payload_source_file
from app.us.status_reference import active_reference_metadata as active_status_reference


RULESET_PAYLOAD_SCHEMA = "MARKORBIT_US_STATUS_RULESET_V1"
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
_RULE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_ALLOWED_CONFIDENCE = {"LOW", "MEDIUM", "HIGH"}


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _clean(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_ruleset_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != RULESET_PAYLOAD_SCHEMA:
        raise ValueError(f"schema must be {RULESET_PAYLOAD_SCHEMA}")
    version = _clean(payload.get("ruleset_version"))
    if not _VERSION_RE.fullmatch(version):
        raise ValueError("ruleset_version has an invalid format")

    status_reference_version = _clean(payload.get("status_reference_version"))
    event_reference_version = _clean(payload.get("event_reference_version"))
    if not status_reference_version:
        raise ValueError("status_reference_version is required")
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

    seen: set[str] = set()
    rules: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_rules, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"rules[{index}] must be an object")
        rule_id = _clean(raw.get("rule_id"))
        if not _RULE_ID_RE.fullmatch(rule_id):
            raise ValueError(f"rules[{index}].rule_id has an invalid format")
        if rule_id in seen:
            raise ValueError(f"duplicate rule_id: {rule_id}")
        seen.add(rule_id)

        status_codes = sorted({_clean(value) for value in raw.get("status_codes", []) if _clean(value)})
        event_codes_any = sorted({_clean(value).upper() for value in raw.get("event_codes_any", []) if _clean(value)})
        event_codes_all = sorted({_clean(value).upper() for value in raw.get("event_codes_all", []) if _clean(value)})
        if not status_codes:
            raise ValueError(f"rules[{index}].status_codes must be non-empty")

        result_label = _clean(raw.get("result_label"))
        confidence = _clean(raw.get("confidence")).upper()
        rationale = _clean(raw.get("rationale"))
        source_refs = raw.get("source_refs")
        if not result_label:
            raise ValueError(f"rules[{index}].result_label is required")
        if confidence not in _ALLOWED_CONFIDENCE:
            raise ValueError(f"rules[{index}].confidence must be LOW, MEDIUM, or HIGH")
        if not rationale:
            raise ValueError(f"rules[{index}].rationale is required")
        if not isinstance(source_refs, list) or not source_refs:
            raise ValueError(f"rules[{index}].source_refs must be a non-empty array")

        rules.append(
            {
                "rule_id": rule_id,
                "priority": int(raw.get("priority", 0)),
                "status_codes": status_codes,
                "event_codes_any": event_codes_any,
                "event_codes_all": event_codes_all,
                "result_label": result_label,
                "confidence": confidence,
                "rationale": rationale,
                "source_refs": [_clean(value) for value in source_refs if _clean(value)],
            }
        )
    rules.sort(key=lambda rule: (-rule["priority"], rule["rule_id"]))

    normalized = {
        "schema": RULESET_PAYLOAD_SCHEMA,
        "ruleset_version": version,
        "status_reference_version": status_reference_version,
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


def load_ruleset_payload(path: Path, *, verify_source_file: bool = True) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read ruleset payload {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("ruleset payload root must be an object")
    normalized = normalize_ruleset_payload(payload)
    if verify_source_file:
        verify_payload_source_file(normalized, path)
    return normalized


def import_ruleset(payload: dict[str, Any], *, activate: bool = True) -> dict[str, Any]:
    normalized = normalize_ruleset_payload(payload)
    status_ref = active_status_reference()
    event_ref = active_event_reference()
    if not status_ref or status_ref["reference_version"] != normalized["status_reference_version"]:
        raise RuntimeError("Ruleset status reference version is not the active official reference")
    if not event_ref or event_ref["reference_version"] != normalized["event_reference_version"]:
        raise RuntimeError("Ruleset event reference version is not the active official reference")

    version = normalized["ruleset_version"]
    source = normalized["source"]
    normalized_sha = normalized["normalized_payload_sha256"]
    rules = normalized["rules"]

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext('markorbit:us:status-ruleset-import'))"
            )
            cur.execute(
                """
                SELECT source_document_sha256, normalized_payload_sha256,
                       rule_count, is_active
                FROM interpretation.us_status_ruleset_version
                WHERE ruleset_version = %s
                """,
                (version,),
            )
            existing = cur.fetchone()
            if existing:
                if (
                    str(existing["source_document_sha256"]).lower() != source["sha256"]
                    or str(existing["normalized_payload_sha256"]).lower() != normalized_sha
                    or int(existing["rule_count"]) != len(rules)
                ):
                    raise RuntimeError("Ruleset version already exists with different evidence")
                if activate and not bool(existing["is_active"]):
                    cur.execute(
                        """
                        UPDATE interpretation.us_status_ruleset_version
                        SET is_active = false
                        WHERE is_active = true AND ruleset_version <> %s
                        """,
                        (version,),
                    )
                    cur.execute(
                        """
                        UPDATE interpretation.us_status_ruleset_version
                        SET is_active = true
                        WHERE ruleset_version = %s
                        """,
                        (version,),
                    )
                    action = "ACTIVATED_EXISTING"
                else:
                    action = "ALREADY_IMPORTED"
                conn.commit()
                return {"status": action, "ruleset_version": version, "rule_count": len(rules)}

            cur.execute(
                """
                INSERT INTO interpretation.us_status_ruleset_version (
                    ruleset_version, status_reference_version, event_reference_version,
                    source_document_name, source_document_sha256,
                    normalized_payload_sha256, rule_count, is_active, evidence_note
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, false, %s)
                """,
                (
                    version,
                    normalized["status_reference_version"],
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
                    INSERT INTO interpretation.us_status_rule (
                        ruleset_version, rule_id, priority, status_codes,
                        event_codes_any, event_codes_all, result_label,
                        confidence, rationale, source_refs
                    )
                    VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                            %s, %s, %s, %s::jsonb)
                    """,
                    (
                        version,
                        rule["rule_id"],
                        rule["priority"],
                        json.dumps(rule["status_codes"]),
                        json.dumps(rule["event_codes_any"]),
                        json.dumps(rule["event_codes_all"]),
                        rule["result_label"],
                        rule["confidence"],
                        rule["rationale"],
                        json.dumps(rule["source_refs"]),
                    ),
                )
            if activate:
                cur.execute(
                    """
                    UPDATE interpretation.us_status_ruleset_version
                    SET is_active = false
                    WHERE is_active = true AND ruleset_version <> %s
                    """,
                    (version,),
                )
                cur.execute(
                    """
                    UPDATE interpretation.us_status_ruleset_version
                    SET is_active = true
                    WHERE ruleset_version = %s
                    """,
                    (version,),
                )
        conn.commit()
    return {"status": "IMPORTED", "ruleset_version": version, "rule_count": len(rules)}


def active_ruleset() -> dict[str, Any] | None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ruleset_version, status_reference_version, event_reference_version,
                       source_document_name, source_document_sha256,
                       normalized_payload_sha256, rule_count, imported_at, evidence_note
                FROM interpretation.us_status_ruleset_version
                WHERE is_active = true
                LIMIT 1
                """
            )
            row = cur.fetchone()
            return dict(row) if row else None


def _unknown(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "result": "UNKNOWN",
        "confidence": "LOW",
        "reason": reason,
        "matched_rule_id": None,
        "ruleset_version": None,
        "legal_interpretation_produced": False,
        **extra,
    }


def interpret_status(
    *,
    raw_root: Path,
    status_code: str,
    event_codes: list[str] | tuple[str, ...] | set[str],
) -> dict[str, Any]:
    ruleset = active_ruleset()
    if ruleset is None:
        return _unknown("active_ruleset_missing")

    status_ref = active_status_reference()
    event_ref = active_event_reference()
    if not status_ref or status_ref["reference_version"] != ruleset["status_reference_version"]:
        return _unknown("active_status_reference_version_mismatch")
    if not event_ref or event_ref["reference_version"] != ruleset["event_reference_version"]:
        return _unknown("active_event_reference_version_mismatch")

    ruleset_evidence = verify_source_evidence(ruleset, raw_root, family="interpretation")
    status_evidence = verify_source_evidence(status_ref, raw_root, family="status")
    event_evidence = verify_source_evidence(event_ref, raw_root, family="event")
    if ruleset_evidence["status"] != "PASS":
        return _unknown("ruleset_evidence_not_verified")
    if status_evidence["status"] != "PASS" or event_evidence["status"] != "PASS":
        return _unknown("official_reference_evidence_not_verified")

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rule_id, priority, status_codes, event_codes_any, event_codes_all,
                       result_label, confidence, rationale, source_refs
                FROM interpretation.us_status_rule
                WHERE ruleset_version = %s
                ORDER BY priority DESC, rule_id
                """,
                (ruleset["ruleset_version"],),
            )
            rows = [dict(row) for row in cur.fetchall()]

    observed_events = {str(code).upper() for code in event_codes}
    matches: list[dict[str, Any]] = []
    for rule in rows:
        status_codes = set(rule["status_codes"])
        event_any = set(rule["event_codes_any"])
        event_all = set(rule["event_codes_all"])
        if status_code not in status_codes:
            continue
        if event_any and not (observed_events & event_any):
            continue
        if event_all and not event_all.issubset(observed_events):
            continue
        matches.append(rule)

    if not matches:
        return _unknown("no_matching_evidence_rule", ruleset_version=ruleset["ruleset_version"])

    top_priority = max(int(rule["priority"]) for rule in matches)
    top = [rule for rule in matches if int(rule["priority"]) == top_priority]
    outputs = {(str(rule["result_label"]), str(rule["confidence"])) for rule in top}
    if len(outputs) != 1:
        return _unknown(
            "conflicting_top_priority_rules",
            ruleset_version=ruleset["ruleset_version"],
            conflict_rule_ids=[str(rule["rule_id"]) for rule in top],
        )

    winner = top[0]
    return {
        "result": str(winner["result_label"]),
        "confidence": str(winner["confidence"]),
        "reason": "matched_evidence_rule",
        "matched_rule_id": str(winner["rule_id"]),
        "ruleset_version": str(ruleset["ruleset_version"]),
        "rationale": str(winner["rationale"]),
        "source_refs": list(winner["source_refs"]),
        "legal_interpretation_produced": True,
    }
