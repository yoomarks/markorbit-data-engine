from __future__ import annotations

from dataclasses import dataclass
import re
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
        "企业名称", "系统匹配企业名称", "公司名称", "公司", "机构名称", "主体名称", "申请人名称", "申请人",
        "代理机构名称", "代理机构", "代理名称", "代理公司", "代理机构全称", "事务所名称", "律所名称",
        "知识产权代理机构", "office name", "officename",
        "company", "companyname", "organization", "organizationname", "firm", "firmname", "lawfirm", "agency", "agencyname",
        "rawfirmname", "rawagentfirmname", "canonicalfirmname",
    )),
    # ``原文件导入名称`` is intentionally not an authoritative company name. It is
    # kept as a fallback field and is only promoted by the planner when a stable
    # identifier such as a USCC is present. This preserves the V1.2 QCC safety rule.
    AliasSpec("SOURCE_ENTITY_NAME", (
        "原文件导入名称", "sourceentityname", "sourcecompanyname", "importedname",
    )),
    # Legacy agent datasets frequently use a generic agent/name column. Keep it
    # separate from firm/entity names so the planner can use it as a safe fallback
    # or as the contact person when an explicit firm is present.
    AliasSpec("AGENT_NAME", (
        "代理人", "代理人名称", "代理", "agent", "agentname", "rawagentname",
        "officialname", "name", "名称", "canonicalname", "rawname",
        "emër/mbiemër", "emermbiemer", "нэр",
    )),
    AliasSpec("ENTITY_STATUS", (
        "登记状态", "企业状态", "经营状态", "status", "companystatus", "entitystatus", "leadstatus",
    )),
    AliasSpec("LEGAL_REPRESENTATIVE", ("法定代表人", "法人", "法定代表", "legalrepresentative", "legalrep")),
    AliasSpec("ESTABLISHED_DATE", ("成立日期", "成立时间", "注册日期", "establisheddate", "founded", "incorporationdate")),
    AliasSpec("CREDIT_CODE", (
        "统一社会信用代码", "信用代码", "统一信用代码", "uscc", "unifiedsocialcreditcode", "creditcode", "nipt",
    )),
    AliasSpec("ADDRESS", (
        "注册地址", "企业地址", "公司地址", "办公地址", "地址", "address", "registeredaddress", "firmaddress",
        "rawaddress", "postaladdress", "streetaddress", "businessaddress", "officeaddress", "adresa", "agentaddress",
    )),
    AliasSpec("PROVINCE", ("所属省份", "省份", "省", "province", "state", "region")),
    AliasSpec("CITY", ("所属城市", "城市", "市", "city", "agentcity", "districtcity")),
    AliasSpec("COUNTRY", (
        "国家", "国家地区", "国家/地区", "country", "countrycode", "jurisdiction", "jurisdictioncode",
        "rawcountry", "rawjurisdiction", "countrycodehint", "jurisdictionhint", "sourcecountrycode",
        "resolvedcountrycode", "officialcountrycode",
    )),
    AliasSpec("MOBILE", (
        "有效手机号", "手机号", "手机号码", "手机", "mobile", "mobilenumber", "mobilephone", "rawmobile", "primarymobile",
    )),
    AliasSpec("PHONE", (
        "更多电话", "联系电话", "联系方式", "电话", "电话号码", "tel", "telephone", "phone", "phonenumber",
        "businessphone", "officephone", "rawphone", "primaryphone",
    )),
    AliasSpec("EMAIL", (
        "邮箱", "更多邮箱", "备用邮箱", "电子邮箱", "邮件", "email", "emailaddress", "e-mail", "rawemail", "primaryemail",
    )),
    AliasSpec("WEBSITE", (
        "官网网址", "官网", "网址", "网站", "website", "web", "url", "webpage", "websiteurl", "rawwebsite",
        "primarywebsite", "primarydomain", "domain",
    )),
    AliasSpec("FORMER_NAME", ("曾用名", "历史名称", "formername", "previousname")),
    AliasSpec("ENGLISH_NAME", ("英文名", "英文名称", "englishname", "nameenglish")),
    AliasSpec("CONTACT_PERSON", (
        "联系人", "联系人姓名", "联系人名称", "姓名", "contact", "contactperson", "contactname", "attorney", "attorneyname",
        "attorneynames", "person", "personname", "rawpersonname", "rawcontactname", "rawattorneyname", "rawcorrespondentname",
        "lawyer", "lawyername", "lawyernames", "律师", "律师姓名", "代理师", "代理师姓名", "专利代理师", "商标代理人",
    )),
    AliasSpec("PERSON_SURNAME", ("surname", "lastname", "familyname", "姓")),
    AliasSpec("PERSON_GIVEN_NAMES", ("othernames", "givennames", "firstname", "forenames", "名")),
    AliasSpec("TITLE", ("职位", "职务", "头衔", "title", "position", "jobtitle", "role")),
    AliasSpec("DEPARTMENT", ("部门", "department", "team")),
    AliasSpec("PERSON_EMAIL", (
        "联系人邮箱", "个人邮箱", "代理人邮箱", "代理人备用邮箱", "contactemail", "personemail", "attorneyemail",
        "lawyeremail", "agentemail",
    ), "PERSON"),
    AliasSpec("PERSON_PHONE", (
        "联系人电话", "联系人手机", "代理人电话", "代理人手机", "contactphone", "contactmobile", "personphone",
        "attorneyphone", "lawyerphone", "agentphone", "agentmobile", "agenttelephone",
    ), "PERSON"),
    AliasSpec("WHATSAPP", (
        "whatsapp", "whatsappnumber", "whatsapp号码", "rawwhatsapp", "primarywhatsapp",
    )),
    AliasSpec("AGENT_CODE", (
        "代理机构代码", "代理代码", "agentcode", "agencycode", "agentno", "agentnumber", "agentseq", "licenseno",
        "licensenumber", "registrationno", "注册号", "执业证号", "numriilicencësnëregjistër", "numriilicencesnereg­jister",
    )),
    AliasSpec("TRADEMARK_APPLICATION_NUMBER", (
        "申请号", "申请号码", "applicationnumber", "serialnumber", "serialno",
    )),
    AliasSpec("TRADEMARK_REGISTRATION_NUMBER", (
        "商标注册号", "registrationnumber", "registrationno", "regnumber", "regno",
    )),
    # Some public-register exports use a single mixed contact cell (phone/email).
    # The planner classifies the value by its content without changing ownership.
    AliasSpec("CONTACT_VALUE", (
        "холбоо барих", "kontakt", "contactdetails", "contactdetail", "contactinfo", "contactinformation",
    ), "PERSON"),
)

