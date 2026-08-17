from __future__ import annotations

from datetime import datetime, timezone

from app.contact_ingest.person_dedupe import (
    CONTACT_PERSON_DEDUPE_VERSION,
    PersonSnapshot,
    plan_person_merges,
)


ENTITY_A = "10000000-0000-0000-0000-000000000001"
ENTITY_B = "10000000-0000-0000-0000-000000000002"


def _person(
    person_id: str,
    *,
    entity_id: str = ENTITY_A,
    name: str = "张三",
    country: str = "CN",
    channels: tuple[tuple[str, str], ...] = (),
    relation_count: int = 1,
) -> PersonSnapshot:
    return PersonSnapshot(
        entity_id=entity_id,
        person_id=person_id,
        canonical_name=name,
        normalized_name=name,
        country_code=country,
        status="CANDIDATE",
        first_seen_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        relation_count=relation_count,
        relation_types=("CONTACT_PERSON",),
        channels=channels,
    )


def test_same_company_same_name_and_email_is_candidate() -> None:
    first = _person(
        "20000000-0000-0000-0000-000000000001",
        channels=(("EMAIL", "zhangsan@example.cn"),),
        relation_count=2,
    )
    duplicate = _person(
        "20000000-0000-0000-0000-000000000002",
        channels=(("EMAIL", "zhangsan@example.cn"),),
    )

    decisions, metrics = plan_person_merges([first, duplicate])

    assert metrics["candidate_clusters"] == 1
    assert metrics["candidate_duplicates"] == 1
    assert decisions[0].status == "CANDIDATE"
    assert decisions[0].canonical_person_id == first.person_id
    assert decisions[0].duplicate_person_id == duplicate.person_id
    assert decisions[0].evidence["corroboration"] == [
        "SAME_PERSON_CHANNEL:EMAIL|zhangsan@example.cn"
    ]


def test_same_name_alone_never_merges_people() -> None:
    first = _person("20000000-0000-0000-0000-000000000011")
    second = _person("20000000-0000-0000-0000-000000000012")

    decisions, metrics = plan_person_merges([first, second])

    assert decisions == []
    assert metrics["candidate_duplicates"] == 0
    assert metrics["same_name_groups_without_shared_person_channel"] == 1


def test_same_phone_across_phone_channel_types_is_corroboration() -> None:
    first = _person(
        "20000000-0000-0000-0000-000000000021",
        channels=(("MOBILE", "+8613812345678"),),
    )
    second = _person(
        "20000000-0000-0000-0000-000000000022",
        channels=(("PHONE_UNKNOWN", "+8613812345678"),),
    )

    decisions, _metrics = plan_person_merges([first, second])

    assert len(decisions) == 1
    assert decisions[0].status == "CANDIDATE"
    assert decisions[0].evidence["corroboration"] == [
        "SAME_PERSON_CHANNEL:PHONE|+8613812345678"
    ]


def test_people_at_different_entities_never_merge() -> None:
    first = _person(
        "20000000-0000-0000-0000-000000000031",
        entity_id=ENTITY_A,
        channels=(("EMAIL", "same@example.cn"),),
    )
    second = _person(
        "20000000-0000-0000-0000-000000000032",
        entity_id=ENTITY_B,
        channels=(("EMAIL", "same@example.cn"),),
    )

    decisions, _metrics = plan_person_merges([first, second])

    assert decisions == []


def test_person_country_conflict_fails_closed() -> None:
    first = _person(
        "20000000-0000-0000-0000-000000000041",
        country="CN",
        channels=(("EMAIL", "same@example.cn"),),
    )
    second = _person(
        "20000000-0000-0000-0000-000000000042",
        country="HK",
        channels=(("EMAIL", "same@example.cn"),),
    )

    decisions, metrics = plan_person_merges([first, second])

    assert metrics["blocked_clusters"] == 1
    assert decisions[0].status == "BLOCKED"
    assert "PERSON_COUNTRY_CONFLICT" in decisions[0].reason_codes


def test_rule_version_is_explicit() -> None:
    assert CONTACT_PERSON_DEDUPE_VERSION == "CONTACT_PERSON_DEDUPE_V1"
