from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import shutil
import uuid
from typing import Any

from app.cn.entity import EntityCandidate, build_entity_candidate
from app.cn.migrations import ensure_m15_schema
from app.cn.reader import iter_member_rows
from app.cn.status import classify_goods_status
from app.cn.text import (
    agent_mention_uuid,
    application_number_parts,
    case_relation_uuid,
    case_uuid,
    clean_text,
    infer_geo,
    mention_uuid,
    normalized_match_text,
    parse_class,
    parse_date,
    party_relation_key,
    party_relation_uuid,
    sha256_text,
    strip_cn_id_mask_suffix,
    truthy_cn,
)
from app.cn.zipio import iter_package_members
from app.db import clickhouse_client
from app.repository import (
    create_job_run,
    finish_job_run,
    get_package,
    record_quality_issues,
    update_package_status,
    upsert_entities,
    upsert_entity_mentions,
    upsert_package_file,
)


BATCH_SIZE = 20_000
ZERO_UUID = uuid.UUID(int=0)


STAGE_COLUMNS: dict[str, list[str]] = {
    "markorbit_facts.cn_stage_basic": [
        "package_id", "case_id", "family_root_case_id", "application_number", "case_family_root",
        "suffix_path", "filing_route", "number_family",
        "international_registration_number", "is_derived_case", "relation_id",
        "class_no", "filing_date", "filing_date_raw", "mark_name_raw",
        "mark_type_raw", "agent_code", "agent_relation_id",
        "agent_relation_key", "agent_mention_id", "agent_entity_id",
        "prelim_pub_issue", "prelim_pub_date", "prelim_pub_date_raw",
        "registration_pub_issue", "registration_pub_date",
        "registration_pub_date_raw", "exclusive_start_date",
        "exclusive_start_date_raw", "exclusive_end_date",
        "exclusive_end_date_raw", "exclusive_period", "design_description",
        "color_description", "exclusive_rights_disclaimer", "is_3d_mark",
        "is_co_application", "mark_form_raw", "geo_indication_info",
        "color_mark_flag", "is_well_known_mark", "date_quality_flags",
        "source_file", "source_start_line", "source_end_line", "row_hash",
    ],
    "markorbit_facts.cn_stage_applicant": [
        "package_id", "case_id", "application_number", "class_no",
        "relation_id", "relation_key", "mention_id", "entity_id", "raw_name",
        "normalized_name", "raw_address", "normalized_address", "country_code",
        "region_code", "city", "geo_confidence", "source_file",
        "source_start_line", "source_end_line", "row_hash",
    ],
    "markorbit_facts.cn_stage_goods": [
        "package_id", "case_id", "application_number", "class_no",
        "similar_group", "goods_sequence", "goods_name", "goods_status_raw",
        "goods_status_bucket", "goods_status_reason",
        "goods_status_mapping_version", "source_file", "source_start_line",
        "source_end_line", "row_hash",
    ],
    "markorbit_facts.cn_stage_agent": [
        "package_id", "relation_id", "mention_id", "entity_id", "agent_code",
        "agent_name", "agent_name_norm", "source_file", "source_start_line",
        "source_end_line", "row_hash",
    ],
    "markorbit_facts.cn_stage_priority": [
        "package_id", "application_number", "class_no", "priority_number",
        "priority_type", "priority_date", "priority_goods",
        "priority_country_region", "source_file", "source_start_line",
        "source_end_line", "row_hash",
    ],
    "markorbit_facts.cn_stage_madrid": [
        "package_id", "application_number", "international_registration_number",
        "international_registration_date", "international_notification_date",
        "application_language", "application_type", "international_pub_issue",
        "international_pub_date", "subsequent_designation_date",
        "basic_registration_date", "source_file", "source_start_line",
        "source_end_line", "row_hash",
    ],
    "markorbit_facts.cn_stage_coowner": [
        "package_id", "case_id", "application_number", "relation_id",
        "relation_key", "mention_id", "entity_id", "raw_name",
        "normalized_name", "raw_address", "normalized_address", "country_code",
        "region_code", "city", "geo_confidence", "source_file",
        "source_start_line", "source_end_line", "row_hash",
    ],
}


class StageBatchWriter:
    def __init__(self, batch_size: int = BATCH_SIZE):
        self.client = clickhouse_client()
        self.batch_size = batch_size
        self.buffers: dict[str, list[list[Any]]] = {
            table: [] for table in STAGE_COLUMNS
        }
        self.row_counts: Counter[str] = Counter()

    def add(self, table: str, row: list[Any]) -> None:
        self.buffers[table].append(row)
        self.row_counts[table] += 1
        if len(self.buffers[table]) >= self.batch_size:
            self.flush(table)

    def flush(self, table: str) -> None:
        rows = self.buffers[table]
        if not rows:
            return
        self.client.insert(table, rows, column_names=STAGE_COLUMNS[table])
        self.buffers[table] = []

    def close(self) -> None:
        for table in self.buffers:
            self.flush(table)


@dataclass(frozen=True)
class PartyStageResult:
    table: str
    row: list[Any]
    mention: dict[str, Any]
    entity: EntityCandidate | None


def _validated_basic_dates(record: dict[str, str]) -> tuple[dict[str, date | None], list[str]]:
    filing = parse_date(record.get("filing_date"))
    prelim = parse_date(record.get("prelim_pub_date"))
    registration = parse_date(record.get("registration_pub_date"))
    exclusive_start = parse_date(record.get("exclusive_start_date"))
    exclusive_end = parse_date(record.get("exclusive_end_date"))
    flags: list[str] = []

    if filing is None and clean_text(record.get("filing_date")):
        flags.append("INVALID_FILING_DATE")
    if prelim and filing and prelim < filing:
        flags.append("PRELIM_BEFORE_FILING")
        prelim = None
    if registration and filing and registration < filing:
        flags.append("REGISTRATION_BEFORE_FILING")
        registration = None
    if exclusive_start and filing and exclusive_start < filing:
        flags.append("TERM_START_BEFORE_FILING")
        exclusive_start = None
    if exclusive_end and filing and exclusive_end < filing:
        flags.append("TERM_END_BEFORE_FILING")
        exclusive_end = None
    if exclusive_start and exclusive_end and exclusive_end < exclusive_start:
        flags.append("TERM_END_BEFORE_TERM_START")
        exclusive_end = None

    return {
        "filing": filing,
        "prelim": prelim,
        "registration": registration,
        "exclusive_start": exclusive_start,
        "exclusive_end": exclusive_end,
    }, flags


def _party_values(
    package_uuid: uuid.UUID,
    record: dict[str, str],
    role: str,
    source_file: str,
    source_start_line: int,
    source_end_line: int,
) -> PartyStageResult | None:
    parts = application_number_parts(record.get("application_number"))
    if not parts.full:
        return None

    if role == "OWNER":
        raw_name = record.get("owner_name_cn") or record.get("owner_name_foreign") or ""
        raw_address = (
            record.get("owner_address_cn") or record.get("owner_address_foreign") or ""
        )
        address_foreign = record.get("owner_address_foreign") or ""
        table = "markorbit_facts.cn_stage_applicant"
    else:
        raw_name = (
            record.get("coowner_name_cn") or record.get("coowner_name_foreign") or ""
        )
        raw_address = (
            record.get("coowner_address_cn")
            or record.get("coowner_address_foreign")
            or ""
        )
        address_foreign = record.get("coowner_address_foreign") or ""
        table = "markorbit_facts.cn_stage_coowner"

    raw_name = strip_cn_id_mask_suffix(raw_name)
    if not raw_name:
        return None

    normalized_name = normalized_match_text(raw_name)
    normalized_address = normalized_match_text(raw_address)
    geo = infer_geo(raw_address, address_foreign)
    relation_key = party_relation_key(role, raw_name, raw_address)
    relation_id = party_relation_uuid(parts.full, role, relation_key)
    mention_id = mention_uuid(parts.full, role, raw_name, raw_address)
    entity = build_entity_candidate(
        role=role,
        raw_name=raw_name,
        raw_address=raw_address,
        country_code=geo.country_code,
        region_code=geo.region_code,
        city=geo.city,
    )
    entity_id = entity.entity_id if entity else None
    class_no = parse_class(record.get("class_no"))

    row: list[Any] = [
        package_uuid,
        case_uuid(parts.full),
        parts.full,
    ]
    if role == "OWNER":
        row.append(class_no or 0)
    row.extend(
        [
            relation_id,
            relation_key,
            mention_id,
            entity_id,
            clean_text(raw_name),
            normalized_name,
            clean_text(raw_address),
            normalized_address,
            geo.country_code,
            geo.region_code,
            geo.city,
            float(geo.confidence),
            source_file,
            source_start_line,
            source_end_line,
            sha256_text(parts.full, role, raw_name, raw_address),
        ]
    )

    mention = {
        "mention_id": mention_id,
        "source_case_key": parts.full,
        "role": role,
        "raw_name": clean_text(raw_name),
        "normalized_name": normalized_name,
        "raw_address": clean_text(raw_address),
        "normalized_address": normalized_address,
        "country_code": geo.country_code,
        "region_code": geo.region_code,
        "city": geo.city,
        "geo_confidence": geo.confidence * 100,
        "source_package_id": package_uuid,
        "source_internal_file": source_file,
        "source_start_line": source_start_line,
        "entity_id": entity_id,
        "match_status": "EXACT_MATCH" if entity else "UNRESOLVED",
        "resolution_method": entity.resolution_method if entity else "",
    }
    return PartyStageResult(table=table, row=row, mention=mention, entity=entity)


