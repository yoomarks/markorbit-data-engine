from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.contact_ingest.models import FieldMapping, TableData
from app.contact_ingest.normalization import clean_text, normalize_header


@dataclass(frozen=True)
class AliasSpec:
    canonical_field: str
    aliases: tuple[str, ...]
    owner_hint: str | None = None


_ALIAS_SPECS = (
    AliasSpec("ENTITY_NAME", (
        "企业名称", "公司名称", "机构名称", "主体名称", "申请人名称", "申请人", "代理机构名称", "代理机构",
        "company", "companyname", "organization", "organizationname", "firm", "firmname", "lawfirm", "agency", "agencyname",
    )),
    AliasSpec("ENTITY_STATUS", ("登记状态", "企业状态", "经营状态", "status", "companystatus")),
    AliasSpec("LEGAL_REPRESENTATIVE", ("法定代表人", "法人", "法定代表", "legalrepresentative", "legalrep")),
    AliasSpec("ESTABLISHED_DATE", ("成立日期", "成立时间", "注册日期", "establisheddate", "founded", "incorporationdate")),
    AliasSpec("CREDIT_CODE", ("统一社会信用代码", "信用代码", "统一信用代码", "uscc", "unifiedsocialcreditcode", "creditcode")),
    AliasSpec("ADDRESS", ("注册地址", "企业地址", "公司地址", "办公地址", "地址", "address", "registeredaddress", "firmaddress")),
    AliasSpec("PROVINCE", ("所属省份", "省份", "省", "province", "state", "region")),
    AliasSpec("CITY", ("所属城市", "城市", "市", "city")),
    AliasSpec("COUNTRY", ("国家", "国家地区", "国家/地区", "country", "countrycode", "jurisdiction")),
    AliasSpec("MOBILE", ("有效手机号", "手机号", "手机号码", "手机", "mobile", "mobilenumber")),
    AliasSpec("PHONE", ("更多电话", "联系电话", "电话", "电话号码", "tel", "telephone", "phone", "phonenumber")),
    AliasSpec("EMAIL", ("邮箱", "电子邮箱", "邮件", "email", "emailaddress", "e-mail")),
    AliasSpec("WEBSITE", ("官网网址", "官网", "网址", "网站", "website", "web", "url")),
    AliasSpec("FORMER_NAME", ("曾用名", "历史名称", "formername", "previousname")),
    AliasSpec("ENGLISH_NAME", ("英文名", "英文名称", "englishname", "nameenglish")),
    AliasSpec("CONTACT_PERSON", ("联系人", "联系人姓名", "联系人名称", "contact", "contactperson", "contactname", "attorney", "attorneyname")),
    AliasSpec("TITLE", ("职位", "职务", "头衔", "title", "position", "jobtitle")),
    AliasSpec("DEPARTMENT", ("部门", "department", "team")),
    AliasSpec("PERSON_EMAIL", ("联系人邮箱", "个人邮箱", "contactemail", "personemail", "attorneyemail"), "PERSON"),
    AliasSpec("PERSON_PHONE", ("联系人电话", "联系人手机", "contactphone", "contactmobile", "personphone", "attorneyphone"), "PERSON"),
    AliasSpec("WHATSAPP", ("whatsapp", "whatsappnumber", "whatsapp号码")),
    AliasSpec("AGENT_CODE", ("代理机构代码", "代理代码", "agentcode", "agencycode")),
)

_ALIAS_LOOKUP: dict[str, AliasSpec] = {}
for spec in _ALIAS_SPECS:
    for alias in spec.aliases:
        _ALIAS_LOOKUP[normalize_header(alias)] = spec


def lookup_header(header: str) -> AliasSpec | None:
    return _ALIAS_LOOKUP.get(normalize_header(header))


def score_header(row: Iterable[str]) -> float:
    score = 0.0
    seen: set[str] = set()
    for value in row:
        spec = lookup_header(value)
        if not spec or spec.canonical_field in seen:
            continue
        seen.add(spec.canonical_field)
        if spec.canonical_field == "ENTITY_NAME":
            score += 4.0
        elif spec.canonical_field in {"CREDIT_CODE", "CONTACT_PERSON", "LEGAL_REPRESENTATIVE"}:
            score += 2.0
        elif spec.canonical_field in {"EMAIL", "MOBILE", "PHONE", "PERSON_EMAIL", "PERSON_PHONE", "WEBSITE"}:
            score += 1.5
        else:
            score += 1.0
    return score


def detect_header_row(table: TableData, scan_rows: int = 20) -> tuple[int, float]:
    best_idx = -1
    best_score = 0.0
    for idx, row in enumerate(table.rows[:scan_rows]):
        score = score_header(row)
        if score > best_score:
            best_idx, best_score = idx, score
    return best_idx, best_score


def map_headers(headers: list[str]) -> list[FieldMapping]:
    mappings: list[FieldMapping] = []
    for idx, raw in enumerate(headers):
        spec = lookup_header(raw)
        if spec:
            mappings.append(FieldMapping(
                source_column=clean_text(raw),
                source_index=idx,
                canonical_field=spec.canonical_field,
                confidence=1.0,
                owner_hint=spec.owner_hint,  # type: ignore[arg-type]
            ))
    return mappings


def detect_profile(headers: list[str], mappings: list[FieldMapping]) -> tuple[str, float]:
    normalized_headers = {normalize_header(header) for header in headers if clean_text(header)}
    canonical = {mapping.canonical_field for mapping in mappings}
    qcc_markers = {
        normalize_header("企业名称"), normalize_header("统一社会信用代码"),
        normalize_header("法定代表人"), normalize_header("有效手机号"),
    }
    qcc_hits = len(qcc_markers & normalized_headers)
    if qcc_hits >= 3 and "ENTITY_NAME" in canonical:
        return "QCC_COMPANY_EXPORT", min(1.0, 0.70 + qcc_hits * 0.075)

    agent_header_markers = {
        normalize_header("firm name"), normalize_header("law firm"), normalize_header("agency name"),
        normalize_header("attorney"), normalize_header("attorney name"), normalize_header("contact person"),
    }
    agent_hits = len(agent_header_markers & normalized_headers)
    has_channel = bool(canonical & {"EMAIL", "PHONE", "MOBILE", "PERSON_EMAIL", "PERSON_PHONE", "WEBSITE", "WHATSAPP"})
    if "ENTITY_NAME" in canonical and has_channel and ("CONTACT_PERSON" in canonical or agent_hits >= 1):
        return "AGENT_CONTACT_LIST", min(0.98, 0.74 + agent_hits * 0.06)

    if "ENTITY_NAME" in canonical and has_channel:
        return "GENERIC_CONTACT_TABLE", 0.80
    if "ENTITY_NAME" in canonical:
        return "GENERIC_ENTITY_TABLE", 0.65
    return "UNKNOWN", 0.0
