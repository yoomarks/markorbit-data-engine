from __future__ import annotations

from dataclasses import dataclass
import hashlib
import uuid

from app.cn.text import clean_text, normalized_match_text


ENTITY_NAMESPACE = uuid.UUID("4a36c431-3daa-4bf9-b0d9-604423f36ac8")


@dataclass(frozen=True)
class EntityCandidate:
    entity_id: uuid.UUID
    entity_key: str
    entity_type: str
    canonical_name: str
    normalized_name: str
    normalized_address: str
    country_code: str
    region_code: str
    city: str
    confidence_score: float
    resolution_method: str


def build_entity_candidate(
    *,
    role: str,
    raw_name: str,
    raw_address: str,
    country_code: str,
    region_code: str,
    city: str,
    agent_code: str = "",
) -> EntityCandidate | None:
    normalized_name = normalized_match_text(raw_name)
    normalized_address = normalized_match_text(raw_address)
    role = clean_text(role).upper()
    country = clean_text(country_code).upper()

    if role == "AGENT" and clean_text(agent_code):
        material = f"AGENT_FIRM|CN|CODE|{clean_text(agent_code).upper()}"
        method = "EXACT_AGENT_CODE"
        confidence = 0.99
        entity_type = "AGENT_FIRM"
    elif normalized_name and normalized_address:
        material = f"TRADEMARK_PARTY|{country}|{normalized_name}|{normalized_address}"
        method = "EXACT_NAME_ADDRESS"
        confidence = 0.95
        entity_type = "TRADEMARK_PARTY"
    else:
        return None

    entity_key = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return EntityCandidate(
        entity_id=uuid.uuid5(ENTITY_NAMESPACE, material),
        entity_key=entity_key,
        entity_type=entity_type,
        canonical_name=clean_text(raw_name),
        normalized_name=normalized_name,
        normalized_address=normalized_address,
        country_code=country,
        region_code=clean_text(region_code),
        city=clean_text(city),
        confidence_score=confidence,
        resolution_method=method,
    )