def _basic_stage_row(
    package_uuid: uuid.UUID,
    record: dict[str, str],
    source_file: str,
    source_start_line: int,
    source_end_line: int,
) -> tuple[list[Any] | None, EntityCandidate | None, dict[str, Any] | None]:
    parts = application_number_parts(record.get("application_number"))
    class_no = parse_class(record.get("class_no"))
    if not parts.full or class_no is None:
        return None, None, None

    dates, date_flags = _validated_basic_dates(record)
    agent_code = clean_text(record.get("agent_code"))
    agent_relation_key = party_relation_key("AGENT", agent_code, "", agent_code)
    agent_relation_id = (
        party_relation_uuid(parts.full, "AGENT", agent_relation_key)
        if agent_code
        else ZERO_UUID
    )
    agent_mention_id = agent_mention_uuid(agent_code) if agent_code else ZERO_UUID
    agent_entity = (
        build_entity_candidate(
            role="AGENT",
            raw_name=agent_code,
            raw_address="",
            country_code="CN",
            region_code="",
            city="",
            agent_code=agent_code,
        )
        if agent_code
        else None
    )
    relation_id = (
        case_relation_uuid(parts.family_root, parts.full)
        if parts.is_derived_case
        else ZERO_UUID
    )

    row = [
        package_uuid,
        case_uuid(parts.full),
        case_uuid(parts.family_root),
        parts.full,
        parts.family_root,
        parts.suffix_path,
        parts.filing_route,
        parts.number_family,
        parts.international_registration_number,
        int(parts.is_derived_case),
        relation_id,
        class_no,
        dates["filing"],
        clean_text(record.get("filing_date")),
        clean_text(record.get("mark_name")),
        clean_text(record.get("mark_type_raw")),
        agent_code,
        agent_relation_id,
        agent_relation_key,
        agent_mention_id,
        agent_entity.entity_id if agent_entity else None,
        clean_text(record.get("prelim_pub_issue")),
        dates["prelim"],
        clean_text(record.get("prelim_pub_date")),
        clean_text(record.get("registration_pub_issue")),
        dates["registration"],
        clean_text(record.get("registration_pub_date")),
        dates["exclusive_start"],
        clean_text(record.get("exclusive_start_date")),
        dates["exclusive_end"],
        clean_text(record.get("exclusive_end_date")),
        clean_text(record.get("exclusive_period")),
        clean_text(record.get("design_description"), preserve_newlines=True),
        clean_text(record.get("color_description"), preserve_newlines=True),
        clean_text(record.get("exclusive_rights_disclaimer"), preserve_newlines=True),
        int(truthy_cn(record.get("is_3d_mark"))),
        int(truthy_cn(record.get("is_co_application"))),
        clean_text(record.get("mark_form_raw")),
        clean_text(record.get("geo_indication_info"), preserve_newlines=True),
        clean_text(record.get("color_mark_flag")),
        int(truthy_cn(record.get("is_well_known_mark"))),
        date_flags,
        source_file,
        source_start_line,
        source_end_line,
        sha256_text(*record.values()),
    ]

    mention = None
    if agent_code:
        mention = {
            "mention_id": agent_mention_id,
            "source_case_key": f"AGENT:{agent_code}",
            "role": "AGENT",
            "raw_name": agent_code,
            "normalized_name": normalized_match_text(agent_code),
            "raw_address": "",
            "normalized_address": "",
            "country_code": "CN",
            "region_code": "",
            "city": "",
            "geo_confidence": 40,
            "source_package_id": package_uuid,
            "source_internal_file": source_file,
            "source_start_line": source_start_line,
            "entity_id": agent_entity.entity_id if agent_entity else None,
            "match_status": "EXACT_MATCH" if agent_entity else "UNRESOLVED",
            "resolution_method": agent_entity.resolution_method if agent_entity else "",
        }
    return row, agent_entity, mention


def _other_stage_row(
    role: str,
    package_uuid: uuid.UUID,
    record: dict[str, str],
    source_file: str,
    source_start_line: int,
    source_end_line: int,
) -> tuple[str, list[Any], EntityCandidate | None, dict[str, Any] | None] | None:
    parts = application_number_parts(record.get("application_number"))
    if role != "agent" and not parts.full:
        return None

    if role == "goods":
        class_no = parse_class(record.get("class_no"))
        if class_no is None:
            return None
        status = classify_goods_status(record.get("goods_status_raw"))
        return "markorbit_facts.cn_stage_goods", [
            package_uuid,
            case_uuid(parts.full),
            parts.full,
            class_no,
            clean_text(record.get("similar_group")),
            clean_text(record.get("goods_sequence")),
            clean_text(record.get("goods_name"), preserve_newlines=True),
            status.raw,
            status.bucket,
            status.reason,
            status.mapping_version,
            source_file,
            source_start_line,
            source_end_line,
            sha256_text(*record.values()),
        ], None, None

    if role == "priority":
        class_no = parse_class(record.get("class_no"))
        if class_no is None:
            return None
        return "markorbit_facts.cn_stage_priority", [
            package_uuid,
            parts.full,
            class_no,
            clean_text(record.get("priority_number")),
            clean_text(record.get("priority_type")),
            parse_date(record.get("priority_date")),
            clean_text(record.get("priority_goods"), preserve_newlines=True),
            clean_text(record.get("priority_country_region")),
            source_file,
            source_start_line,
            source_end_line,
            sha256_text(*record.values()),
        ], None, None

    if role == "madrid":
        return "markorbit_facts.cn_stage_madrid", [
            package_uuid,
            parts.full,
            clean_text(record.get("international_registration_number")),
            parse_date(record.get("international_registration_date")),
            parse_date(record.get("international_notification_date")),
            clean_text(record.get("application_language")),
            clean_text(record.get("application_type")),
            clean_text(record.get("international_pub_issue")),
            parse_date(record.get("international_pub_date")),
            parse_date(record.get("subsequent_designation_date")),
            parse_date(record.get("basic_registration_date")),
            source_file,
            source_start_line,
            source_end_line,
            sha256_text(*record.values()),
        ], None, None

    if role == "agent":
        agent_code = clean_text(record.get("agent_code"))
        agent_name = clean_text(record.get("agent_name"))
        if not agent_code or not agent_name:
            return None
        mention_id = agent_mention_uuid(agent_code)
        entity = build_entity_candidate(
            role="AGENT",
            raw_name=agent_name,
            raw_address="",
            country_code="CN",
            region_code="",
            city="",
            agent_code=agent_code,
        )
        relation_key = party_relation_key("AGENT", agent_name, "", agent_code)
        relation_id = party_relation_uuid(f"AGENT:{agent_code}", "AGENT", relation_key)
        mention = {
            "mention_id": mention_id,
            "source_case_key": f"AGENT:{agent_code}",
            "role": "AGENT",
            "raw_name": agent_name,
            "normalized_name": normalized_match_text(agent_name),
            "raw_address": "",
            "normalized_address": "",
            "country_code": "CN",
            "region_code": "",
            "city": "",
            "geo_confidence": 90,
            "source_package_id": package_uuid,
            "source_internal_file": source_file,
            "source_start_line": source_start_line,
            "entity_id": entity.entity_id if entity else None,
            "match_status": "EXACT_MATCH" if entity else "UNRESOLVED",
            "resolution_method": entity.resolution_method if entity else "",
        }
        return "markorbit_facts.cn_stage_agent", [
            package_uuid,
            relation_id,
            mention_id,
            entity.entity_id if entity else None,
            agent_code,
            agent_name,
            normalized_match_text(agent_name),
            source_file,
            source_start_line,
            source_end_line,
            sha256_text(agent_code, agent_name),
        ], entity, mention

    return None


def _ch_nullable_date(value: date | None) -> str:
    if value is None:
        return "CAST(NULL, 'Nullable(Date32)')"
    return f"toDate32('{value.isoformat()}')"


