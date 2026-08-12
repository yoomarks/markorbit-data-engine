from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any
import hashlib
import re

from app.contact_ingest import CONTACT_INGEST_VERSION
from app.contact_ingest.mapping import detect_header_row, detect_profile, map_headers
from app.contact_ingest.models import (
    CaseContactPlan,
    ChannelPlan,
    EntityPlan,
    FieldMapping,
    ImportPlan,
    PersonPlan,
    TableData,
    TablePlan,
)
from app.contact_ingest.normalization import (
    clean_text,
    normalize_channel,
    normalize_country_code,
    normalize_credit_code,
    normalize_email,
    normalize_phone,
    normalize_website,
    normalized_match_text,
    split_values,
)
from app.contact_ingest.readers import read_input


_AUTO_CONTACT_LABEL_RE = re.compile(r"^\s*[^:：]{1,24}[:：]\s*")
_FIRM_HINT_RE = re.compile(
    r"(?:\b(?:co|company|ltd|limited|llp|llc|inc|corp|firm|law|legal|partners?|"
    r"associates?|attorneys?|advocates?|patent|trademark|agency|consult|berhad|sdn|cia)\b|"
    r"事务所|有限公司|公司|代理|知识产权)",
    flags=re.I,
)


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


def _auto_contact_parts(raw: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in split_values(raw):
        candidate = clean_text(_AUTO_CONTACT_LABEL_RE.sub("", item))
        if not candidate:
            continue
        if normalize_email(candidate):
            out.append(("EMAIL", candidate))
        elif normalize_website(candidate):
            out.append(("WEBSITE", candidate))
        elif normalize_phone(candidate):
            out.append(("PHONE", candidate))
    return out


def _plan_case_contact(
    row: list[str],
    *,
    mappings: list[Any],
    source_row: int,
    headers: list[str],
) -> CaseContactPlan | None:
    values = _mapped_values(row, mappings)
    application_number = clean_text(_first(values, "TRADEMARK_APPLICATION_NUMBER"))
    registration_number = clean_text(_first(values, "TRADEMARK_REGISTRATION_NUMBER"))
    if not application_number and not registration_number:
        return None

    plan = CaseContactPlan(
        application_number=application_number,
        registration_number=registration_number,
        source_row=source_row,
        raw_record={
            header: _row_value(row, idx)
            for idx, header in enumerate(headers)
            if clean_text(header)
        },
    )
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
                _append_channel(
                    plan.channels,
                    owner_scope="UNRESOLVED",
                    channel_type=declared_type,
                    raw_value=item,
                    source_column=mapping.source_column,
                    source_row=source_row,
                    country_code="",
                )
    for mapping, raw in values.get("CONTACT_VALUE", []):
        for channel_type, item in _auto_contact_parts(raw):
            _append_channel(
                plan.channels,
                owner_scope="UNRESOLVED",
                channel_type=channel_type,
                raw_value=item,
                source_column=mapping.source_column,
                source_row=source_row,
                country_code="",
            )
    return plan if plan.channels else None


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
    source_entity_name = _first(values, "SOURCE_ENTITY_NAME")
    agent_name = _first(values, "AGENT_NAME")
    contact_name = _person_name(values)
    credit_code = normalize_credit_code(_first(values, "CREDIT_CODE"))

    # Legacy QCC exports sometimes contain only 原文件导入名称. Never promote that
    # text by itself. A stable identifier (normally USCC) must accompany it.
    if not firm_name and source_entity_name and credit_code:
        firm_name = source_entity_name

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

    for mapping, raw in values.get("CONTACT_VALUE", []):
        for channel_type, item in _auto_contact_parts(raw):
            owner_is_person = contact_person is not None
            target = contact_person.channels if owner_is_person else entity.channels
            _append_channel(
                target,
                owner_scope="PERSON" if owner_is_person else "ENTITY",
                channel_type=channel_type,
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


def _append_continuation_channels(
    entity: EntityPlan,
    row: list[str],
    *,
    mappings: list[Any],
    source_row: int,
) -> bool:
    """Attach contact-only continuation rows to the immediately preceding record."""
    values = _mapped_values(row, mappings)
    if any(values.get(key) for key in (
        "ENTITY_NAME", "SOURCE_ENTITY_NAME", "AGENT_NAME", "CONTACT_PERSON",
        "PERSON_SURNAME", "PERSON_GIVEN_NAMES",
    )):
        return False
    if not values.get("CONTACT_VALUE"):
        return False

    target_person = next(
        (person for person in reversed(entity.people) if person.relation_type != "LEGAL_REPRESENTATIVE"),
        None,
    )
    added = False
    for mapping, raw in values.get("CONTACT_VALUE", []):
        for channel_type, item in _auto_contact_parts(raw):
            target = target_person.channels if target_person is not None else entity.channels
            before = len(target)
            _append_channel(
                target,
                owner_scope="PERSON" if target_person is not None else "ENTITY",
                channel_type=channel_type,
                raw_value=item,
                source_column=mapping.source_column,
                source_row=source_row,
                country_code=entity.country_code,
            )
            added = added or len(target) > before
    return added


def _looks_like_phone(value: str) -> bool:
    text = clean_text(value)
    digits = re.sub(r"\D", "", text)
    return len(digits) >= 7 and bool(normalize_phone(text))


def _infer_headerless_table(
    table: TableData,
) -> tuple[list[str], list[FieldMapping], str, float, list[list[str]]] | None:
    """Infer a narrow contact schema when a legacy sheet genuinely has no header.

    Inference requires stable channel-shaped columns and a separate textual name
    column. It never treats an email-only list as an entity list.
    """
    rows = [row for row in table.rows if any(clean_text(value) for value in row)]
    if len(rows) < 2:
        return None
    width = max(len(row) for row in rows[:200])
    if width < 2:
        return None
    sample = rows[:200]
    nonempty = [0] * width
    emails = [0] * width
    websites = [0] * width
    phones = [0] * width
    distinct: list[set[str]] = [set() for _ in range(width)]
    firm_hits = [0] * width
    lengths: list[list[int]] = [[] for _ in range(width)]

    for row in sample:
        for idx in range(width):
            value = _row_value(row, idx)
            if not value:
                continue
            nonempty[idx] += 1
            distinct[idx].add(value.casefold())
            lengths[idx].append(len(value))
            if normalize_email(value):
                emails[idx] += 1
            elif normalize_website(value):
                websites[idx] += 1
            elif _looks_like_phone(value):
                phones[idx] += 1
            if _FIRM_HINT_RE.search(value):
                firm_hits[idx] += 1

    channel_fields: dict[int, str] = {}
    for idx in range(width):
        if nonempty[idx] < 2:
            continue
        ratio_email = emails[idx] / nonempty[idx]
        ratio_web = websites[idx] / nonempty[idx]
        ratio_phone = phones[idx] / nonempty[idx]
        best = max((ratio_email, "EMAIL"), (ratio_web, "WEBSITE"), (ratio_phone, "PHONE"))
        if best[0] >= 0.55:
            channel_fields[idx] = best[1]
    if not channel_fields:
        return None

    name_candidates = []
    for idx in range(width):
        if idx in channel_fields or nonempty[idx] < max(2, int(len(sample) * 0.50)):
            continue
        avg_len = sum(lengths[idx]) / len(lengths[idx]) if lengths[idx] else 999.0
        if avg_len > 120:
            continue
        unique_ratio = len(distinct[idx]) / max(nonempty[idx], 1)
        firm_ratio = firm_hits[idx] / max(nonempty[idx], 1)
        name_candidates.append((idx, firm_ratio, unique_ratio, avg_len))
    if not name_candidates:
        return None

    firm_candidates = [item for item in name_candidates if item[1] >= 0.15]
    if firm_candidates:
        entity_col = max(
            firm_candidates,
            key=lambda item: (item[1], item[2], -item[3]),
        )[0]
    else:
        entity_col = max(name_candidates, key=lambda item: (item[2], -item[3]))[0]

    person_col: int | None = None
    if firm_candidates:
        person_candidates = [item for item in name_candidates if item[0] != entity_col and item[3] <= 80]
        if person_candidates:
            person_col = max(person_candidates, key=lambda item: (item[2], -item[3]))[0]

    headers = [f"Inferred column {idx + 1}" for idx in range(width)]
    mappings = [FieldMapping(
        source_column=headers[entity_col],
        source_index=entity_col,
        canonical_field="ENTITY_NAME",
        confidence=0.70,
    )]
    if person_col is not None:
        mappings.append(FieldMapping(
            source_column=headers[person_col],
            source_index=person_col,
            canonical_field="CONTACT_PERSON",
            confidence=0.65,
        ))
    for idx, canonical in sorted(channel_fields.items()):
        mappings.append(FieldMapping(
            source_column=headers[idx],
            source_index=idx,
            canonical_field=canonical,
            confidence=0.75,
            owner_hint="PERSON" if person_col is not None and canonical != "WEBSITE" else None,
        ))
    profile = "AGENT_CONTACT_LIST" if person_col is not None else "GENERIC_CONTACT_TABLE"
    return headers, mappings, profile, 0.60, rows


def build_plan(path: Path, *, source_name: str = "") -> ImportPlan:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    tables = read_input(path)
    table_plans: list[TablePlan] = []
    global_warnings: list[str] = []
    recognized_but_empty = 0

    for table in tables:
        header_idx, header_score = detect_header_row(table)
        inferred = False
        if header_idx < 0 or header_score < 3.0:
            inferred_schema = _infer_headerless_table(table)
            if inferred_schema is None:
                global_warnings.append(
                    f"Skipped {table.source_member}:{table.sheet_name or '<table>'}; "
                    "no reliable contact header row or safe headerless schema detected."
                )
                continue
            headers, mappings, profile, profile_confidence, data_rows = inferred_schema
            header_idx = -1
            inferred = True
        else:
            headers = [clean_text(value) for value in table.rows[header_idx]]
            mappings = map_headers(headers)
            profile, profile_confidence = detect_profile(headers, mappings)
            data_rows = table.rows[header_idx + 1 :]
            if profile == "UNKNOWN":
                global_warnings.append(
                    f"Skipped {table.source_member}:{table.sheet_name or '<table>'}; "
                    "source profile is UNKNOWN."
                )
                continue

        entities: list[EntityPlan] = []
        case_contacts: list[CaseContactPlan] = []
        skipped = 0
        source_rows = 0
        start_row = 1 if inferred else header_idx + 2
        for offset, row in enumerate(data_rows):
            if not any(clean_text(value) for value in row):
                continue
            source_rows += 1
            source_row = start_row + offset
            if profile == "CASE_CONTACT_TABLE":
                case_contact = _plan_case_contact(
                    row,
                    mappings=mappings,
                    source_row=source_row,
                    headers=headers,
                )
                if case_contact is None:
                    skipped += 1
                else:
                    case_contacts.append(case_contact)
                continue

            entity = _plan_entity(
                row,
                mappings=mappings,
                profile=profile,
                source_row=source_row,
                headers=headers,
            )
            if entity is None:
                if entities and _append_continuation_channels(
                    entities[-1], row, mappings=mappings, source_row=source_row
                ):
                    continue
                skipped += 1
                continue
            entities.append(entity)

        if not entities and not case_contacts:
            recognized_but_empty += 1
            global_warnings.append(
                f"Skipped {table.source_member}:{table.sheet_name or '<table>'}; "
                "recognized structure contains no ingestible data rows."
            )
            continue

        mapped_indexes = {mapping.source_index for mapping in mappings}
        unknown_columns = [
            header
            for idx, header in enumerate(headers)
            if clean_text(header) and idx not in mapped_indexes
        ]
        warnings = []
        if inferred:
            warnings.append(
                "Headerless legacy table inferred conservatively from repeated name/channel value patterns."
            )
        if profile == "CASE_CONTACT_TABLE":
            warnings.append(
                "Case-linked channels have no named owner in the source and are preserved as UNRESOLVED observations."
            )
        if unknown_columns and not inferred:
            preview = ", ".join(unknown_columns[:8])
            suffix = " ..." if len(unknown_columns) > 8 else ""
            warnings.append(
                f"Unmapped columns retained in raw_record: {preview}{suffix}"
            )

        table_plans.append(TablePlan(
            source_member=table.source_member,
            sheet_name=table.sheet_name,
            header_row=header_idx + 1 if header_idx >= 0 else 0,
            profile=profile,
            profile_confidence=profile_confidence,
            mappings=mappings,
            entities=entities,
            case_contacts=case_contacts,
            source_rows=source_rows,
            skipped_rows=skipped,
            warnings=warnings,
        ))

    if not table_plans:
        if recognized_but_empty:
            raise ValueError(
                "Recognizable contact table headers were found, but the file contains no ingestible data rows"
            )
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
