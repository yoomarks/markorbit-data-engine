from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import re
import unicodedata
import uuid
from typing import Any


ENTITY_MENTION_NAMESPACE = uuid.UUID("c86136fb-4047-5c88-9306-d49f92c07b35")
CASE_NAMESPACE = uuid.UUID("768754a4-6dc4-5dce-8fb4-50283672746d")
CASE_RELATION_NAMESPACE = uuid.UUID("2241840b-0bb9-4de0-89dc-e781b95ab4dd")

PARTY_RELATION_NAMESPACE = uuid.UUID("f0357d4e-7c43-4c6e-bfbd-3b4d4cd6c7a9")
CARVE_OUT_NAMESPACE = uuid.UUID("b67828aa-d13b-46cc-8187-69afcb45ef4a")


NULL_LIKE = {"", "nan", "none", "null", "na", "<na>", "nat", "-", "－", "--"}
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SPACE_RE = re.compile(r"\s+")
SPACE_TAB_RE = re.compile(r"[ \t]+")
MANY_NEWLINES_RE = re.compile(r"\n{3,}")


def clean_text(value: Any, preserve_newlines: bool = False) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = CONTROL_RE.sub("", text)
    if preserve_newlines:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = SPACE_TAB_RE.sub(" ", text)
        text = MANY_NEWLINES_RE.sub("\n\n", text)
        text = text.strip()
    else:
        text = SPACE_RE.sub(" ", text).strip()
    return "" if text.lower() in NULL_LIKE else text



def strip_cn_id_mask_suffix(value: Any) -> str:
    text = clean_text(value).replace("＊", "*").strip()
    if not text:
        return ""
    text = re.sub(
        r"[（(]\s*(?:身份证|身份号码|身份证号|证件号|护照|护照号|统一社会信用代码|信用代码|ID|PASSPORT)[^）)]*[）)]\s*$",
        "",
        text,
        flags=re.I,
    ).strip()
    text = re.sub(
        r"\s*(?:身份证|身份号码|身份证号|证件号|护照|护照号|ID|PASSPORT)[:：]?\s*[A-Za-z0-9Xx*\-\s]+$",
        "",
        text,
        flags=re.I,
    ).strip()
    text = re.sub(r"(?:[\s\-_/]*[A-Za-z0-9Xx]*\*{2,})+$", "", text).strip()
    text = re.sub(r"[\s\-_/]*[0-9]{6,}[0-9Xx]?$", "", text).strip()
    text = re.sub(r"[\s\-_/]*\*+$", "", text).strip()
    return text

def normalized_match_text(value: Any) -> str:
    text = clean_text(value).lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)


DATE32_MIN = date(1900, 1, 1)
DATE32_MAX = date(2299, 12, 31)


def parse_date(value: Any) -> date | None:
    text = clean_text(value)
    if not text:
        return None
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) < 8:
        return None
    try:
        year, month, day = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
        if year <= 0 or month <= 0 or day <= 0:
            return None
        parsed = date(year, month, day)
        return parsed if DATE32_MIN <= parsed <= DATE32_MAX else None
    except ValueError:
        return None


def parse_class(value: Any) -> int | None:
    digits = re.sub(r"[^0-9]", "", clean_text(value))
    if not digits:
        return None
    number = int(digits)
    return number if 1 <= number <= 45 else None


def truthy_cn(value: Any) -> bool:
    return clean_text(value).lower() in {"是", "1", "true", "yes", "y", "有"}


def sha256_text(*parts: Any) -> str:
    payload = "|".join(clean_text(part, preserve_newlines=True) for part in parts)
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def case_uuid(application_number: str) -> uuid.UUID:
    return uuid.uuid5(CASE_NAMESPACE, f"CN|{clean_text(application_number).upper()}")


def mention_uuid(
    application_number: str,
    role: str,
    raw_name: str,
    raw_address: str,
) -> uuid.UUID:
    key = "|".join(
        (
            "CN",
            clean_text(application_number).upper(),
            role.upper(),
            normalized_match_text(raw_name),
            normalized_match_text(raw_address),
        )
    )
    return uuid.uuid5(ENTITY_MENTION_NAMESPACE, key)