def _case_aggregate_sql(package: str) -> str:
    return f"""
        SELECT
            aggregated.*,
            hex(SHA256(concat(
                application_number, '|', case_family_root, '|', suffix_path, '|',
                filing_route, '|', international_registration_number, '|',
                mark_name_raw, '|', mark_type_raw, '|', mark_form_raw, '|',
                ifNull(toString(filing_date), ''), '|',
                ifNull(toString(prelim_pub_date), ''), '|', prelim_pub_issue, '|',
                ifNull(toString(registration_pub_date), ''), '|', registration_pub_issue, '|',
                ifNull(toString(exclusive_start_date), ''), '|',
                ifNull(toString(exclusive_end_date), ''), '|', exclusive_period, '|',
                design_description, '|', color_description, '|',
                exclusive_rights_disclaimer, '|', toString(is_3d_mark), '|',
                toString(is_co_application), '|', geo_indication_info, '|',
                color_mark_flag, '|', toString(is_well_known_mark), '|', agent_code, '|',
                arrayStringConcat(arrayMap(x -> toString(x), classes), ','), '|',
                arrayStringConcat(data_quality_flags, ',')
            ))) AS record_hash
        FROM
        (
            SELECT
                case_id,
                argMax(family_root_case_id, toUInt64(stage_source_start_line)) AS family_root_case_id,
                application_number,
                argMax(case_family_root, toUInt64(stage_source_start_line)) AS case_family_root,
                argMax(suffix_path, toUInt64(stage_source_start_line)) AS suffix_path,
                argMax(filing_route, toUInt64(stage_source_start_line)) AS filing_route,
                argMax(number_family, toUInt64(stage_source_start_line)) AS number_family,
                argMax(international_registration_number, toUInt64(stage_source_start_line))
                    AS international_registration_number,
                max(is_derived_case) AS is_derived_case,
                argMax(relation_id, toUInt64(stage_source_start_line)) AS relation_id,
                argMax(mark_name_raw, toUInt64(stage_source_start_line)) AS mark_name_raw,
                argMax(mark_type_raw, toUInt64(stage_source_start_line)) AS mark_type_raw,
                argMax(mark_form_raw, toUInt64(stage_source_start_line)) AS mark_form_raw,
                argMax(agent_code, toUInt64(stage_source_start_line)) AS agent_code,
                min(filing_date) AS filing_date,
                argMax(prelim_pub_date, toUInt64(stage_source_start_line)) AS prelim_pub_date,
                argMax(prelim_pub_issue, toUInt64(stage_source_start_line)) AS prelim_pub_issue,
                argMax(registration_pub_date, toUInt64(stage_source_start_line)) AS registration_pub_date,
                argMax(registration_pub_issue, toUInt64(stage_source_start_line)) AS registration_pub_issue,
                argMax(exclusive_start_date, toUInt64(stage_source_start_line)) AS exclusive_start_date,
                argMax(exclusive_end_date, toUInt64(stage_source_start_line)) AS exclusive_end_date,
                argMax(exclusive_period, toUInt64(stage_source_start_line)) AS exclusive_period,
                argMax(design_description, toUInt64(stage_source_start_line)) AS design_description,
                argMax(color_description, toUInt64(stage_source_start_line)) AS color_description,
                argMax(exclusive_rights_disclaimer, toUInt64(stage_source_start_line))
                    AS exclusive_rights_disclaimer,
                max(is_3d_mark) AS is_3d_mark,
                max(is_co_application) AS is_co_application,
                argMax(geo_indication_info, toUInt64(stage_source_start_line)) AS geo_indication_info,
                argMax(color_mark_flag, toUInt64(stage_source_start_line)) AS color_mark_flag,
                max(is_well_known_mark) AS is_well_known_mark,
                arraySort(groupUniqArray(class_no)) AS classes,
                arraySort(arrayDistinct(arrayFlatten(groupArray(date_quality_flags))))
                    AS data_quality_flags,
                argMin(source_file, toUInt64(stage_source_start_line)) AS source_file,
                min(toUInt64(stage_source_start_line)) AS source_first_line,
                max(toUInt64(stage_source_end_line)) AS source_last_line,
                hex(SHA256(arrayStringConcat(arraySort(groupArray(toString(row_hash))), '|')))
                    AS source_row_hash
            FROM (
                SELECT
                    package_id, case_id, family_root_case_id, application_number,
                    case_family_root, suffix_path, filing_route, number_family,
                    international_registration_number, is_derived_case, relation_id,
                    class_no, filing_date, filing_date_raw, mark_name_raw,
                    mark_type_raw, agent_code, agent_relation_id,
                    agent_relation_key, agent_mention_id, agent_entity_id,
                    prelim_pub_issue, prelim_pub_date, prelim_pub_date_raw,
                    registration_pub_issue, registration_pub_date,
                    registration_pub_date_raw, exclusive_start_date,
                    exclusive_start_date_raw, exclusive_end_date,
                    exclusive_end_date_raw, exclusive_period, design_description,
                    color_description, exclusive_rights_disclaimer, is_3d_mark,
                    is_co_application, mark_form_raw, geo_indication_info,
                    color_mark_flag, is_well_known_mark, date_quality_flags,
                    source_file, source_start_line AS stage_source_start_line,
                    source_end_line AS stage_source_end_line, row_hash
                FROM markorbit_facts.cn_stage_basic
            ) AS stage_basic
            WHERE package_id = toUUID('{package}')
            GROUP BY case_id, application_number
        ) AS aggregated
    """


def _scope_aggregate_sql(package: str) -> str:
    return f"""
        SELECT
            aggregated.*,
            if(unmapped_status_item_count = 0,
               toNullable(toUInt32(interpreted_active_item_count)),
               CAST(NULL, 'Nullable(UInt32)')) AS effective_item_count,
            if(unmapped_status_item_count = 0, 1, 0) AS interpretation_complete,
            if(unmapped_status_item_count = 0, 'COMPLETE', 'UNMAPPED_STATUS_CODES_PRESENT')
                AS scope_interpretation_status,
            hex(SHA256(concat(
                application_number, '|', toString(class_no), '|', goods_items_compact, '|',
                arrayStringConcat(similar_groups, ','), '|',
                arrayStringConcat(observed_status_codes, ',')
            ))) AS scope_hash,
            if(unmapped_status_item_count = 0,
               hex(SHA256(concat(
                   application_number, '|', toString(class_no), '|',
                   toString(interpreted_active_item_count), '|',
                   arrayStringConcat(active_similar_groups, ',')
               ))), '') AS effective_scope_hash
        FROM
        (
            SELECT
                case_id,
                application_number,
                class_no,
                toUInt32(count()) AS source_item_count,
                toUInt32(countIf(goods_status_bucket = 'ACTIVE')) AS interpreted_active_item_count,
                toUInt32(countIf(goods_status_bucket = 'INACTIVE')) AS interpreted_inactive_item_count,
                toUInt32(countIf(goods_status_bucket = 'UNKNOWN')) AS unmapped_status_item_count,
                argMax(goods_status_mapping_version, toUInt64(stage_source_start_line))
                    AS goods_status_mapping_version,
                arraySort(groupUniqArray(if(goods_status_raw = '', '<BLANK>', goods_status_raw)))
                    AS observed_status_codes,
                toJSONString(arraySort(groupArray((
                    goods_sequence, similar_group, goods_name, goods_status_raw,
                    goods_status_bucket, goods_status_reason
                )))) AS goods_items_compact,
                arrayStringConcat(arraySort(groupArray(goods_name)), ' ') AS goods_text_search,
                arraySort(arrayFilter(x -> x != '', groupUniqArray(similar_group)))
                    AS similar_groups,
                arraySort(arrayFilter(
                    x -> x != '', groupUniqArrayIf(similar_group, goods_status_bucket = 'ACTIVE')
                )) AS active_similar_groups,
                argMin(source_file, toUInt64(stage_source_start_line)) AS source_file,
                min(toUInt64(stage_source_start_line)) AS source_first_line,
                max(toUInt64(stage_source_end_line)) AS source_last_line,
                hex(SHA256(arrayStringConcat(arraySort(groupArray(toString(row_hash))), '|')))
                    AS source_row_hash
            FROM (
                SELECT
                    package_id, case_id, application_number, class_no,
                    similar_group, goods_sequence, goods_name, goods_status_raw,
                    goods_status_bucket, goods_status_reason,
                    goods_status_mapping_version, source_file,
                    source_start_line AS stage_source_start_line,
                    source_end_line AS stage_source_end_line,
                    row_hash
                FROM markorbit_facts.cn_stage_goods
            ) AS stage_goods
            WHERE package_id = toUUID('{package}')
            GROUP BY case_id, application_number, class_no
        ) AS aggregated
    """


