from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import urlsplit
from typing import Any


NULL_LIKE = {"", "nan", "none", "null", "na", "n/a", "<na>", "nat", "-", "－", "--", "无", "暂无"}
SPACE_RE = re.compile(r"\s+")
MULTI_SPLIT_RE = re.compile(r"[;；\n\r]+")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
CN_MOBILE_RE = re.compile(r"^(?:\+?86)?1[3-9]\d{9}$")
CN_LANDLINE_RE = re.compile(r"^(?:\+?86)?0\d{9,11}$")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = SPACE_RE.sub(" ", text).strip()
    return "" if text.casefold() in NULL_LIKE else text


def normalized_match_text(value: Any) -> str:
    text = clean_text(value).casefold()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)


def normalize_header(value: Any) -> str:
    text = clean_text(value).casefold()
    return re.sub(r"[\s_\-—–:：/\\()（）\[\]【】.]+", "", text)


def split_values(value: Any) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    values = [clean_text(part) for part in MULTI_SPLIT_RE.split(text)]
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not item:
            continue
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def normalize_email(value: Any) -> str:
    text = clean_text(value).strip("<>()[]{}，,;；").casefold()
    return text if EMAIL_RE.fullmatch(text) else ""


def normalize_phone(value: Any, country_code: str = "") -> str:
    text = clean_text(value)
    if not text:
        return ""
    ext_match = re.search(r"(?:ext\.?|x|分机)\s*(\d+)$", text, flags=re.I)
    ext = ext_match.group(1) if ext_match else ""
    if ext_match:
        text = text[: ext_match.start()]
    has_plus = text.lstrip().startswith("+")
    digits = re.sub(r"\D", "", text)
    if not digits:
        return ""
    country = clean_text(country_code).upper()
    if country == "CN":
        if len(digits) == 11 and digits.startswith("1"):
            normalized = f"+86{digits}"
        elif digits.startswith("86") and len(digits) >= 12:
            normalized = f"+{digits}"
        elif has_plus:
            normalized = f"+{digits}"
        else:
            normalized = digits
    else:
        normalized = f"+{digits}" if has_plus else digits
    return f"{normalized}x{ext}" if ext else normalized


def classify_phone(value: Any, *, declared_type: str = "", country_code: str = "") -> str:
    declared = clean_text(declared_type).upper()
    normalized = normalize_phone(value, country_code)
    bare = normalized.split("x", 1)[0].replace("+", "")
    if declared in {"MOBILE", "LANDLINE", "PHONE_UNKNOWN"}:
        return declared
    if country_code.upper() == "CN":
        cn = bare[2:] if bare.startswith("86") else bare
        if re.fullmatch(r"1[3-9]\d{9}", cn):
            return "MOBILE"
        if re.fullmatch(r"0\d{9,11}", cn):
            return "LANDLINE"
    raw_compact = re.sub(r"[\s()\-]", "", clean_text(value))
    if CN_MOBILE_RE.fullmatch(raw_compact):
        return "MOBILE"
    if CN_LANDLINE_RE.fullmatch(raw_compact):
        return "LANDLINE"
    return "PHONE_UNKNOWN"


def normalize_website(value: Any) -> str:
    text = clean_text(value).strip().rstrip("/.,;；")
    if not text:
        return ""
    candidate = text if "://" in text else f"//{text}"
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return ""
    host = (parts.hostname or "").casefold().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return ""
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    return host


_COUNTRY_ALIASES = {
    "cn": "CN", "china": "CN", "中国": "CN", "中国大陆": "CN",
    "us": "US", "usa": "US", "unitedstates": "US", "unitedstatesofamerica": "US", "美国": "US",
    "gb": "GB", "uk": "GB", "unitedkingdom": "GB", "英国": "GB",
    "hk": "HK", "hongkong": "HK", "香港": "HK",
    "sg": "SG", "singapore": "SG", "新加坡": "SG",
    "jp": "JP", "japan": "JP", "日本": "JP",
    "kr": "KR", "southkorea": "KR", "korea": "KR", "韩国": "KR",
    "au": "AU", "australia": "AU", "澳大利亚": "AU",
    "ca": "CA", "canada": "CA", "加拿大": "CA",
    "de": "DE", "germany": "DE", "德国": "DE",
    "fr": "FR", "france": "FR", "法国": "FR",
}


def normalize_country_code(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    folded = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text.casefold())
    if folded in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[folded]
    upper = text.upper()
    return upper if re.fullmatch(r"[A-Z]{2}", upper) else ""


def normalize_credit_code(value: Any) -> str:
    text = re.sub(r"[^0-9A-Za-z]", "", clean_text(value)).upper()
    return text


def normalize_channel(channel_type: str, value: Any, *, country_code: str = "") -> tuple[str, str]:
    declared = clean_text(channel_type).upper()
    if declared == "EMAIL":
        return "EMAIL", normalize_email(value)
    if declared == "WEBSITE":
        return "WEBSITE", normalize_website(value)
    if declared in {"MOBILE", "LANDLINE", "PHONE", "PHONE_UNKNOWN"}:
        normalized = normalize_phone(value, country_code)
        actual = classify_phone(value, declared_type="" if declared == "PHONE" else declared, country_code=country_code)
        return actual, normalized
    if declared == "WHATSAPP":
        return "WHATSAPP", normalize_phone(value, country_code)
    return declared, clean_text(value)


def sha256_text(*parts: Any) -> str:
    payload = "|".join(clean_text(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()
