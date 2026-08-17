from __future__ import annotations

from datetime import datetime, timezone

from app.contact_ingest.entity_dedupe import (
    CONTACT_ENTITY_DEDUPE_VERSION,
    EntitySnapshot,
    plan_entity_merges,
)


def _snapshot(
    entity_id: str,
    *,
    name: str = "深圳示例科技有限公司",
    address: str = "深圳市南山区科技园一号",
    country: str = "CN",
    entity_type: str = "ORGANIZATION",
    source_primary: str = "CONTACT_INGEST",
    mention_count: int = 0,
    identifiers: tuple[tuple[str, str, str], ...] = (),
    channels: tuple[tuple[str, str], ...] = (),
    raw_record_count: int = 1,
) -> EntitySnapshot:
    return EntitySnapshot(
        entity_id=entity_id,
        entity_type=entity_type,
        canonical_name=name,
        normalized_name=name,
        normalized_address=address,
        source_country_code=country,
        inferred_country_code="",
        inferred_country_confidence=0,
        source_primary=source_primary,
        status="CANDIDATE",
        confidence_score=0.9,
        first_seen_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        mention_count=mention_count,
        raw_record_count=raw_record_count,
        identifiers=identifiers,
        channels=channels,
    )


def test_exact_name_address_collapses_contact_duplicate_into_official_entity() -> None:
    official = _snapshot(
        "00000000-0000-0000-0000-000000000001",
        source_primary="CN_OFFICIAL",
        mention_count=3,
    )
    duplicate = _snapshot("00000000-0000-0000-0000-000000000002")

    decisions, metrics = plan_entity_merges([duplicate, official])

    assert metrics["candidate_clusters"] == 1
    assert metrics["candidate_duplicates"] == 1
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.status == "CANDIDATE"
    assert decision.canonical_entity_id == official.entity_id
    assert decision.duplicate_entity_id == duplicate.entity_id
    assert any(
        evidence.startswith("EXACT_NAME_ADDRESS:")
        for evidence in decision.evidence["corroboration"]
    )


def test_same_name_and_channel_can_corroborate_when_address_is_missing() -> None:
    left = _snapshot(
        "00000000-0000-0000-0000-000000000011",
        address="",
        channels=(("EMAIL", "info@example.cn"),),
        raw_record_count=4,
    )
    right = _snapshot(
        "00000000-0000-0000-0000-000000000012",
        address="",
        channels=(("EMAIL", "info@example.cn"),),
    )

    decisions, metrics = plan_entity_merges([left, right])

    assert metrics["candidate_duplicates"] == 1
    assert decisions[0].status == "CANDIDATE"
    assert any(
        evidence.startswith("EXACT_NAME_CHANNEL:")
        for evidence in decisions[0].evidence["corroboration"]
    )


def test_same_name_alone_is_never_a_merge_signal() -> None:
    left = _snapshot(
        "00000000-0000-0000-0000-000000000021",
        address="北京市",
    )
    right = _snapshot(
        "00000000-0000-0000-0000-000000000022",
        address="上海市",
    )

    decisions, metrics = plan_entity_merges([left, right])

    assert decisions == []
    assert metrics["candidate_duplicates"] == 0


def test_conflicting_strong_china_identifiers_fail_closed() -> None:
    left = _snapshot(
        "00000000-0000-0000-0000-000000000031",
        identifiers=(("CN_USCC", "91440300AAAA", "CN"),),
    )
    right = _snapshot(
        "00000000-0000-0000-0000-000000000032",
        identifiers=(("CN_USCC", "91440300BBBB", "CN"),),
    )

    decisions, metrics = plan_entity_merges([left, right])

    assert metrics["blocked_clusters"] == 1
    assert decisions[0].status == "BLOCKED"
    assert "STRONG_IDENTIFIER_CONFLICT" in decisions[0].reason_codes


def test_country_conflict_and_person_organization_conflict_fail_closed() -> None:
    organization = _snapshot(
        "00000000-0000-0000-0000-000000000041",
        country="CN",
        entity_type="ORGANIZATION",
    )
    person = _snapshot(
        "00000000-0000-0000-0000-000000000042",
        country="HK",
        entity_type="AGENT_PERSON",
    )

    decisions, _metrics = plan_entity_merges([organization, person])

    assert decisions[0].status == "BLOCKED"
    assert "COUNTRY_CONFLICT" in decisions[0].reason_codes
    assert "PERSON_ORGANIZATION_TYPE_CONFLICT" in decisions[0].reason_codes


def test_multiple_official_entities_are_not_auto_merged() -> None:
    first = _snapshot(
        "00000000-0000-0000-0000-000000000051",
        source_primary="CN_OFFICIAL",
        mention_count=1,
    )
    second = _snapshot(
        "00000000-0000-0000-0000-000000000052",
        source_primary="US_OFFICIAL",
        mention_count=1,
    )

    decisions, _metrics = plan_entity_merges([first, second])

    assert decisions[0].status == "BLOCKED"
    assert "MULTIPLE_OFFICIAL_IDENTITIES" in decisions[0].reason_codes


def test_rule_version_is_explicit() -> None:
    assert CONTACT_ENTITY_DEDUPE_VERSION == "CONTACT_ENTITY_DEDUPE_V1"