def _party_aggregate_sql(package: str) -> str:
    return f"""
        SELECT
            party.*,
            hex(SHA256(concat(
                application_number, '|', role, '|', relation_key, '|', raw_name, '|',
                raw_address, '|', arrayStringConcat(arrayMap(x -> toString(x), class_nos), ',')
            ))) AS record_hash
        FROM
        (
            SELECT
                case_id,
                application_number,
                'OWNER' AS role,
                relation_id,
                relation_key,
                argMax(mention_id, toUInt64(stage_source_start_line)) AS mention_id,
                argMax(entity_id, toUInt64(stage_source_start_line)) AS entity_id,
                '' AS agent_code,
                argMax(raw_name, toUInt64(stage_source_start_line)) AS raw_name,
                argMax(normalized_name, toUInt64(stage_source_start_line)) AS normalized_name,
                argMax(raw_address, toUInt64(stage_source_start_line)) AS raw_address,
                argMax(normalized_address, toUInt64(stage_source_start_line)) AS normalized_address,
                argMax(country_code, toUInt64(stage_source_start_line)) AS country_code,
                argMax(region_code, toUInt64(stage_source_start_line)) AS region_code,
                argMax(city, toUInt64(stage_source_start_line)) AS city,
                arraySort(groupUniqArray(class_no)) AS class_nos,
                max(geo_confidence) * 100 AS confidence_score,
                argMin(source_file, toUInt64(stage_source_start_line)) AS source_file,
                min(toUInt64(stage_source_start_line)) AS source_first_line,
                max(toUInt64(stage_source_end_line)) AS source_last_line,
                hex(SHA256(arrayStringConcat(arraySort(groupArray(toString(row_hash))), '|')))
                    AS source_row_hash
            FROM (
                SELECT
                    package_id, case_id, application_number, class_no, relation_id,
                    relation_key, mention_id, entity_id, raw_name,
                    normalized_name, raw_address, normalized_address, country_code,
                    region_code, city, geo_confidence, source_file,
                    source_start_line AS stage_source_start_line,
                    source_end_line AS stage_source_end_line,
                    row_hash
                FROM markorbit_facts.cn_stage_applicant
            ) AS applicant_source
            WHERE package_id = toUUID('{package}')
            GROUP BY case_id, application_number, relation_id, relation_key

            UNION ALL

            SELECT
                co.case_id,
                co.application_number,
                'CO_OWNER' AS role,
                co.relation_id,
                co.relation_key,
                argMax(co.mention_id, toUInt64(co.stage_source_start_line)) AS mention_id,
                argMax(co.entity_id, toUInt64(co.stage_source_start_line)) AS entity_id,
                '' AS agent_code,
                argMax(co.raw_name, toUInt64(co.stage_source_start_line)) AS raw_name,
                argMax(co.normalized_name, toUInt64(co.stage_source_start_line)) AS normalized_name,
                argMax(co.raw_address, toUInt64(co.stage_source_start_line)) AS raw_address,
                argMax(co.normalized_address, toUInt64(co.stage_source_start_line)) AS normalized_address,
                argMax(co.country_code, toUInt64(co.stage_source_start_line)) AS country_code,
                argMax(co.region_code, toUInt64(co.stage_source_start_line)) AS region_code,
                argMax(co.city, toUInt64(co.stage_source_start_line)) AS city,
                arraySort(groupUniqArray(b.class_no)) AS class_nos,
                max(co.geo_confidence) * 100 AS confidence_score,
                argMin(co.source_file, toUInt64(co.stage_source_start_line)) AS source_file,
                min(toUInt64(co.stage_source_start_line)) AS source_first_line,
                max(toUInt64(co.stage_source_end_line)) AS source_last_line,
                hex(SHA256(arrayStringConcat(arraySort(groupArray(toString(co.row_hash))), '|')))
                    AS source_row_hash
            FROM (
                SELECT
                    package_id, case_id, application_number, relation_id,
                    relation_key, mention_id, entity_id, raw_name,
                    normalized_name, raw_address, normalized_address, country_code,
                    region_code, city, geo_confidence, source_file,
                    source_start_line AS stage_source_start_line,
                    source_end_line AS stage_source_end_line,
                    row_hash
                FROM markorbit_facts.cn_stage_coowner
            ) AS co
            LEFT JOIN (
                SELECT
                    package_id, application_number, class_no, source_file,
                    source_start_line AS stage_source_start_line,
                    source_end_line AS stage_source_end_line
                FROM markorbit_facts.cn_stage_basic
            ) AS b
              ON b.package_id = co.package_id
             AND b.application_number = co.application_number
            WHERE co.package_id = toUUID('{package}')
            GROUP BY co.case_id, co.application_number, co.relation_id, co.relation_key

            UNION ALL

            SELECT
                b.case_id,
                b.application_number,
                'AGENT' AS role,
                b.agent_relation_id AS relation_id,
                b.agent_relation_key AS relation_key,
                b.agent_mention_id AS mention_id,
                b.agent_entity_id AS entity_id,
                b.agent_code AS agent_code,
                if(argMax(a.agent_name, toUInt64(a.stage_source_start_line)) = '', b.agent_code,
                   argMax(a.agent_name, toUInt64(a.stage_source_start_line))) AS raw_name,
                if(argMax(a.agent_name_norm, toUInt64(a.stage_source_start_line)) = '', lowerUTF8(b.agent_code),
                   argMax(a.agent_name_norm, toUInt64(a.stage_source_start_line))) AS normalized_name,
                '' AS raw_address,
                '' AS normalized_address,
                'CN' AS country_code,
                '' AS region_code,
                '' AS city,
                arraySort(groupUniqArray(b.class_no)) AS class_nos,
                if(argMax(a.agent_name, toUInt64(a.stage_source_start_line)) = '', 40, 90) AS confidence_score,
                argMin(b.source_file, toUInt64(b.stage_source_start_line)) AS source_file,
                min(toUInt64(b.stage_source_start_line)) AS source_first_line,
                max(toUInt64(b.stage_source_end_line)) AS source_last_line,
                hex(SHA256(arrayStringConcat(arraySort(groupArray(toString(b.row_hash))), '|')))
                    AS source_row_hash
            FROM (
                SELECT
                    package_id, case_id, application_number, class_no,
                    agent_code, agent_relation_id, agent_relation_key,
                    agent_mention_id, agent_entity_id, source_file,
                    source_start_line AS stage_source_start_line,
                    source_end_line AS stage_source_end_line,
                    row_hash
                FROM markorbit_facts.cn_stage_basic
            ) AS b
            LEFT JOIN (
                SELECT
                    package_id, agent_code, agent_name, agent_name_norm,
                    source_file, source_start_line AS stage_source_start_line,
                    source_end_line AS stage_source_end_line
                FROM markorbit_facts.cn_stage_agent
            ) AS a
              ON a.package_id = b.package_id
             AND a.agent_code = b.agent_code
            WHERE b.package_id = toUUID('{package}')
              AND b.agent_code != ''
            GROUP BY b.case_id, b.application_number, b.agent_code,
                     b.agent_relation_id, b.agent_relation_key,
                     b.agent_mention_id, b.agent_entity_id
        ) AS party
    """


def _party_touched_sql(party_agg: str) -> str:
    """Collapse relation-level party facts to case+role touch lineage.

    Output aliases deliberately use touched_* names. ClickHouse 24.8 resolves aliases
    aggressively across nested queries; reusing source_first_line/source_last_line here
    can make a legal aggregate-over-subquery look like a nested aggregate.
    """
    return f"""
        SELECT
            p.application_number,
            p.role,
            argMin(p.source_file, p.source_first_line) AS touched_source_file,
            min(p.source_first_line) AS touched_first_line,
            max(p.source_last_line) AS touched_last_line,
            argMin(p.source_row_hash, p.source_first_line) AS touched_source_row_hash
        FROM ({party_agg}) AS p
        GROUP BY p.application_number, p.role
    """


def _insert_case_events(
    client: Any,
    package: str,
    package_kind: str,
    source_rank: int,
    case_agg: str,
) -> None:
    common_join = f"""
        FROM ({case_agg}) AS incoming
        LEFT JOIN markorbit_facts.cn_case_current AS cur FINAL
          ON cur.application_number = incoming.application_number
        WHERE (cur.application_number = '' OR cur.source_rank < {source_rank})
    """

    event_specs = [
        (
            "if(cur.application_number = '', 'APPLICATION_OBSERVED', 'CASE_FACTS_CHANGED_OBSERVED')",
            "incoming.filing_date",
            "CASE",
            "record_hash",
            "cur.application_number = '' OR cur.record_hash != incoming.record_hash",
            "if(cur.application_number = '', '', cur.record_hash)",
            "incoming.record_hash",
        ),
        (
            "'PRELIMINARY_PUBLICATION_OBSERVED'",
            "incoming.prelim_pub_date",
            "CASE",
            "preliminary_publication",
            "incoming.prelim_pub_date IS NOT NULL AND (cur.application_number = '' OR concat(ifNull(toString(cur.prelim_pub_date), ''), '|', cur.prelim_pub_issue) != concat(ifNull(toString(incoming.prelim_pub_date), ''), '|', incoming.prelim_pub_issue))",
            "if(cur.application_number = '', '', toJSONString(map('date', ifNull(toString(cur.prelim_pub_date), ''), 'issue', cur.prelim_pub_issue)))",
            "toJSONString(map('date', ifNull(toString(incoming.prelim_pub_date), ''), 'issue', incoming.prelim_pub_issue))",
        ),
        (
            "'REGISTRATION_PUBLICATION_OBSERVED'",
            "incoming.registration_pub_date",
            "CASE",
            "registration_publication",
            "incoming.registration_pub_date IS NOT NULL AND (cur.application_number = '' OR concat(ifNull(toString(cur.registration_pub_date), ''), '|', cur.registration_pub_issue) != concat(ifNull(toString(incoming.registration_pub_date), ''), '|', incoming.registration_pub_issue))",
            "if(cur.application_number = '', '', toJSONString(map('date', ifNull(toString(cur.registration_pub_date), ''), 'issue', cur.registration_pub_issue)))",
            "toJSONString(map('date', ifNull(toString(incoming.registration_pub_date), ''), 'issue', incoming.registration_pub_issue))",
        ),
        (
            "if(cur.valid_until IS NOT NULL AND incoming.exclusive_end_date > cur.valid_until, 'TERM_EXTENDED_OBSERVED', 'EXCLUSIVE_TERM_OBSERVED')",
            "incoming.exclusive_end_date",
            "CASE",
            "exclusive_term",
            "(incoming.exclusive_start_date IS NOT NULL OR incoming.exclusive_end_date IS NOT NULL) AND (cur.application_number = '' OR concat(ifNull(toString(cur.valid_from), ''), '|', ifNull(toString(cur.valid_until), '')) != concat(ifNull(toString(incoming.exclusive_start_date), ''), '|', ifNull(toString(incoming.exclusive_end_date), '')))",
            "if(cur.application_number = '', '', toJSONString(map('from', ifNull(toString(cur.valid_from), ''), 'until', ifNull(toString(cur.valid_until), ''))))",
            "toJSONString(map('from', ifNull(toString(incoming.exclusive_start_date), ''), 'until', ifNull(toString(incoming.exclusive_end_date), ''), 'raw', incoming.exclusive_period))",
        ),
        (
            "'MARK_NAME_CHANGED_OBSERVED'",
            "CAST(NULL, 'Nullable(Date32)')",
            "CASE",
            "mark_name",
            "cur.application_number != '' AND cur.mark_name_raw != incoming.mark_name_raw",
            "cur.mark_name_raw",
            "incoming.mark_name_raw",
        ),
        (
            "'AGENT_CODE_CHANGED_OBSERVED'",
            "CAST(NULL, 'Nullable(Date32)')",
            "PARTY",
            "agent_code",
            "cur.application_number != '' AND cur.agent_code != incoming.agent_code",
            "cur.agent_code",
            "incoming.agent_code",
        ),
    ]

    for event_type, event_date, scope, field_name, condition, old_value, new_value in event_specs:
        client.command(f"""
            INSERT INTO markorbit_facts.cn_observed_event
            SELECT
                generateUUIDv4(), incoming.case_id, incoming.application_number,
                {event_type}, {event_date}, now64(3), '{scope}',
                CAST(NULL, 'Nullable(UInt8)'), '{field_name}',
                {old_value}, {new_value}, 'OFFICIAL_FACT_OBSERVATION',
                'NOT_DETERMINED', 1.0, toUUID('{package}'), '{package_kind}',
                incoming.source_file, incoming.source_first_line, incoming.source_last_line,
                incoming.source_row_hash, {source_rank},
                hex(SHA256(concat(
                    incoming.application_number, '|', {event_type}, '|', '{field_name}', '|',
                    {old_value}, '|', {new_value}, '|', toString({source_rank})
                )))
            {common_join}
              AND ({condition})
        """)


