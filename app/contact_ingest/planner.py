from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any
import hashlib

from app.contact_ingest import CONTACT_INGEST_VERSION
from app.contact_ingest.mapping import detect_header_row, detect_profile, map_headers
from app.contact_ingest.models import ChannelPlan, EntityPlan, ImportPlan, PersonPlan, TablePlan
from app.contact_ingest.normalization import (
    clean_text,
    normalize_channel,
    normalize_country_code,
    normalize_credit_code,
    normalized_match_text,
    split_values,
)
from app.contact_ingest.readers import read_input


def _row_value(row: list[str], idx: int) -> str:
    return clean_text(row[idx]) if idx < len(row) else ""


def _mapped_values(row: list[str], mappings: list[Any]) -> dict[str, list[tuple[Any, str]]]:
    values: dict[str, list[tuple[Any, str]]] = defaultdict(list)
    for mapping in mappings:
        value = _row_value(row, mapping.source_index)
        if value:
            values[mapping.canonical_field].append((mapping, value))
    return values


def _first(values: dict[str, list[tuple[Any, str]]], key: str) -> str:
    items = values.get(key, [])
    return items[0][1] if items else ""


def _person_name(values: dict[str, list[tuple[Any, str]]]) -> str:
    explicit = _first(values, "CONTACT_PERSON")
    if explicit:
        return explicit
    given = _first(values, "PERSON_GIVEN_NAMES")
    surname = _first(values, "PERSON_SURNAME")
    return clean_text(" ".join(part for part in (given, surname) if part))


def _append_channel(
    target: list[ChannelPlan],
    *,
    owner_scope: str,
    channel_type: str,
    raw_value: str,
    source_column: str,
    source_row: int,
    country_code: str,
) -> None:
    actual_type, normalized = normalize_channel(channel_type, raw_value, country_code=country_code)
    if not normalized:
        return
    key = (owner_scope, actual_type, normalized)
    if any((item.owner_scope, item.channel_type, item.normalized_value) == key for item in target):
        return
    target.append(ChannelPlan(
        owner_scope=owner_scope,  # type: ignore[arg-type]
        channel_type=actual_type,
        raw_value=raw_value,
        normalized_value=normalized,
        source_column=source_column,
        source_row=source_row,
    ))


def _plan_entity(
    row: list[str],
    *,
    mappings: list[Any],
    profile: str,
    source_row: int,
    headers: list[str],
) -> EntityPlan | None:
    values = _mapped_values(row, mappings)
    firm_name = _first(values, "ENTITY_NAME")
    agent_name = _first(values, "AGENT_NAME")
    contact_name = _person_name(values)

    # Historical agent files are often person-only registers. In that case the
    # agent/person identity becomes the entity anchor so its channels can still
    # link to trademark AGENT mentions. If a firm exists, keep the firm as the
    # entity and use agent/person columns only for the contact relation.
    name = firm_name or agent_name or contact_name
    if not name:
        return None

    country_code = (
        "CN"
        if profile == "QCC_COMPANY_EXPORT"
        else normalize_country_code(_first(values, "COUNTRY"))
    )
    address = _first(values, "ADDRESS")
    person_only_agent = (
        profile == "AGENT_CONTACT_LIST"
        and not firm_name
        and bool(agent_name or contact_name)
    )
    entity = EntityPlan(
        canonical_name=name,
        normalized_name=normalized_match_text(name),
        normalized_address=normalized_match_text(address),
        country_code=country_code,
        region_code=_first(values, "PROVINCE"),
        city=_first(values, "CITY"),
        external_status=_first(values, "ENTITY_STATUS"),
        entity_type_hint="AGENT_PERSON" if person_only_agent else "",
        source_row=source_row,
        raw_record={
            header: _row_value(row, idx)
            for idx, header in enumerate(headers)
            if clean_text(header)
        },
    )

    credit_code = normalize_credit_code(_first(values, "CREDIT_CODE"))
    if credit_code:
        entity.identifiers[
            "CN_USCC" if country_code == "CN" else "REGISTRATION_ID"
        ] = credit_code
    agent_code = clean_text(_first(values, "AGENT_CODE")).upper()
    if agent_code:
        entity.identifiers[
            "CN_AGENT_CODE" if country_code == "CN" else "AGENT_CODE"
        ] = agent_code

    for field_name, language in (("FORMER_NAME", "zh"), ("ENGLISH_NAME", "en")):
        for _mapping, raw in values.get(field_name, []):
            for alias in split_values(raw):
                if (
                    normalized_match_text(alias)
                    and normalized_match_text(alias) != entity.normalized_name
                ):
                    entity.aliases.append((alias, language))

    legal_rep = _first(values, "LEGAL_REPRESENTATIVE")
    if legal_rep:
        entity.people.append(PersonPlan(
            full_name=legal_rep,
            normalized_name=normalized_match_text(legal_rep),
            relation_type="LEGAL_REPRESENTATIVE",
        ))

    # When a firm is present, a legacy agent_name is normally the professional
    # working at that firm. For person-only agent registers it is both the entity
    # anchor and contact person, which is intentional: entity identity and contact
    # profile remain separate stores.
    if not contact_name and agent_name:
        if not firm_name or normalized_match_text(agent_name) != normalized_match_text(firm_name):
            contact_name = agent_name

    contact_person: PersonPlan | None = None
    if contact_name:
        contact_person = PersonPlan(
            full_name=contact_name,
            normalized_name=normalized_match_text(contact_name),
            relation_type="ATTORNEY" if profile == "AGENT_CONTACT_LIST" else "CONTACT_PERSON",
            title=_first(values, "TITLE"),
            department=_first(values, "DEPARTMENT"),
        )
        entity.people.append(contact_person)

    for canonical, declared_type in (
        ("MOBILE", "MOBILE"),
        ("PHONE", "PHONE"),
        ("EMAIL", "EMAIL"),
        ("WEBSITE", "WEBSITE"),
        ("WHATSAPP", "WHATSAPP"),
        ("PERSON_EMAIL", "EMAIL"),
        ("PERSON_PHONE", "PHONE"),
    ):
        for mapping, raw in values.get(canonical, []):
            for item in split_values(raw):
                explicit_person = canonical.startswith("PERSON_") or mapping.owner_hint == "PERSON"
                profile_person = (
                    profile == "AGENT_CONTACT_LIST"
                    and contact_person is not None
                    and declared_type != "WEBSITE"
                )
                owner_is_person = contact_person is not None and (
                    explicit_person or profile_person
                )
                target = contact_person.channels if owner_is_person else entity.channels
                _append_channel(
                    target,
                    owner_scope="PERSON" if owner_is_person else "ENTITY",
                    channel_type=declared_type,
                    raw_value=item,
                    source_column=mapping.source_column,
                    source_row=source_row,
                    country_code=country_code,
                )

    # Critical source semantics: QCC legal-representative columns never imply
    # that unlabelled company phones/emails belong to the legal representative.
    if profile == "QCC_COMPANY_EXPORT":
        for person in entity.people:
            if person.relation_type == "LEGAL_REPRESENTATIVE":
                person.channels.clear()

    return entity