_ALIAS_LOOKUP: dict[str, AliasSpec] = {}
for spec in _ALIAS_SPECS:
    for alias in spec.aliases:
        _ALIAS_LOOKUP[normalize_header(alias)] = spec


def _heuristic_spec(normalized: str) -> AliasSpec | None:
    """Recognize common numbered/prefixed legacy export headers conservatively."""
    if not normalized:
        return None

    if re.fullmatch(r"(?:raw|primary)?email\d*", normalized):
        return AliasSpec("EMAIL", ())
    if re.fullmatch(r"(?:contact|person|attorney|lawyer|agent)email\d*", normalized):
        return AliasSpec("PERSON_EMAIL", (), "PERSON")
    if re.fullmatch(r"(?:raw|primary|business|office)?(?:phone|telephone|tel)\d*", normalized):
        return AliasSpec("PHONE", ())
    if re.fullmatch(r"(?:contact|person|attorney|lawyer|agent)(?:phone|mobile|telephone|tel)\d*", normalized):
        return AliasSpec("PERSON_PHONE", (), "PERSON")
    if re.fullmatch(r"(?:raw|primary)?mobile(?:phone)?\d*", normalized):
        return AliasSpec("MOBILE", ())
    if re.fullmatch(r"(?:raw|primary)?(?:website|webpage|url|domain)\d*", normalized):
        return AliasSpec("WEBSITE", ())

    # Typical extractor/output names from the historical agent skills.
    if normalized.endswith("firmname") or normalized.endswith("companyname") or normalized.endswith("agencyname"):
        return AliasSpec("ENTITY_NAME", ())
    if normalized.endswith("personname") or normalized.endswith("contactname") or normalized.endswith("attorneyname"):
        return AliasSpec("CONTACT_PERSON", ())
    if normalized.endswith("agentname"):
        return AliasSpec("AGENT_NAME", ())
    if normalized.endswith("countrycode") or normalized.endswith("jurisdictioncode"):
        return AliasSpec("COUNTRY", ())
    if normalized.endswith("address"):
        return AliasSpec("ADDRESS", ())
    return None


