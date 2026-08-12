from __future__ import annotations

from pathlib import Path

from app.contact_ingest import CONTACT_INGEST_VERSION
from app.contact_ingest.planner import build_plan


def test_legacy_raw_agent_export_is_ingestible(tmp_path: Path) -> None:
    path = tmp_path / "legacy_agent.csv"
    path.write_text(
        "raw_firm_name,raw_person_name,raw_email,raw_phone,raw_website,raw_country\n"
        "Example IP LLP,Alex Example,alex@example.test,+1 202 555 0100,https://example.test,US\n",
        encoding="utf-8",
    )

    plan = build_plan(path)
    table = plan.tables[0]
    entity = table.entities[0]

    assert plan.version == CONTACT_INGEST_VERSION == "CONTACT_INGEST_V1.2"
    assert table.profile == "AGENT_CONTACT_LIST"
    assert entity.canonical_name == "Example IP LLP"
    assert entity.people[0].full_name == "Alex Example"
    assert any(channel.channel_type == "EMAIL" for channel in entity.people[0].channels)
    assert any(channel.channel_type == "WEBSITE" for channel in entity.channels)


def test_registry_surname_and_other_names_are_combined(tmp_path: Path) -> None:
    path = tmp_path / "registry.csv"
    path.write_text(
        "AGENT NO.,SURNAME,OTHER NAMES,COMPANY,BUSINESS PHONE,MOBILE PHONE,EMAIL,WEB PAGE,STATUS\n"
        "534,Othero,Janet Amollo,Triple Oklaw Advocates,+254 20 123456,0712 345678,janet@example.test,www.example.test,Active\n",
        encoding="utf-8",
    )

    plan = build_plan(path)
    entity = plan.tables[0].entities[0]

    assert plan.tables[0].profile == "AGENT_CONTACT_LIST"
    assert entity.canonical_name == "Triple Oklaw Advocates"
    assert entity.people[0].full_name == "Janet Amollo Othero"
    assert entity.identifiers["AGENT_CODE"] == "534"
    assert any(channel.channel_type == "EMAIL" for channel in entity.people[0].channels)


def test_person_only_agent_register_is_not_rejected_or_typed_as_firm(tmp_path: Path) -> None:
    path = tmp_path / "person_only.csv"
    path.write_text(
        "AGENT NO.,SURNAME,OTHER NAMES,EMAIL,COUNTRY\n"
        "736,Odhiambo,Brance Ken,law@example.test,KE\n",
        encoding="utf-8",
    )

    plan = build_plan(path)
    entity = plan.tables[0].entities[0]

    assert plan.tables[0].profile == "AGENT_CONTACT_LIST"
    assert entity.canonical_name == "Brance Ken Odhiambo"
    assert entity.entity_type_hint == "AGENT_PERSON"
    assert entity.people[0].full_name == "Brance Ken Odhiambo"


def test_historical_primary_channel_columns_are_recognized(tmp_path: Path) -> None:
    path = tmp_path / "historical.csv"
    path.write_text(
        "firm_name,canonical_name,primary_email,primary_phone,primary_website,country_code_hint\n"
        "Example Rights LLP,Alex Example,alex@example.test,+44 20 7946 0958,https://example.test,GB\n",
        encoding="utf-8",
    )

    plan = build_plan(path)
    entity = plan.tables[0].entities[0]

    assert entity.canonical_name == "Example Rights LLP"
    assert entity.people[0].full_name == "Alex Example"
    assert entity.country_code == "GB"
    assert sum(len(person.channels) for person in entity.people) >= 2
    assert any(channel.channel_type == "WEBSITE" for channel in entity.channels)


def test_numbered_legacy_channel_headers_are_recognized(tmp_path: Path) -> None:
    path = tmp_path / "numbered.csv"
    path.write_text(
        "Firm Name,Contact Person,Email1,Email2,Phone1\n"
        "Example IP,Alex Example,a@example.test,b@example.test,+1 212 555 0100\n",
        encoding="utf-8",
    )

    entity = build_plan(path).tables[0].entities[0]
    person_channels = entity.people[0].channels
    assert len([c for c in person_channels if c.channel_type == "EMAIL"]) == 2
    assert any(c.channel_type.startswith("PHONE") for c in person_channels)