def _publish(
    package_uuid: uuid.UUID,
    package_meta: dict[str, Any],
) -> dict[str, int]:
    client = clickhouse_client()
    package = str(package_uuid)
    package_kind = str(package_meta["package_kind"])
    source_rank = int(package_meta["source_rank"])
    effective_date = package_meta.get("source_period_end")
    effective_expr = _ch_nullable_date(effective_date)
    case_agg = _case_aggregate_sql(package)
    scope_agg = _scope_aggregate_sql(package)
    party_agg = _party_aggregate_sql(package)
    party_touched = _party_touched_sql(party_agg)

    _insert_case_events(client, package, package_kind, source_rank, case_agg)

    client.command(f"""
        INSERT INTO markorbit_facts.cn_observed_event
        SELECT
            generateUUIDv4(), incoming.case_id, incoming.application_number,
            if(cur.application_number = '', 'GOODS_SCOPE_OBSERVED',
               'GOODS_SCOPE_CHANGED_OBSERVED'),
            CAST(NULL, 'Nullable(Date32)'), now64(3), 'GOODS',
            toNullable(incoming.class_no), 'goods_scope',
            if(cur.application_number = '', '', toJSONString(map(
                'source_item_count', toString(cur.source_item_count),
                'active', toString(cur.interpreted_active_item_count),
                'inactive', toString(cur.interpreted_inactive_item_count),
                'unknown', toString(cur.unmapped_status_item_count),
                'scope_hash', cur.scope_hash
            ))),
            toJSONString(map(
                'source_item_count', toString(incoming.source_item_count),
                'active', toString(incoming.interpreted_active_item_count),
                'inactive', toString(incoming.interpreted_inactive_item_count),
                'unknown', toString(incoming.unmapped_status_item_count),
                'scope_hash', incoming.scope_hash,
                'mapping_version', incoming.goods_status_mapping_version
            )),
            'OFFICIAL_FACT_OBSERVATION', 'NOT_DETERMINED', 1.0,
            toUUID('{package}'), '{package_kind}', incoming.source_file,
            incoming.source_first_line, incoming.source_last_line,
            incoming.source_row_hash, {source_rank},
            hex(SHA256(concat(
                incoming.application_number, '|GOODS|', toString(incoming.class_no), '|',
                if(cur.application_number = '', '', cur.scope_hash), '|',
                incoming.scope_hash, '|', toString({source_rank})
            )))
        FROM ({scope_agg}) AS incoming
        LEFT JOIN markorbit_facts.cn_case_scope_current AS cur FINAL
          ON cur.application_number = incoming.application_number
         AND cur.class_no = incoming.class_no
        WHERE (cur.application_number = '' OR cur.source_rank < {source_rank})
          AND (cur.application_number = '' OR cur.scope_hash != incoming.scope_hash)
    """)

    # Observe party replacements before changing current relations.
    client.command(f"""
        INSERT INTO markorbit_facts.cn_observed_event
        SELECT
            generateUUIDv4(), cur.case_id, cur.application_number,
            concat(cur.role, '_RELATION_SUPERSEDED_OBSERVED'),
            {effective_expr}, now64(3), 'PARTY', CAST(NULL, 'Nullable(UInt8)'),
            lowerUTF8(cur.role),
            toJSONString(map('name', cur.raw_name, 'address', cur.raw_address,
                             'relation_key', cur.relation_key)),
            '', 'OFFICIAL_DATA_RELATION_REPLACEMENT', 'NOT_DETERMINED', 0.95,
            toUUID('{package}'), '{package_kind}', touched.touched_source_file,
            touched.touched_first_line, touched.touched_last_line,
            touched.touched_source_row_hash, {source_rank},
            hex(SHA256(concat(
                cur.application_number, '|', cur.role, '|SUPERSEDED|',
                cur.relation_key, '|', toString({source_rank})
            )))
        FROM markorbit_facts.cn_case_party_current AS cur FINAL
        INNER JOIN ({party_touched}) AS touched
          ON touched.application_number = cur.application_number
         AND touched.role = cur.role
        LEFT JOIN ({party_agg}) AS incoming
          ON incoming.application_number = cur.application_number
         AND incoming.role = cur.role
         AND incoming.relation_key = cur.relation_key
        WHERE cur.is_current = 1
          AND cur.source_rank < {source_rank}
          AND incoming.application_number = ''
    """)

    client.command(f"""
        INSERT INTO markorbit_facts.cn_observed_event
        SELECT
            generateUUIDv4(), incoming.case_id, incoming.application_number,
            concat(incoming.role, '_RELATION_OBSERVED'),
            {effective_expr}, now64(3), 'PARTY', CAST(NULL, 'Nullable(UInt8)'),
            lowerUTF8(incoming.role),
            '', toJSONString(map(
                'name', incoming.raw_name, 'address', incoming.raw_address,
                'relation_key', incoming.relation_key,
                'entity_id', ifNull(toString(incoming.entity_id), '')
            )), 'OFFICIAL_FACT_OBSERVATION', 'NOT_DETERMINED', 1.0,
            toUUID('{package}'), '{package_kind}', incoming.source_file,
            incoming.source_first_line, incoming.source_last_line,
            incoming.source_row_hash, {source_rank},
            hex(SHA256(concat(
                incoming.application_number, '|', incoming.role, '|OBSERVED|',
                incoming.relation_key, '|', toString({source_rank})
            )))
        FROM ({party_agg}) AS incoming
        LEFT JOIN markorbit_facts.cn_case_party_current AS cur FINAL
          ON cur.application_number = incoming.application_number
         AND cur.role = incoming.role
         AND cur.relation_key = incoming.relation_key
        WHERE (cur.application_number = '' OR cur.source_rank < {source_rank})
          AND (cur.application_number = '' OR cur.is_current = 0
               OR cur.record_hash != incoming.record_hash)
    """)

    client.command(f"""
        INSERT INTO markorbit_facts.cn_case_party_relation_history
        SELECT
            generateUUIDv4(), cur.relation_id, cur.case_id, cur.application_number,
            cur.role, 'SUPERSEDED', {effective_expr}, cur.relation_key,
            cur.mention_id, cur.entity_id, cur.raw_name, cur.raw_address,
            toUUID('{package}'), '{package_kind}', touched.touched_source_file,
            touched.touched_first_line, touched.touched_last_line,
            touched.touched_source_row_hash, {source_rank},
            hex(SHA256(concat(
                cur.application_number, '|', cur.role, '|', cur.relation_key,
                '|SUPERSEDED|', toString({source_rank})
            ))), now64(3)
        FROM markorbit_facts.cn_case_party_current AS cur FINAL
        INNER JOIN ({party_touched}) AS touched
          ON touched.application_number = cur.application_number
         AND touched.role = cur.role
        LEFT JOIN ({party_agg}) AS incoming
          ON incoming.application_number = cur.application_number
         AND incoming.role = cur.role
         AND incoming.relation_key = cur.relation_key
        WHERE cur.is_current = 1
          AND cur.source_rank < {source_rank}
          AND incoming.application_number = ''
    """)

    client.command(f"""
        INSERT INTO markorbit_facts.cn_case_party_relation_history
        SELECT
            generateUUIDv4(), incoming.relation_id, incoming.case_id,
            incoming.application_number, incoming.role, 'OBSERVED_CURRENT',
            {effective_expr}, incoming.relation_key, incoming.mention_id,
            incoming.entity_id, incoming.raw_name, incoming.raw_address,
            toUUID('{package}'), '{package_kind}', incoming.source_file,
            incoming.source_first_line, incoming.source_last_line,
            incoming.source_row_hash, {source_rank},
            hex(SHA256(concat(
                incoming.application_number, '|', incoming.role, '|',
                incoming.relation_key, '|OBSERVED|', toString({source_rank})
            ))), now64(3)
        FROM ({party_agg}) AS incoming
    """)

    # Close relations omitted by a later case-role observation.
    client.command(f"""
        INSERT INTO markorbit_facts.cn_case_party_current
        SELECT
            cur.relation_id, cur.case_id, cur.application_number, cur.role,
            cur.relation_key, cur.mention_id, cur.entity_id, cur.agent_code,
            cur.raw_name, cur.normalized_name, cur.raw_address,
            cur.normalized_address, cur.country_code, cur.region_code, cur.city,
            cur.class_nos, cur.confidence_score, cur.valid_from, {effective_expr},
            0, 'SUPERSEDED_BY_SOURCE_OBSERVATION', 'CASE_ROLE_REPLACE',
            '{package_kind}', {effective_expr}, touched.touched_source_file,
            touched.touched_first_line, touched.touched_last_line,
            cur.source_row_hash, toUUID('{package}'),
            hex(SHA256(concat(cur.record_hash, '|SUPERSEDED|', toString({source_rank})))),
            {source_rank}, now64(3), 0
        FROM markorbit_facts.cn_case_party_current AS cur FINAL
        INNER JOIN ({party_touched}) AS touched
          ON touched.application_number = cur.application_number
         AND touched.role = cur.role
        LEFT JOIN ({party_agg}) AS incoming
          ON incoming.application_number = cur.application_number
         AND incoming.role = cur.role
         AND incoming.relation_key = cur.relation_key
        WHERE cur.is_current = 1
          AND cur.source_rank < {source_rank}
          AND incoming.application_number = ''
    """)

    client.command(f"""
        INSERT INTO markorbit_facts.cn_case_current
        SELECT
            incoming.case_id, 'CN', incoming.application_number,
            incoming.case_family_root, incoming.suffix_path, incoming.filing_route,
            incoming.number_family, incoming.international_registration_number,
            incoming.is_derived_case, 'UNKNOWN', incoming.mark_name_raw,
            lowerUTF8(incoming.mark_name_raw), incoming.filing_date,
            incoming.prelim_pub_date, incoming.prelim_pub_issue,
            incoming.registration_pub_date, incoming.registration_pub_issue,
            incoming.exclusive_start_date, incoming.exclusive_end_date,
            incoming.exclusive_period, incoming.classes, incoming.mark_type_raw,
            incoming.mark_form_raw, incoming.design_description,
            incoming.color_description, incoming.exclusive_rights_disclaimer,
            incoming.is_3d_mark, incoming.is_co_application,
            incoming.geo_indication_info, incoming.color_mark_flag,
            incoming.is_well_known_mark, incoming.agent_code,
            incoming.data_quality_flags, '{package_kind}', {effective_expr},
            incoming.source_file, incoming.source_first_line,
            incoming.source_last_line, incoming.source_row_hash,
            toUUID('{package}'), incoming.record_hash, {source_rank}, now64(3), 0
        FROM ({case_agg}) AS incoming
        LEFT JOIN markorbit_facts.cn_case_current AS cur FINAL
          ON cur.application_number = incoming.application_number
        WHERE cur.application_number = '' OR cur.source_rank <= {source_rank}
    """)

    client.command(f"""
        INSERT INTO markorbit_facts.cn_case_scope_current
        SELECT
            incoming.case_id, incoming.application_number, incoming.class_no,
            incoming.source_item_count, incoming.interpreted_active_item_count,
            incoming.interpreted_inactive_item_count, incoming.unmapped_status_item_count,
            incoming.effective_item_count, incoming.interpretation_complete,
            incoming.scope_interpretation_status,
            incoming.goods_status_mapping_version, incoming.observed_status_codes,
            incoming.goods_items_compact, incoming.goods_text_search,
            incoming.similar_groups, incoming.active_similar_groups,
            incoming.scope_hash, incoming.effective_scope_hash, '{package_kind}',
            {effective_expr}, incoming.source_file, incoming.source_first_line,
            incoming.source_last_line, incoming.source_row_hash, toUUID('{package}'),
            {source_rank}, now64(3), 0
        FROM ({scope_agg}) AS incoming
        LEFT JOIN markorbit_facts.cn_case_scope_current AS cur FINAL
          ON cur.application_number = incoming.application_number
         AND cur.class_no = incoming.class_no
        WHERE cur.application_number = '' OR cur.source_rank <= {source_rank}
    """)

    client.command(f"""
        INSERT INTO markorbit_facts.cn_case_party_current
        SELECT
            incoming.relation_id, incoming.case_id, incoming.application_number,
            incoming.role, incoming.relation_key, incoming.mention_id,
            incoming.entity_id, incoming.agent_code, incoming.raw_name,
            incoming.normalized_name, incoming.raw_address,
            incoming.normalized_address, incoming.country_code,
            incoming.region_code, incoming.city,
            if(length(case_current.classes) > 0, case_current.classes, incoming.class_nos),
            incoming.confidence_score,
            if(incoming.role = 'OWNER', case_current.filing_date, {effective_expr}),
            CAST(NULL, 'Nullable(Date32)'), 1, 'OBSERVED_CURRENT',
            'CASE_ROLE_REPLACE', '{package_kind}', {effective_expr},
            incoming.source_file, incoming.source_first_line,
            incoming.source_last_line, incoming.source_row_hash,
            toUUID('{package}'), incoming.record_hash, {source_rank}, now64(3), 0
        FROM ({party_agg}) AS incoming
        LEFT JOIN markorbit_facts.cn_case_current AS case_current FINAL
          ON case_current.application_number = incoming.application_number
        LEFT JOIN markorbit_facts.cn_case_party_current AS cur FINAL
          ON cur.application_number = incoming.application_number
         AND cur.role = incoming.role
         AND cur.relation_key = incoming.relation_key
        WHERE cur.application_number = '' OR cur.source_rank <= {source_rank}
    """)

    client.command(f"""
        INSERT INTO markorbit_facts.cn_agent_current
        SELECT
            b.agent_code, b.agent_mention_id, b.agent_entity_id,
            if(argMax(a.agent_name, toUInt64(a.source_start_line)) = '', b.agent_code,
               argMax(a.agent_name, toUInt64(a.source_start_line))),
            if(argMax(a.agent_name_norm, toUInt64(a.source_start_line)) = '', lowerUTF8(b.agent_code),
               argMax(a.agent_name_norm, toUInt64(a.source_start_line))),
            argMin(b.source_file, toUInt64(b.source_start_line)),
            min(toUInt64(b.source_start_line)),
            max(toUInt64(b.source_end_line)),
            hex(SHA256(arrayStringConcat(arraySort(groupArray(toString(b.row_hash))), '|'))),
            toUUID('{package}'), {source_rank}, now64(3), 0
        FROM markorbit_facts.cn_stage_basic AS b
        LEFT JOIN markorbit_facts.cn_stage_agent AS a
          ON a.package_id = b.package_id AND a.agent_code = b.agent_code
        WHERE b.package_id = toUUID('{package}') AND b.agent_code != ''
        GROUP BY b.agent_code, b.agent_mention_id, b.agent_entity_id
    """)

    client.command(f"""
        INSERT INTO markorbit_facts.cn_priority_current
        SELECT
            application_number, class_no, priority_number,
            argMax(priority_type, toUInt64(source_start_line)),
            argMax(priority_date, toUInt64(source_start_line)),
            argMax(priority_goods, toUInt64(source_start_line)),
            argMax(priority_country_region, toUInt64(source_start_line)),
            argMin(source_file, toUInt64(source_start_line)),
            min(toUInt64(source_start_line)),
            max(toUInt64(source_end_line)),
            hex(SHA256(arrayStringConcat(arraySort(groupArray(toString(row_hash))), '|'))),
            hex(SHA256(concat(
                application_number, '|', toString(class_no), '|', priority_number, '|',
                argMax(priority_goods, toUInt64(source_start_line))
            ))), toUUID('{package}'), {source_rank}, now64(3), 0
        FROM markorbit_facts.cn_stage_priority
        WHERE package_id = toUUID('{package}')
        GROUP BY application_number, class_no, priority_number
    """)

    client.command(f"""
        INSERT INTO markorbit_facts.cn_madrid_current
        SELECT
            application_number, international_registration_number,
            argMax(international_registration_date, toUInt64(source_start_line)),
            argMax(international_notification_date, toUInt64(source_start_line)),
            argMax(application_language, toUInt64(source_start_line)),
            argMax(application_type, toUInt64(source_start_line)),
            argMax(international_pub_issue, toUInt64(source_start_line)),
            argMax(international_pub_date, toUInt64(source_start_line)),
            argMax(subsequent_designation_date, toUInt64(source_start_line)),
            argMax(basic_registration_date, toUInt64(source_start_line)),
            argMin(source_file, toUInt64(source_start_line)),
            min(toUInt64(source_start_line)),
            max(toUInt64(source_end_line)),
            hex(SHA256(arrayStringConcat(arraySort(groupArray(toString(row_hash))), '|'))),
            hex(SHA256(concat(
                application_number, '|', international_registration_number, '|',
                ifNull(toString(argMax(international_registration_date, toUInt64(source_start_line))), '')
            ))), toUUID('{package}'), {source_rank}, now64(3), 0
        FROM markorbit_facts.cn_stage_madrid
        WHERE package_id = toUUID('{package}')
        GROUP BY application_number, international_registration_number
    """)

    # G-prefixed Madrid-designation cases remain CN cases and participate in
    # the same derived-case graph as direct CN filings.
    client.command(f"""
        INSERT INTO markorbit_facts.cn_case_relation_current
        SELECT
            incoming.relation_id,
            incoming.family_root_case_id,
            incoming.case_id,
            incoming.case_family_root,
            incoming.application_number,
            'DERIVED_CASE', 'UNKNOWN', incoming.filing_route,
            incoming.international_registration_number, 0.95,
            'SUFFIX_AND_ROOT_NUMBER_OBSERVED', toUUID('{package}'),
            '{package_kind}', incoming.source_file, incoming.source_first_line,
            incoming.source_last_line, incoming.source_row_hash,
            hex(SHA256(concat(
                incoming.case_family_root, '|', incoming.application_number,
                '|DERIVED_CASE|', incoming.suffix_path
            ))), {source_rank}, now64(3), 0
        FROM ({case_agg}) AS incoming
        WHERE incoming.is_derived_case = 1
    """)

    client.command(f"""
        INSERT INTO markorbit_facts.cn_observed_event
        SELECT
            generateUUIDv4(), incoming.case_id, incoming.application_number,
            'DERIVED_CASE_OBSERVED', incoming.filing_date, now64(3), 'CASE_RELATION',
            CAST(NULL, 'Nullable(UInt8)'), 'case_family', '',
            toJSONString(map(
                'root', incoming.case_family_root,
                'target', incoming.application_number,
                'suffix', incoming.suffix_path,
                'filing_route', incoming.filing_route,
                'international_registration_number',
                    incoming.international_registration_number,
                'derivation_reason', 'UNKNOWN'
            )), 'STRUCTURAL_INFERENCE', 'NOT_DETERMINED', 0.95,
            toUUID('{package}'), '{package_kind}', incoming.source_file,
            incoming.source_first_line, incoming.source_last_line,
            incoming.source_row_hash, {source_rank},
            hex(SHA256(concat(
                incoming.application_number, '|DERIVED_CASE|', incoming.case_family_root,
                '|', incoming.suffix_path, '|', toString({source_rank})
            )))
        FROM ({case_agg}) AS incoming
        WHERE incoming.is_derived_case = 1
    """)

    client.command(f"""
        INSERT INTO markorbit_facts.cn_scope_carve_out_current
        SELECT
            generateUUIDv4(), relation.relation_id,
            relation.source_application_number, relation.target_application_number,
            target.class_no, 'UNKNOWN', ifNull(source.scope_hash, ''),
            target.scope_hash,
            if(source.application_number = '', 'TARGET_SCOPE_ONLY',
               'ROOT_AND_TARGET_SCOPE_OBSERVED'),
            if(source.application_number = '', 0.55, 0.75),
            toUUID('{package}'), target.source_file, target.source_first_line,
            target.source_last_line, target.source_row_hash,
            hex(SHA256(concat(
                relation.source_application_number, '|',
                relation.target_application_number, '|', toString(target.class_no), '|',
                ifNull(source.scope_hash, ''), '|', target.scope_hash
            ))), {source_rank}, now64(3), 0
        FROM markorbit_facts.cn_case_relation_current AS relation FINAL
        INNER JOIN ({scope_agg}) AS target
          ON target.application_number = relation.target_application_number
        LEFT JOIN markorbit_facts.cn_case_scope_current AS source FINAL
          ON source.application_number = relation.source_application_number
         AND source.class_no = target.class_no
        WHERE relation.source_package_id = toUUID('{package}')
    """)

    metrics_rows = client.query(f"""
        SELECT
            (SELECT count() FROM ({case_agg})) AS cases,
            (SELECT count() FROM ({scope_agg})) AS scopes,
            (SELECT sum(source_item_count) FROM ({scope_agg})) AS goods_items,
            (SELECT sum(unmapped_status_item_count) FROM ({scope_agg}))
                AS unmapped_status_items,
            (SELECT count() FROM ({party_agg})) AS party_relations,
            (SELECT count() FROM ({case_agg}) WHERE is_derived_case = 1)
                AS derived_cases
    """).result_rows
    values = metrics_rows[0] if metrics_rows else (0, 0, 0, 0, 0, 0)
    return {
        "cases": int(values[0] or 0),
        "scopes": int(values[1] or 0),
        "goods_items": int(values[2] or 0),
        "unmapped_status_items": int(values[3] or 0),
        "party_relations": int(values[4] or 0),
        "derived_cases": int(values[5] or 0),
    }