def lookup_header(header: str) -> AliasSpec | None:
    normalized = normalize_header(header)
    return _ALIAS_LOOKUP.get(normalized) or _heuristic_spec(normalized)


def score_header(row: Iterable[str]) -> float:
    score = 0.0
    seen: set[str] = set()
    for value in row:
        spec = lookup_header(value)
        if not spec or spec.canonical_field in seen:
            continue
        seen.add(spec.canonical_field)
        if spec.canonical_field in {"ENTITY_NAME", "SOURCE_ENTITY_NAME", "AGENT_NAME"}:
            score += 4.0
        elif spec.canonical_field in {
            "CREDIT_CODE", "CONTACT_PERSON", "LEGAL_REPRESENTATIVE", "PERSON_SURNAME", "PERSON_GIVEN_NAMES",
            "TRADEMARK_APPLICATION_NUMBER", "TRADEMARK_REGISTRATION_NUMBER",
        }:
            score += 2.0
        elif spec.canonical_field in {
            "EMAIL", "MOBILE", "PHONE", "PERSON_EMAIL", "PERSON_PHONE", "WEBSITE", "WHATSAPP", "CONTACT_VALUE"
        }:
            score += 1.5
        else:
            score += 1.0
    return score


def detect_header_row(table: TableData, scan_rows: int = 60) -> tuple[int, float]:
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
        normalize_header("企业名称"), normalize_header("系统匹配企业名称"),
        normalize_header("原文件导入名称"), normalize_header("统一社会信用代码"),
        normalize_header("法定代表人"), normalize_header("有效手机号"),
    }
    qcc_hits = len(qcc_markers & normalized_headers)
    if qcc_hits >= 3 and (
        "ENTITY_NAME" in canonical
        or {"SOURCE_ENTITY_NAME", "CREDIT_CODE"}.issubset(canonical)
    ):
        return "QCC_COMPANY_EXPORT", min(1.0, 0.67 + qcc_hits * 0.065)

    has_channel = bool(canonical & {
        "EMAIL", "PHONE", "MOBILE", "PERSON_EMAIL", "PERSON_PHONE", "WEBSITE", "WHATSAPP", "CONTACT_VALUE"
    })
    has_case_reference = bool(canonical & {
        "TRADEMARK_APPLICATION_NUMBER", "TRADEMARK_REGISTRATION_NUMBER"
    })
    has_name = bool(canonical & {
        "ENTITY_NAME", "SOURCE_ENTITY_NAME", "AGENT_NAME", "CONTACT_PERSON", "PERSON_SURNAME", "PERSON_GIVEN_NAMES"
    })
    if has_case_reference and has_channel and not has_name:
        return "CASE_CONTACT_TABLE", 0.85

    agent_header_markers = {
        normalize_header("firm name"), normalize_header("law firm"), normalize_header("agency name"),
        normalize_header("office name"), normalize_header("attorney"), normalize_header("attorney name"),
        normalize_header("attorney names"), normalize_header("contact person"), normalize_header("agent"),
        normalize_header("agent name"), normalize_header("代理人"), normalize_header("代理人邮箱"),
        normalize_header("律师"), normalize_header("surname"), normalize_header("other names"),
        normalize_header("raw_firm_name"), normalize_header("raw_person_name"),
    }
    agent_hits = len(agent_header_markers & normalized_headers)
    has_entity_name = bool(canonical & {"ENTITY_NAME", "SOURCE_ENTITY_NAME", "AGENT_NAME"})
    has_person_name = bool(canonical & {"CONTACT_PERSON", "PERSON_SURNAME", "PERSON_GIVEN_NAMES"})
    has_any_name = has_entity_name or has_person_name

    if has_channel and has_any_name and (agent_hits >= 1 or has_person_name):
        return "AGENT_CONTACT_LIST", min(0.98, 0.74 + agent_hits * 0.04)
    if has_channel and has_any_name:
        return "GENERIC_CONTACT_TABLE", 0.80
    if has_any_name:
        return "GENERIC_ENTITY_TABLE", 0.65
    return "UNKNOWN", 0.0