@dataclass(frozen=True)
class ApplicationNumberParts:
    full: str
    family_root: str
    suffix_path: str
    filing_route: str
    number_family: str
    international_registration_number: str
    is_derived_case: bool


def application_number_parts(value: Any) -> ApplicationNumberParts:
    """Parse a CN application number without changing its jurisdiction.

    A G-prefixed number is a CNIPA case created through Madrid designation of
    China. It remains a CN trademark case. For example, G602365A maps to the
    CN family root G602365, suffix A, and WIPO IR number 602365.
    """
    full = clean_text(value).upper()
    match = re.fullmatch(r"(G?)(\d+)([A-Z]+)?", full)
    if not match:
        return ApplicationNumberParts(
            full=full,
            family_root=full,
            suffix_path="",
            filing_route="UNKNOWN",
            number_family="OTHER",
            international_registration_number="",
            is_derived_case=False,
        )

    g_prefix, digits, suffix = match.groups()
    suffix = suffix or ""
    if g_prefix:
        return ApplicationNumberParts(
            full=full,
            family_root=f"G{digits}",
            suffix_path=suffix,
            filing_route="MADRID_DESIGNATION_CN",
            number_family="CN_MADRID_G_NUMBER",
            international_registration_number=digits,
            is_derived_case=bool(suffix),
        )
    return ApplicationNumberParts(
        full=full,
        family_root=digits,
        suffix_path=suffix,
        filing_route="CN_DIRECT",
        number_family="CN_DIRECT_NUMBER",
        international_registration_number="",
        is_derived_case=bool(suffix),
    )


def parse_application_number(value: Any) -> tuple[str, str, str]:
    """Backward-compatible tuple form: full, family root, suffix path."""
    parts = application_number_parts(value)
    return parts.full, parts.family_root, parts.suffix_path


def case_relation_uuid(source_application_number: str, target_application_number: str) -> uuid.UUID:
    material = (
        f"CN|DERIVED_CASE|{clean_text(source_application_number).upper()}|"
        f"{clean_text(target_application_number).upper()}"
    )
    return uuid.uuid5(CASE_RELATION_NAMESPACE, material)


def party_relation_key(
    role: str,
    raw_name: str,
    raw_address: str,
    agent_code: str = "",
) -> str:
    material = "|".join(
        (
            clean_text(role).upper(),
            clean_text(agent_code).upper(),
            normalized_match_text(raw_name),
            normalized_match_text(raw_address),
        )
    )
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()


def party_relation_uuid(
    application_number: str,
    role: str,
    relation_key: str,
) -> uuid.UUID:
    material = "|".join(
        (
            "CN",
            clean_text(application_number).upper(),
            clean_text(role).upper(),
            clean_text(relation_key).lower(),
        )
    )
    return uuid.uuid5(PARTY_RELATION_NAMESPACE, material)


def agent_mention_uuid(agent_code: str) -> uuid.UUID:
    material = f"CN|AGENT_CODE|{clean_text(agent_code).upper()}"
    return uuid.uuid5(ENTITY_MENTION_NAMESPACE, material)


def carve_out_uuid(
    source_application_number: str,
    target_application_number: str,
    class_no: int,
) -> uuid.UUID:
    material = (
        f"CN|SCOPE_CARVE_OUT|{clean_text(source_application_number).upper()}|"
        f"{clean_text(target_application_number).upper()}|{int(class_no)}"
    )
    return uuid.uuid5(CARVE_OUT_NAMESPACE, material)


CN_REGION_CODES = {
    "北京市": "CN-BJ", "天津市": "CN-TJ", "上海市": "CN-SH", "重庆市": "CN-CQ",
    "河北省": "CN-HE", "山西省": "CN-SX", "辽宁省": "CN-LN", "吉林省": "CN-JL",
    "黑龙江省": "CN-HL", "江苏省": "CN-JS", "浙江省": "CN-ZJ", "安徽省": "CN-AH",
    "福建省": "CN-FJ", "江西省": "CN-JX", "山东省": "CN-SD", "河南省": "CN-HA",
    "湖北省": "CN-HB", "湖南省": "CN-HN", "广东省": "CN-GD", "海南省": "CN-HI",
    "四川省": "CN-SC", "贵州省": "CN-GZ", "云南省": "CN-YN", "陕西省": "CN-SN",
    "甘肃省": "CN-GS", "青海省": "CN-QH", "台湾省": "CN-TW",
    "内蒙古自治区": "CN-NM", "广西壮族自治区": "CN-GX", "西藏自治区": "CN-XZ",
    "宁夏回族自治区": "CN-NX", "新疆维吾尔自治区": "CN-XJ",
    "香港特别行政区": "CN-HK", "澳门特别行政区": "CN-MO",
}