def _collect_stage_quality_issues(
    package_uuid: uuid.UUID,
    run_id: uuid.UUID,
) -> list[dict[str, Any]]:
    client = clickhouse_client()
    package = str(package_uuid)
    issues: list[dict[str, Any]] = []

    date_rows = client.query(f"""
        SELECT
            flag,
            count() AS occurrence_count,
            groupArray(5)(tuple(application_number, source_file, source_start_line)) AS examples
        FROM
        (
            SELECT application_number, source_file, source_start_line,
                   arrayJoin(date_quality_flags) AS flag
            FROM markorbit_facts.cn_stage_basic
            WHERE package_id = toUUID('{package}')
        )
        GROUP BY flag
    """).result_rows
    for flag, occurrence_count, examples in date_rows:
        issues.append(
            {
                "package_id": package_uuid,
                "run_id": run_id,
                "issue_type": str(flag),
                "severity": "WARNING",
                "occurrence_count": int(occurrence_count),
                "source_file": None,
                "source_row": None,
                "raw_excerpt": "",
                "details": {"examples": examples},
            }
        )

    unknown_status_rows = client.query(f"""
        SELECT
            if(goods_status_raw = '', '<BLANK>', goods_status_raw) AS raw_code,
            count() AS occurrence_count,
            groupArray(5)(tuple(application_number, class_no, source_file, source_start_line))
                AS examples
        FROM markorbit_facts.cn_stage_goods
        WHERE package_id = toUUID('{package}')
          AND goods_status_bucket = 'UNKNOWN'
        GROUP BY raw_code
        ORDER BY occurrence_count DESC
    """).result_rows
    for raw_code, occurrence_count, examples in unknown_status_rows:
        issues.append(
            {
                "package_id": package_uuid,
                "run_id": run_id,
                "issue_type": "UNMAPPED_GOODS_STATUS_CODE",
                "severity": "WARNING",
                "occurrence_count": int(occurrence_count),
                "source_file": None,
                "source_row": None,
                "raw_excerpt": str(raw_code),
                "details": {"raw_code": raw_code, "examples": examples},
            }
        )

    integrity_specs = [
        (
            "GOODS_WITHOUT_BASIC",
            f"""
            SELECT count(), groupArray(10)(tuple(g.application_number, g.class_no))
            FROM
            (
                SELECT DISTINCT application_number, class_no
                FROM markorbit_facts.cn_stage_goods
                WHERE package_id = toUUID('{package}')
            ) AS g
            LEFT JOIN
            (
                SELECT DISTINCT application_number, class_no
                FROM markorbit_facts.cn_stage_basic
                WHERE package_id = toUUID('{package}')
            ) AS b USING (application_number, class_no)
            WHERE b.application_number = ''
            """,
        ),
        (
            "BASIC_WITHOUT_GOODS",
            f"""
            SELECT count(), groupArray(10)(tuple(b.application_number, b.class_no))
            FROM
            (
                SELECT DISTINCT application_number, class_no
                FROM markorbit_facts.cn_stage_basic
                WHERE package_id = toUUID('{package}')
            ) AS b
            LEFT JOIN
            (
                SELECT DISTINCT application_number, class_no
                FROM markorbit_facts.cn_stage_goods
                WHERE package_id = toUUID('{package}')
            ) AS g USING (application_number, class_no)
            WHERE g.application_number = ''
            """,
        ),
        (
            "APPLICANT_WITHOUT_BASIC",
            f"""
            SELECT count(), groupArray(10)(tuple(a.application_number, a.class_no))
            FROM
            (
                SELECT DISTINCT application_number, class_no
                FROM markorbit_facts.cn_stage_applicant
                WHERE package_id = toUUID('{package}')
            ) AS a
            LEFT JOIN
            (
                SELECT DISTINCT application_number, class_no
                FROM markorbit_facts.cn_stage_basic
                WHERE package_id = toUUID('{package}')
            ) AS b USING (application_number, class_no)
            WHERE b.application_number = ''
            """,
        ),
    ]
    for issue_type, query in integrity_specs:
        row = client.query(query).result_rows[0]
        count_value = int(row[0] or 0)
        if count_value:
            issues.append(
                {
                    "package_id": package_uuid,
                    "run_id": run_id,
                    "issue_type": issue_type,
                    "severity": "WARNING",
                    "occurrence_count": count_value,
                    "source_file": None,
                    "source_row": None,
                    "raw_excerpt": "",
                    "details": {"examples": row[1]},
                }
            )
    return issues


