"""Deterministic fingerprint primitives for snapshot-first sources."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Return stable JSON representation for hashing."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint(value: Any) -> str:
    """Create a deterministic SHA-256 fingerprint."""
    payload = canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def record_fingerprint(
    entity_type: str,
    entity_id: str,
    fields: dict[str, Any],
) -> str:
    """Fingerprint a source observation record.

    This identifies source observation changes only; it does not infer
    legal events or conclusions.
    """
    return fingerprint(
        {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "fields": fields,
        }
    )
