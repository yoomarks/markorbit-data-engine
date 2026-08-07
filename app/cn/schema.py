from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable


@dataclass(frozen=True)
class FileSchema:
    role: str
    name_markers: tuple[str, ...]
    canonical_columns: tuple[str, ...]
    header_aliases: dict[str, str]
    variable_column: str | None = None
    requires_class: bool = False
    requires_date: bool = False


def _aliases(**kwargs: str) -> dict[str, str]:
    return {normalize_header(k): v for k, v in kwargs.items()}


def normalize_header(value: str) -> str:
    value = (value or "").replace("\ufeff", "").strip()
    value = value.replace("＞", ">").replace("：", ":")
    value = re.sub(r"\s+", "", value)
    return value


SCHEMAS: tuple[FileSchema, ...] = (
    FileSchema(
        role="basic",
        name_markers=("注册商标基本信息",),
        canonical_columns=(
            "application_number", "class_no", "filing_date", "mark_name", "mark_type_raw",
            "agent_code", "prelim_pub_issue", "prelim_pub_date", "registration_pub_issue",
            "registration_pub_date", "exclusive_start_date", "exclusive_end_date",
            "exclusive_period", "design_description", "color_description",
            "exclusive_rights_disclaimer", "is_3d_mark", "is_co_application",
            "mark_form_raw", "geo_indication_info", "color_mark_flag", "is_well_known_mark",
        ),
        header_aliases=_aliases(**{
            "注册号/申请号": "application_number",
            "注册号": "application_number",
            "申请号": "application_number",
            "国际分类": "class_no",
            "申请日期": "filing_date",
            "商标名称": "mark_name",
            "商标类型": "mark_type_raw",
            "代理机构代码": "agent_code",
            "初审公告期号": "prelim_pub_issue",
            "初审公告日期": "prelim_pub_date",
            "初审公告日期>日期": "prelim_pub_date",
            "注册公告期号": "registration_pub_issue",
            "注册公告日期": "registration_pub_date",
            "注册公告日期>日期": "registration_pub_date",
            "专用期开始日期": "exclusive_start_date",
            "专用期结束日期": "exclusive_end_date",
            "专用有效期": "exclusive_period",
            "商标设计说明": "design_description",
            "商标颜色说明": "color_description",
            "放弃专用权说明": "exclusive_rights_disclaimer",
            "是否立体商标": "is_3d_mark",
            "是否共有申请": "is_co_application",
            "商标形态": "mark_form_raw",
            "地理标志信息": "geo_indication_info",
            "颜色标志": "color_mark_flag",
            "是否驰名商标": "is_well_known_mark",
        }),
        variable_column="design_description",
        requires_class=True,
        requires_date=True,
    ),
    FileSchema(
        role="applicant",
        name_markers=("商标注册人信息", "注册商标注册人信息"),
        canonical_columns=(
            "application_number", "class_no", "owner_name_cn", "owner_name_foreign",
            "owner_address_cn", "owner_address_foreign",
        ),
        header_aliases=_aliases(**{
            "注册号/申请号": "application_number",
            "申请号": "application_number",
            "注册号": "application_number",
            "国际分类": "class_no",
            "注册人中文名称": "owner_name_cn",
            "申请人中文名称": "owner_name_cn",
            "注册人外文名称": "owner_name_foreign",
            "申请人外文名称": "owner_name_foreign",
            "注册人中文地址": "owner_address_cn",
            "申请人中文地址": "owner_address_cn",
            "注册人英文地址": "owner_address_foreign",
            "申请人英文地址": "owner_address_foreign",
        }),
        variable_column="owner_address_cn",
        requires_class=True,
    ),
    FileSchema(
        role="goods",
        name_markers=("注册商标商品服务信息", "商标商品服务信息"),
        canonical_columns=(
            "application_number", "class_no", "similar_group", "goods_sequence",
            "goods_name", "goods_status_raw",
        ),
        header_aliases=_aliases(**{
            "注册号/申请号": "application_number",
            "申请号": "application_number",
            "注册号": "application_number",
            "国际分类": "class_no",
            "类似群": "similar_group",
            "商品序号": "goods_sequence",
            "商品中文名称": "goods_name",
            "商品/服务名称": "goods_name",
            "商品状态": "goods_status_raw",
        }),
        variable_column="goods_name",
        requires_class=True,
    ),
    FileSchema(
        role="coowner",
        name_markers=("注册商标共有人信息", "商标共有人信息"),
        canonical_columns=(
            "application_number", "coowner_name_cn", "coowner_name_foreign",
            "coowner_address_cn", "coowner_address_foreign",
        ),
        header_aliases=_aliases(**{
            "注册号/申请号": "application_number",
            "申请号": "application_number",
            "注册号": "application_number",
            "共有人中文名称": "coowner_name_cn",
            "共有人英文文名称": "coowner_name_foreign",
            "共有人英文名称": "coowner_name_foreign",
            "共有人中文地址": "coowner_address_cn",
            "共有人英文地址": "coowner_address_foreign",
        }),
        variable_column="coowner_address_cn",
    ),
    FileSchema(
        role="priority",
        name_markers=("注册商标优先权信息", "商标优先权信息"),
        canonical_columns=(
            "application_number", "class_no", "priority_number", "priority_type",
            "priority_date", "priority_goods", "priority_country_region",
        ),
        header_aliases=_aliases(**{
            "注册号/申请号": "application_number",
            "申请号": "application_number",
            "注册号": "application_number",
            "国际分类": "class_no",
            "优先权编号": "priority_number",
            "优先权种类": "priority_type",
            "优先权日期": "priority_date",
            "优先权商品": "priority_goods",
            "优先权国家/地区": "priority_country_region",
        }),
        variable_column="priority_number",
        requires_class=True,
    ),
    FileSchema(
        role="madrid",
        name_markers=("国际注册基本信息",),
        canonical_columns=(
            "application_number", "international_registration_number",
            "international_registration_date", "international_notification_date",
            "application_language", "application_type", "international_pub_issue",
            "international_pub_date", "subsequent_designation_date", "basic_registration_date",
        ),
        header_aliases=_aliases(**{
            "注册号/申请号": "application_number",
            "申请号": "application_number",
            "注册号": "application_number",
            "国际注册号": "international_registration_number",
            "国际注册日期": "international_registration_date",
            "国际通知日期": "international_notification_date",
            "国际申请语种": "application_language",
            "国际申请类型": "application_type",
            "国际公告期号": "international_pub_issue",
            "国际公告日期": "international_pub_date",
            "后期指定>日期": "subsequent_designation_date",
            "后期指定日期": "subsequent_designation_date",
            "基础注册日期": "basic_registration_date",
        }),
        variable_column="basic_registration_date",
    ),
    FileSchema(
        role="agent",
        name_markers=("商标代理人信息", "商标代理机构信息"),
        canonical_columns=("agent_code", "agent_name"),
        header_aliases=_aliases(**{
            "代理人编码": "agent_code",
            "代理机构代码": "agent_code",
            "代理人名称": "agent_name",
            "代理机构名称": "agent_name",
        }),
        variable_column="agent_name",
    ),
)