def _cleanup_partial_outputs(package_uuid: uuid.UUID) -> None:
    client = clickhouse_client()
    package = str(package_uuid)
    filters = {
        "markorbit_facts.cn_observed_event": "source_package_id",
        "markorbit_facts.cn_case_current": "last_source_package_id",
        "markorbit_facts.cn_case_scope_current": "last_source_package_id",
        "markorbit_facts.cn_case_party_current": "last_source_package_id",
        "markorbit_facts.cn_case_party_relation_history": "source_package_id",
        "markorbit_facts.cn_agent_current": "last_source_package_id",
        "markorbit_facts.cn_priority_current": "last_source_package_id",
        "markorbit_facts.cn_madrid_current": "last_source_package_id",
        "markorbit_facts.cn_case_relation_current": "source_package_id",
        "markorbit_facts.cn_scope_carve_out_current": "source_package_id",
    }
    for table, column in filters.items():
        client.command(
            f"ALTER TABLE {table} DELETE WHERE {column} = toUUID('{package}') "
            "SETTINGS mutations_sync = 1"
        )


def _cleanup_stage(package_uuid: uuid.UUID) -> None:
    client = clickhouse_client()
    package = str(package_uuid)
    for table in STAGE_COLUMNS:
        client.command(
            f"ALTER TABLE {table} DELETE WHERE package_id = toUUID('{package}') "
            "SETTINGS mutations_sync = 1"
        )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _archive_package(path: Path, raw_root: Path) -> Path:
    archive_dir = raw_root / "archive" / "cn"
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination = archive_dir / path.name
    if path.resolve() == destination.resolve():
        return destination
    if destination.exists():
        # A reset may require replaying a package that is already archived. Keep
        # only one authoritative raw copy when the files are byte-identical.
        if _file_sha256(path) == _file_sha256(destination):
            path.unlink()
            return destination
        destination = archive_dir / f"{path.stem}_{_file_sha256(path)[:8]}{path.suffix}"
    shutil.move(str(path), str(destination))
    return destination