CN_REGION_ALIASES = {
    "北京": ("北京市", "CN-BJ"), "天津": ("天津市", "CN-TJ"),
    "上海": ("上海市", "CN-SH"), "重庆": ("重庆市", "CN-CQ"),
    "河北": ("河北省", "CN-HE"), "山西": ("山西省", "CN-SX"),
    "辽宁": ("辽宁省", "CN-LN"), "吉林": ("吉林省", "CN-JL"),
    "黑龙江": ("黑龙江省", "CN-HL"), "江苏": ("江苏省", "CN-JS"),
    "浙江": ("浙江省", "CN-ZJ"), "安徽": ("安徽省", "CN-AH"),
    "福建": ("福建省", "CN-FJ"), "江西": ("江西省", "CN-JX"),
    "山东": ("山东省", "CN-SD"), "河南": ("河南省", "CN-HA"),
    "湖北": ("湖北省", "CN-HB"), "湖南": ("湖南省", "CN-HN"),
    "广东": ("广东省", "CN-GD"), "海南": ("海南省", "CN-HI"),
    "四川": ("四川省", "CN-SC"), "贵州": ("贵州省", "CN-GZ"),
    "云南": ("云南省", "CN-YN"), "陕西": ("陕西省", "CN-SN"),
    "甘肃": ("甘肃省", "CN-GS"), "青海": ("青海省", "CN-QH"),
    "内蒙古": ("内蒙古自治区", "CN-NM"), "广西": ("广西壮族自治区", "CN-GX"),
    "西藏": ("西藏自治区", "CN-XZ"), "宁夏": ("宁夏回族自治区", "CN-NX"),
    "新疆": ("新疆维吾尔自治区", "CN-XJ"),
}

SPECIAL_CN_REGIONS = {
    "香港": ("HK", "", "香港"),
    "澳门": ("MO", "", "澳门"),
    "台湾": ("TW", "", "台湾"),
}


FOREIGN_TERMS = {
    "美国": "US", "英国": "GB", "日本": "JP", "韩国": "KR", "德国": "DE",
    "法国": "FR", "意大利": "IT", "瑞士": "CH", "荷兰": "NL", "新加坡": "SG",
    "加拿大": "CA", "澳大利亚": "AU", "俄罗斯": "RU", "印度": "IN",
}


@dataclass(frozen=True)
class GeoResult:
    country_code: str
    region_code: str
    city: str
    confidence: float


def infer_geo(address_cn: str, address_foreign: str = "") -> GeoResult:
    cn = clean_text(address_cn)
    foreign = clean_text(address_foreign)

    for term, (country_code, region_code, city) in SPECIAL_CN_REGIONS.items():
        if term in cn:
            return GeoResult(country_code, region_code, city, 0.95)

    for term, code in FOREIGN_TERMS.items():
        if term in cn:
            return GeoResult(code, "", "", 0.9)

    region_code = ""
    region_name = ""
    for name, code in CN_REGION_CODES.items():
        if name in cn:
            region_name, region_code = name, code
            break
    if not region_code:
        for alias, (name, code) in CN_REGION_ALIASES.items():
            if alias in cn:
                region_name, region_code = name, code
                break

    municipality = {"北京市", "天津市", "上海市", "重庆市"}
    if region_name in municipality:
        return GeoResult("CN", region_code, region_name, 0.95)

    city = ""
    match = re.search(r"([\u4e00-\u9fff]{2,12}(?:自治州|地区|盟|市))", cn)
    if match:
        candidate = match.group(1)
        if candidate not in CN_REGION_CODES:
            city = candidate

    if cn:
        return GeoResult("CN", region_code, city, 0.85 if region_code else 0.6)
    if foreign:
        return GeoResult("", "", "", 0.2)
    return GeoResult("", "", "", 0.0)