def build_plan(path: Path, *, source_name: str = "") -> ImportPlan:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    tables = read_input(path)
    table_plans: list[TablePlan] = []
    global_warnings: list[str] = []

    for table in tables:
        header_idx, header_score = detect_header_row(table)
        if header_idx < 0 or header_score < 3.0:
            global_warnings.append(
                f"Skipped {table.source_member}:{table.sheet_name or '<table>'}; "
                "no reliable contact header row detected."
            )
            continue
        headers = [clean_text(value) for value in table.rows[header_idx]]
        mappings = map_headers(headers)
        profile, profile_confidence = detect_profile(headers, mappings)
        if profile == "UNKNOWN":
            global_warnings.append(
                f"Skipped {table.source_member}:{table.sheet_name or '<table>'}; "
                "source profile is UNKNOWN."
            )
            continue

        entities: list[EntityPlan] = []
        skipped = 0
        for zero_idx, row in enumerate(
            table.rows[header_idx + 1 :], start=header_idx + 1
        ):
            source_row = zero_idx + 1
            if not any(clean_text(value) for value in row):
                continue
            entity = _plan_entity(
                row,
                mappings=mappings,
                profile=profile,
                source_row=source_row,
                headers=headers,
            )
            if entity is None:
                skipped += 1
                continue
            entities.append(entity)

        mapped_indexes = {mapping.source_index for mapping in mappings}
        unknown_columns = [
            header
            for idx, header in enumerate(headers)
            if clean_text(header) and idx not in mapped_indexes
        ]
        warnings = []
        if unknown_columns:
            preview = ", ".join(unknown_columns[:8])
            suffix = " ..." if len(unknown_columns) > 8 else ""
            warnings.append(
                f"Unmapped columns retained in raw_record: {preview}{suffix}"
            )

        table_plans.append(TablePlan(
            source_member=table.source_member,
            sheet_name=table.sheet_name,
            header_row=header_idx + 1,
            profile=profile,
            profile_confidence=profile_confidence,
            mappings=mappings,
            entities=entities,
            source_rows=len(entities) + skipped,
            skipped_rows=skipped,
            warnings=warnings,
        ))

    if not table_plans:
        raise ValueError(
            "No ingestible entity/contact tables were detected in the input file"
        )

    return ImportPlan(
        input_path=path,
        source_name=clean_text(source_name) or path.name,
        source_sha256=sha256,
        file_type=path.suffix.lower().lstrip("."),
        version=CONTACT_INGEST_VERSION,
        tables=table_plans,
        warnings=global_warnings,
    )