def ingest_cn_package(
    package_id: str,
    path: Path,
    raw_root: Path,
    trigger_type: str = "SCHEDULED",
    retrying: bool = False,
) -> dict[str, Any]:
    package_uuid = uuid.UUID(str(package_id))
    ensure_m15_schema()
    package_meta = get_package(str(package_uuid))
    run_id_text = create_job_run(
        job_type="CN_PACKAGE_INGESTION",
        trigger_type=trigger_type,
        payload={
            "package_id": str(package_uuid),
            "path": str(path),
            "package_kind": package_meta["package_kind"],
            "source_rank": package_meta["source_rank"],
        },
    )
    run_id = uuid.UUID(run_id_text)
    writer = StageBatchWriter()
    mentions: list[dict[str, Any]] = []
    entities: dict[uuid.UUID, EntityCandidate] = {}
    quality_issues: list[dict[str, Any]] = []
    member_profiles: list[dict[str, Any]] = []
    role_counts: Counter[str] = Counter()

    def flush_identity_buffers() -> None:
        nonlocal mentions, entities
        if entities:
            upsert_entities(entities.values())
            entities = {}
        if mentions:
            upsert_entity_mentions(mentions)
            mentions = []

    try:
        update_package_status(
            str(package_uuid),
            "PROCESSING",
            package_kind=str(package_meta["package_kind"]),
        )
        if retrying:
            _cleanup_stage(package_uuid)
            _cleanup_partial_outputs(package_uuid)

        for member in iter_package_members(path):
            if member.schema is None:
                quality_issues.append(
                    {
                        "package_id": package_uuid,
                        "run_id": run_id,
                        "issue_type": "UNCLASSIFIED_PACKAGE_MEMBER",
                        "severity": "WARNING",
                        "source_file": member.internal_name,
                        "source_row": None,
                        "raw_excerpt": "",
                        "details": {
                            "original_internal_name": member.original_internal_name,
                            "size": member.size,
                        },
                    }
                )
                continue

            profile, rows = iter_member_rows(member)
            for parsed in rows:
                role = member.schema.role
                role_counts[role] += 1
                record = parsed.record
                source_file = member.internal_name
                source_start_line = parsed.source_start_line
                source_end_line = parsed.source_end_line

                if role == "applicant":
                    result = _party_values(
                        package_uuid,
                        record,
                        "OWNER",
                        source_file,
                        source_start_line,
                        source_end_line,
                    )
                    if result:
                        writer.add(result.table, result.row)
                        mentions.append(result.mention)
                        if result.entity:
                            entities[result.entity.entity_id] = result.entity
                elif role == "coowner":
                    result = _party_values(
                        package_uuid,
                        record,
                        "CO_OWNER",
                        source_file,
                        source_start_line,
                        source_end_line,
                    )
                    if result:
                        writer.add(result.table, result.row)
                        mentions.append(result.mention)
                        if result.entity:
                            entities[result.entity.entity_id] = result.entity
                elif role == "basic":
                    row, entity, mention = _basic_stage_row(
                        package_uuid,
                        record,
                        source_file,
                        source_start_line,
                        source_end_line,
                    )
                    if row:
                        writer.add("markorbit_facts.cn_stage_basic", row)
                    if entity:
                        entities[entity.entity_id] = entity
                    if mention:
                        mentions.append(mention)
                else:
                    staged = _other_stage_row(
                        role,
                        package_uuid,
                        record,
                        source_file,
                        source_start_line,
                        source_end_line,
                    )
                    if staged:
                        table, row, entity, mention = staged
                        writer.add(table, row)
                        if entity:
                            entities[entity.entity_id] = entity
                        if mention:
                            mentions.append(mention)

                if len(mentions) >= 5_000 or len(entities) >= 5_000:
                    flush_identity_buffers()

            profile_item = profile.as_dict()
            profile_item.update(
                {
                    "original_internal_name": member.original_internal_name,
                    "size": member.size,
                    "compressed_size": member.compressed_size,
                    "filename_repaired": member.filename_repaired,
                    "filename_encoding": member.filename_encoding,
                }
            )
            member_profiles.append(profile_item)
            upsert_package_file(str(package_uuid), profile_item)

            unknown_headers = [
                value for value in profile.header_canonical if value.startswith("unknown:")
            ]
            if unknown_headers:
                quality_issues.append(
                    {
                        "package_id": package_uuid,
                        "run_id": run_id,
                        "issue_type": "UNKNOWN_SOURCE_HEADER",
                        "severity": "WARNING",
                        "source_file": member.internal_name,
                        "source_row": 1,
                        "raw_excerpt": ",".join(profile.header_raw),
                        "details": {
                            "unknown_headers": unknown_headers,
                            "canonical_headers": profile.header_canonical,
                        },
                    }
                )

            for example in profile.failed_examples:
                quality_issues.append(
                    {
                        "package_id": package_uuid,
                        "run_id": run_id,
                        "issue_type": "UNREPAIRABLE_CSV_ROW",
                        "severity": "ERROR",
                        "source_file": member.internal_name,
                        "source_row": example["start_line"],
                        "raw_excerpt": example["raw_excerpt"],
                        "details": example,
                    }
                )
            if profile.replacement_chars:
                quality_issues.append(
                    {
                        "package_id": package_uuid,
                        "run_id": run_id,
                        "issue_type": "INVALID_TEXT_BYTES_REPLACED",
                        "severity": "WARNING",
                        "occurrence_count": profile.replacement_chars,
                        "source_file": member.internal_name,
                        "source_row": None,
                        "raw_excerpt": "",
                        "details": {"replacement_chars": profile.replacement_chars},
                    }
                )

        writer.close()
        flush_identity_buffers()

        if role_counts["basic"] == 0:
            raise RuntimeError("No valid registered-trademark basic rows were produced")
        if role_counts["goods"] == 0:
            raise RuntimeError("No valid registered-trademark goods rows were produced")

        quality_issues.extend(_collect_stage_quality_issues(package_uuid, run_id))
        if quality_issues:
            record_quality_issues(quality_issues)

        publish_metrics = _publish(package_uuid, package_meta)

        totals = {
            "role_counts": dict(role_counts),
            "stage_counts": dict(writer.row_counts),
            "files": len(member_profiles),
            "failed_rows": sum(int(item["failed_rows"]) for item in member_profiles),
            "continuation_rows": sum(
                int(item["continuation_rows"]) for item in member_profiles
            ),
            "replacement_chars": sum(
                int(item["replacement_chars"]) for item in member_profiles
            ),
            "package_kind": package_meta["package_kind"],
            "partition_dimension": package_meta["partition_dimension"],
            "partition_value": package_meta["partition_value"],
            "source_rank": package_meta["source_rank"],
            "publish": publish_metrics,
        }
        profile = {
            "schema_version": "M1.5",
            "package_kind": package_meta["package_kind"],
            "partition_dimension": package_meta["partition_dimension"],
            "partition_value": package_meta["partition_value"],
            "source_period_start": package_meta.get("source_period_start"),
            "source_period_end": package_meta.get("source_period_end"),
            "source_rank": package_meta["source_rank"],
            "members": member_profiles,
            "totals": totals,
        }
        archived = _archive_package(path, raw_root)
        update_package_status(
            str(package_uuid),
            "SUCCESS",
            package_kind=str(package_meta["package_kind"]),
            profile=profile,
            archived_path=str(archived),
        )
        finish_job_run(run_id_text, "SUCCESS", metrics=totals)
        _cleanup_stage(package_uuid)
        return totals

    except Exception as exc:
        try:
            writer.close()
        except Exception:
            pass
        try:
            _cleanup_stage(package_uuid)
        except Exception:
            pass
        try:
            _cleanup_partial_outputs(package_uuid)
        except Exception:
            pass
        update_package_status(
            str(package_uuid),
            "FAILED",
            package_kind=str(package_meta["package_kind"]),
            error_message=str(exc),
        )
        finish_job_run(run_id_text, "FAILED", error_message=str(exc))
        raise