SCHEMA_BY_ROLE = {schema.role: schema for schema in SCHEMAS}


def schema_for_filename(name: str) -> FileSchema | None:
    base = Path(name).name
    for schema in SCHEMAS:
        if any(marker in base for marker in schema.name_markers):
            return schema
    return None


def canonical_header(schema: FileSchema, raw_header: Iterable[str]) -> list[str]:
    values = list(raw_header)
    out: list[str] = []
    for index, value in enumerate(values):
        normalized = normalize_header(value)
        mapped = schema.header_aliases.get(normalized)

        # Some CN exports flatten nested XML headers to "公告日期>日期".
        # In the basic file, position and the neighboring issue column make the
        # otherwise ambiguous date deterministic.
        if mapped is None and schema.role == "basic":
            previous = normalize_header(values[index - 1]) if index > 0 else ""
            if normalized in {"公告日期", "公告日期>日期", "日期"}:
                if "初审" in previous or index == 7:
                    mapped = "prelim_pub_date"
                elif "注册" in previous or index == 9:
                    mapped = "registration_pub_date"
            elif normalized in {"公告期号", "期号"}:
                if "初审" in normalized or index == 6:
                    mapped = "prelim_pub_issue"
                elif "注册" in normalized or index == 8:
                    mapped = "registration_pub_issue"

        out.append(mapped or f"unknown:{normalized}")
    return out


def expected_count(schema: FileSchema, raw_canonical_header: list[str]) -> int:
    return len(raw_canonical_header)


def canonical_record(schema: FileSchema, header: list[str], values: list[str]) -> dict[str, str]:
    record = {column: "" for column in schema.canonical_columns}
    for column, value in zip(header, values):
        if not column.startswith("unknown:") and column in record:
            record[column] = value
    return record
