from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import json
import re
import time
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit
import uuid

import phonenumbers
from phonenumbers import NumberParseException
import pycountry

from app.db import postgres_conn


COUNTRY_INFERENCE_VERSION = "CONTACT_COUNTRY_INFERENCE_V1"
COUNTRY_INFERENCE_LOCK = "markorbit:contact:country-inference"
DEFAULT_MIN_CONFIDENCE = 0.86
DEFAULT_MIN_MARGIN = 0.15
DEFAULT_BATCH_SIZE = 500

_GENERIC_CCTLD = {
    "ai",
    "cc",
    "co",
    "fm",
    "io",
    "me",
    "tv",
    "ws",
}
_FREE_EMAIL_DOMAINS = {
    "126.com",
    "163.com",
    "aol.com",
    "gmail.com",
    "googlemail.com",
    "hotmail.com",
    "icloud.com",
    "live.com",
    "mail.com",
    "msn.com",
    "outlook.com",
    "proton.me",
    "protonmail.com",
    "qq.com",
    "sina.com",
    "sohu.com",
    "yahoo.com",
    "yandex.com",
}
# These are legitimate ISO country names, but as bare words inside an address they
# are too ambiguous (US states, personal/place names, etc.) for a high-confidence
# country inference. An explicit Country field may still resolve them.
_AMBIGUOUS_FREE_TEXT_COUNTRY_NAMES = {
    "chad",
    "georgia",
    "jordan",
    "mali",
    "niger",
}

# pycountry provides the ISO English names/codes. These aliases cover common
# Chinese source material and business-data spellings/abbreviations not reliably
# handled by ISO lookup.
_COUNTRY_ALIASES = {
    "uk": "GB",
    "great britain": "GB",
    "usa": "US",
    "u.s.a.": "US",
    "prc": "CN",
    "uae": "AE",
    "south korea": "KR",
    "republic of korea": "KR",
    "russia": "RU",
    "vietnam": "VN",
    "czech republic": "CZ",
    "taiwan": "TW",
    "macau": "MO",
    "macao": "MO",
    "ivory coast": "CI",
    "tanzania": "TZ",
    "moldova": "MD",
    "中国": "CN",
    "中国大陆": "CN",
    "中华人民共和国": "CN",
    "美国": "US",
    "英国": "GB",
    "香港": "HK",
    "中国香港": "HK",
    "澳门": "MO",
    "中国澳门": "MO",
    "台湾": "TW",
    "中国台湾": "TW",
    "新加坡": "SG",
    "日本": "JP",
    "韩国": "KR",
    "南韩": "KR",
    "澳大利亚": "AU",
    "澳洲": "AU",
    "加拿大": "CA",
    "德国": "DE",
    "法国": "FR",
    "意大利": "IT",
    "西班牙": "ES",
    "葡萄牙": "PT",
    "荷兰": "NL",
    "比利时": "BE",
    "瑞士": "CH",
    "奥地利": "AT",
    "瑞典": "SE",
    "挪威": "NO",
    "芬兰": "FI",
    "丹麦": "DK",
    "爱尔兰": "IE",
    "冰岛": "IS",
    "卢森堡": "LU",
    "波兰": "PL",
    "捷克": "CZ",
    "斯洛伐克": "SK",
    "匈牙利": "HU",
    "罗马尼亚": "RO",
    "保加利亚": "BG",
    "希腊": "GR",
    "克罗地亚": "HR",
    "塞尔维亚": "RS",
    "斯洛文尼亚": "SI",
    "立陶宛": "LT",
    "拉脱维亚": "LV",
    "爱沙尼亚": "EE",
    "俄罗斯": "RU",
    "乌克兰": "UA",
    "土耳其": "TR",
    "以色列": "IL",
    "阿联酋": "AE",
    "阿拉伯联合酋长国": "AE",
    "沙特": "SA",
    "沙特阿拉伯": "SA",
    "卡塔尔": "QA",
    "科威特": "KW",
    "巴林": "BH",
    "阿曼": "OM",
    "约旦": "JO",
    "黎巴嫩": "LB",
    "伊朗": "IR",
    "伊拉克": "IQ",
    "印度": "IN",
    "巴基斯坦": "PK",
    "孟加拉": "BD",
    "孟加拉国": "BD",
    "斯里兰卡": "LK",
    "尼泊尔": "NP",
    "蒙古": "MN",
    "印度尼西亚": "ID",
    "印尼": "ID",
    "马来西亚": "MY",
    "泰国": "TH",
    "越南": "VN",
    "菲律宾": "PH",
    "新西兰": "NZ",
    "墨西哥": "MX",
    "巴西": "BR",
    "阿根廷": "AR",
    "智利": "CL",
    "秘鲁": "PE",
    "哥伦比亚": "CO",
    "乌拉圭": "UY",
    "巴拉圭": "PY",
    "厄瓜多尔": "EC",
    "委内瑞拉": "VE",
    "南非": "ZA",
    "埃及": "EG",
    "摩洛哥": "MA",
    "阿尔及利亚": "DZ",
    "突尼斯": "TN",
    "肯尼亚": "KE",
    "尼日利亚": "NG",
    "埃塞俄比亚": "ET",
    "加纳": "GH",
    "坦桑尼亚": "TZ",
}

_COUNTRY_FIELD_HINTS = (
    "country",
    "nation",
    "国家",
    "国别",
    "国家地区",
    "国家/地区",
)
_CITY_FIELD_HINTS = ("city", "town", "municipality", "城市", "市")
_ADDRESS_FIELD_HINTS = (
    "address",
    "location",
    "office",
    "street",
    "地址",
    "住所",
    "所在地",
)


SCHEMA_SQL = r"""
CREATE SCHEMA IF NOT EXISTS contact;

CREATE TABLE IF NOT EXISTS contact.country_inference_run (
    run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_version text NOT NULL,
    status text NOT NULL,
    apply_mode boolean NOT NULL DEFAULT false,
    min_confidence numeric(5,4) NOT NULL,
    min_margin numeric(5,4) NOT NULL,
    batch_size integer NOT NULL,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED', 'BUSY'))
);

CREATE TABLE IF NOT EXISTS contact.entity_country_inference (
    entity_id uuid PRIMARY KEY REFERENCES entity.entity(entity_id) ON DELETE CASCADE,
    last_run_id uuid REFERENCES contact.country_inference_run(run_id) ON DELETE SET NULL,
    rule_version text NOT NULL,
    status text NOT NULL,
    country_code char(2),
    confidence numeric(5,4) NOT NULL DEFAULT 0,
    runner_up_country_code char(2),
    runner_up_confidence numeric(5,4) NOT NULL DEFAULT 0,
    evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    reason_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
    first_inferred_at timestamptz NOT NULL DEFAULT now(),
    last_inferred_at timestamptz NOT NULL DEFAULT now(),
    applied_at timestamptz,
    CHECK (status IN ('ACCEPTED', 'CONFLICT', 'INSUFFICIENT'))
);

CREATE INDEX IF NOT EXISTS ix_contact_country_inference_status
ON contact.entity_country_inference(status, confidence DESC);

CREATE INDEX IF NOT EXISTS ix_contact_country_inference_country
ON contact.entity_country_inference(country_code, confidence DESC)
WHERE country_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_contact_country_inference_applied
ON contact.entity_country_inference(applied_at DESC)
WHERE applied_at IS NOT NULL;
"""


@dataclass(frozen=True)
class Evidence:
    country_code: str
    kind: str
    weight: float
    value: str
    source: str


@dataclass(frozen=True)
class Inference:
    status: str
    country_code: str
    confidence: float
    runner_up_country_code: str
    runner_up_confidence: float
    evidence: tuple[Evidence, ...]
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class ReferenceModels:
    city_country: dict[str, tuple[str, float, int]]
    domain_country: dict[str, tuple[str, float, int]]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _normalize_key(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)


def _normalize_city(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"\b(city|municipality|prefecture|province)\b", " ", text)
    text = re.sub(r"市$", "", text)
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)


def _country_from_explicit_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = _normalize_key(text)
    for alias, code in _COUNTRY_ALIASES.items():
        if normalized == _normalize_key(alias):
            return code
    try:
        country = pycountry.countries.lookup(text)
    except LookupError:
        return ""
    return str(country.alpha_2).upper()


def _country_name_patterns() -> tuple[tuple[re.Pattern[str], str], ...]:
    patterns: list[tuple[re.Pattern[str], str]] = []
    names: dict[str, str] = {}
    for country in pycountry.countries:
        code = str(country.alpha_2).upper()
        for attribute in ("name", "official_name", "common_name"):
            name = str(getattr(country, attribute, "") or "").strip()
            if len(name) >= 4 and name.casefold() not in _AMBIGUOUS_FREE_TEXT_COUNTRY_NAMES:
                names[name.casefold()] = code
    for alias, code in _COUNTRY_ALIASES.items():
        folded = alias.casefold()
        if folded not in _AMBIGUOUS_FREE_TEXT_COUNTRY_NAMES:
            names[folded] = code
    for name, code in sorted(names.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(r"[\u4e00-\u9fff]", name):
            pattern = re.compile(re.escape(name), re.I)
        else:
            pattern = re.compile(rf"(?<![a-z]){re.escape(name)}(?![a-z])", re.I)
        patterns.append((pattern, code))
    return tuple(patterns)


_COUNTRY_NAME_PATTERNS = _country_name_patterns()


def _countries_in_text(value: Any) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    result = {code for pattern, code in _COUNTRY_NAME_PATTERNS if pattern.search(text)}
    # Avoid overlapping Chinese aliases turning “中国香港/澳门/台湾” into both the
    # region and CN. These regions have distinct ISO country codes in contact data.
    if "HK" in result and "香港" in text:
        result.discard("CN")
    if "MO" in result and "澳门" in text:
        result.discard("CN")
    if "TW" in result and "台湾" in text:
        result.discard("CN")
    return result


def _host_from_channel(channel_type: str, value: Any) -> str:
    text = str(value or "").strip().casefold().strip(". ")
    if not text:
        return ""
    if channel_type == "EMAIL":
        if "@" not in text:
            return ""
        return text.rsplit("@", 1)[1].strip(". ")
    if channel_type == "WEBSITE":
        candidate = text if "://" in text else f"//{text}"
        try:
            host = (urlsplit(candidate).hostname or "").casefold().strip(".")
        except ValueError:
            return ""
        return host[4:] if host.startswith("www.") else host
    return ""


def _cctld_country(host: str) -> tuple[str, float] | None:
    if not host or "." not in host:
        return None
    suffix = host.rsplit(".", 1)[1].casefold()
    if suffix in _GENERIC_CCTLD:
        return None
    if suffix == "uk":
        return "GB", 0.92
    if len(suffix) != 2:
        return None
    country = pycountry.countries.get(alpha_2=suffix.upper())
    if country is None:
        return None
    return str(country.alpha_2).upper(), 0.90


def _phone_country(value: Any) -> tuple[str, float] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    compact = re.sub(r"[\s()\-.]", "", raw)
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if not compact.startswith("+"):
        return None
    compact = re.sub(r"x\d+$", "", compact, flags=re.I)
    try:
        parsed = phonenumbers.parse(compact, None)
    except NumberParseException:
        return None
    region = str(phonenumbers.region_code_for_number(parsed) or "").upper()
    if not re.fullmatch(r"[A-Z]{2}", region):
        return None
    if phonenumbers.is_valid_number(parsed):
        return region, 0.94
    if phonenumbers.is_possible_number(parsed):
        return region, 0.86
    return None


def _combine_confidence(weights: Iterable[float]) -> float:
    remaining = 1.0
    for weight in weights:
        remaining *= 1.0 - max(0.0, min(float(weight), 0.999))
    return min(0.999, 1.0 - remaining)


def infer_from_evidence(
    evidence: Iterable[Evidence],
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    min_margin: float = DEFAULT_MIN_MARGIN,
) -> Inference:
    deduped: dict[tuple[str, str, str], Evidence] = {}
    for item in evidence:
        code = item.country_code.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", code):
            continue
        key = (item.kind, code, _normalize_key(item.value))
        current = deduped.get(key)
        if current is None or item.weight > current.weight:
            deduped[key] = Evidence(code, item.kind, item.weight, item.value, item.source)

    grouped: dict[str, list[Evidence]] = defaultdict(list)
    for item in deduped.values():
        grouped[item.country_code].append(item)
    scores = {
        country: _combine_confidence(item.weight for item in items)
        for country, items in grouped.items()
    }
    ranking = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    if not ranking:
        return Inference("INSUFFICIENT", "", 0.0, "", 0.0, tuple(), ("NO_COUNTRY_EVIDENCE",))

    top_country, top_score = ranking[0]
    runner_country, runner_score = ranking[1] if len(ranking) > 1 else ("", 0.0)
    margin = top_score - runner_score
    if top_score >= min_confidence and margin >= min_margin:
        status = "ACCEPTED"
        reasons = ("CONFIDENCE_THRESHOLD_MET", "COUNTRY_MARGIN_MET")
    elif runner_score >= 0.70 and margin < min_margin:
        status = "CONFLICT"
        reasons = ("COMPETING_COUNTRY_EVIDENCE", "COUNTRY_MARGIN_NOT_MET")
    else:
        status = "INSUFFICIENT"
        reasons = ("CONFIDENCE_OR_MARGIN_NOT_MET",)

    ordered_evidence = tuple(
        sorted(
            deduped.values(),
            key=lambda item: (item.country_code != top_country, -item.weight, item.kind, item.value),
        )
    )
    return Inference(
        status,
        top_country,
        round(top_score, 4),
        runner_country,
        round(runner_score, 4),
        ordered_evidence,
        reasons,
    )


def ensure_country_inference_schema() -> None:
    # Fresh/old volumes must have the additive contact/entity foundations before
    # the inference audit tables and indexes are installed.
    from app.contact_ingest.migrations import ensure_contact_schema

    ensure_contact_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()


def _build_city_model() -> dict[str, tuple[str, float, int]]:
    # Entity rows activated by this engine are excluded from training. Explicit
    # entity_mention country/city pairs remain source-grounded training evidence.
    sql = """
        SELECT e.city, e.country_code
        FROM entity.entity AS e
        LEFT JOIN contact.entity_country_inference AS ci
          ON ci.entity_id = e.entity_id AND ci.applied_at IS NOT NULL
        WHERE e.country_code IS NOT NULL
          AND e.city IS NOT NULL AND btrim(e.city) <> ''
          AND ci.entity_id IS NULL
        UNION ALL
        SELECT city, country_code
        FROM entity.entity_mention
        WHERE country_code IS NOT NULL AND city IS NOT NULL AND btrim(city) <> ''
    """
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            for row in cur.fetchall():
                city = _normalize_city(row.get("city"))
                country = str(row.get("country_code") or "").upper()
                if city and re.fullmatch(r"[A-Z]{2}", country):
                    counts[city][country] += 1
    model: dict[str, tuple[str, float, int]] = {}
    for city, per_country in counts.items():
        total = sum(per_country.values())
        country, top = per_country.most_common(1)[0]
        dominance = top / total if total else 0.0
        if top >= 3 and dominance >= 0.90:
            model[city] = (country, dominance, total)
    return model


def _build_domain_model() -> dict[str, tuple[str, float, int]]:
    # Only source-grounded entity countries train domain mappings; activated
    # inference rows are explicitly excluded so the model cannot self-reinforce.
    sql = """
        WITH channel_owner AS (
            SELECT c.channel_type, c.normalized_value, c.entity_id
            FROM contact.channel AS c
            WHERE c.entity_id IS NOT NULL
            UNION ALL
            SELECT c.channel_type, c.normalized_value, r.entity_id
            FROM contact.channel AS c
            JOIN contact.entity_person_relation AS r ON r.person_id = c.person_id
            WHERE c.person_id IS NOT NULL
        ), domains AS (
            SELECT
                e.country_code,
                CASE
                    WHEN co.channel_type = 'EMAIL' THEN split_part(co.normalized_value, '@', 2)
                    WHEN co.channel_type = 'WEBSITE' THEN co.normalized_value
                    ELSE ''
                END AS domain
            FROM channel_owner AS co
            JOIN entity.entity AS e ON e.entity_id = co.entity_id
            LEFT JOIN contact.entity_country_inference AS ci
              ON ci.entity_id = e.entity_id AND ci.applied_at IS NOT NULL
            WHERE e.country_code IS NOT NULL
              AND ci.entity_id IS NULL
              AND co.channel_type IN ('EMAIL', 'WEBSITE')
        )
        SELECT lower(domain) AS domain, country_code, count(*) AS row_count
        FROM domains
        WHERE domain <> ''
        GROUP BY lower(domain), country_code
    """
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            for row in cur.fetchall():
                domain = str(row.get("domain") or "").casefold().strip(".")
                country = str(row.get("country_code") or "").upper()
                if domain and re.fullmatch(r"[A-Z]{2}", country):
                    counts[domain][country] += int(row.get("row_count") or 0)
    model: dict[str, tuple[str, float, int]] = {}
    for domain, per_country in counts.items():
        if domain in _FREE_EMAIL_DOMAINS:
            continue
        total = sum(per_country.values())
        country, top = per_country.most_common(1)[0]
        dominance = top / total if total else 0.0
        if top >= 1 and dominance >= 0.95:
            model[domain] = (country, dominance, total)
    return model


def build_reference_models() -> ReferenceModels:
    return ReferenceModels(city_country=_build_city_model(), domain_country=_build_domain_model())


def _candidate_batch(after_entity_id: str | None, batch_size: int) -> list[dict[str, Any]]:
    params: list[Any] = []
    after = ""
    if after_entity_id:
        after = "AND e.entity_id > %s::uuid"
        params.append(after_entity_id)
    params.append(batch_size)
    sql = f"""
        SELECT e.entity_id::text, e.canonical_name, e.normalized_address,
               COALESCE(e.region_code, '') AS region_code,
               COALESCE(e.city, '') AS city
        FROM entity.entity AS e
        WHERE e.country_code IS NULL
          {after}
          AND NOT EXISTS (
              SELECT 1
              FROM contact.entity_country_inference AS active_ci
              WHERE active_ci.entity_id = e.entity_id
                AND active_ci.status = 'ACCEPTED'
                AND active_ci.applied_at IS NOT NULL
          )
          AND (
              EXISTS (SELECT 1 FROM contact.raw_record rr WHERE rr.entity_id = e.entity_id)
              OR EXISTS (
                  SELECT 1 FROM contact.entity_person_relation r WHERE r.entity_id = e.entity_id
              )
              OR EXISTS (
                  SELECT 1 FROM contact.channel c WHERE c.entity_id = e.entity_id
              )
          )
        ORDER BY e.entity_id
        LIMIT %s
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def _load_context(entity_ids: list[str]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    context: dict[str, dict[str, list[dict[str, Any]]]] = {
        entity_id: {"raw": [], "channels": [], "mentions": [], "identifiers": []}
        for entity_id in entity_ids
    }
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rr.entity_id::text, rr.raw_data, rr.source_profile,
                       COALESCE(s.default_country_code, '') AS default_country_code,
                       COALESCE(s.source_scope, '') AS source_scope,
                       COALESCE(s.source_segment, '') AS source_segment,
                       s.source_name
                FROM contact.raw_record AS rr
                JOIN contact.source AS s ON s.source_id = rr.source_id
                WHERE rr.entity_id = ANY(%s::uuid[])
                """,
                (entity_ids,),
            )
            for row in cur.fetchall():
                context[str(row["entity_id"])]["raw"].append(dict(row))

            cur.execute(
                """
                SELECT DISTINCT owner.entity_id::text, c.channel_type,
                       c.channel_value, c.normalized_value
                FROM contact.channel AS c
                JOIN LATERAL (
                    SELECT c.entity_id AS entity_id WHERE c.entity_id IS NOT NULL
                    UNION ALL
                    SELECT r.entity_id
                    FROM contact.entity_person_relation AS r
                    WHERE c.person_id IS NOT NULL AND r.person_id = c.person_id
                ) AS owner ON true
                WHERE owner.entity_id = ANY(%s::uuid[])
                  AND c.channel_type IN (
                      'EMAIL', 'WEBSITE', 'PHONE_UNKNOWN', 'LANDLINE',
                      'MOBILE', 'WHATSAPP'
                  )
                """,
                (entity_ids,),
            )
            for row in cur.fetchall():
                context[str(row["entity_id"])]["channels"].append(dict(row))

            cur.execute(
                """
                SELECT entity_id::text, COALESCE(country_code, '') AS country_code,
                       raw_address, COALESCE(city, '') AS city,
                       COALESCE(region_code, '') AS region_code, role
                FROM entity.entity_mention
                WHERE entity_id = ANY(%s::uuid[])
                """,
                (entity_ids,),
            )
            for row in cur.fetchall():
                context[str(row["entity_id"])]["mentions"].append(dict(row))

            cur.execute(
                """
                SELECT entity_id::text, identifier_type, identifier_value,
                       COALESCE(country_code, '') AS country_code
                FROM entity.entity_identifier
                WHERE entity_id = ANY(%s::uuid[]) AND country_code IS NOT NULL
                """,
                (entity_ids,),
            )
            for row in cur.fetchall():
                context[str(row["entity_id"])]["identifiers"].append(dict(row))
    return context


def _city_evidence(
    value: Any,
    models: ReferenceModels,
    *,
    source: str,
    weight: float,
) -> list[Evidence]:
    city = _normalize_city(value)
    if not city:
        return []
    match = models.city_country.get(city)
    if not match:
        return []
    country, dominance, sample_count = match
    adjusted = min(weight, weight * dominance)
    return [
        Evidence(
            country,
            "CITY_CORPUS_MODEL",
            adjusted,
            str(value),
            f"{source};n={sample_count};share={dominance:.3f}",
        )
    ]


def _address_city_tokens(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = [item.strip() for item in re.split(r"[,，;；|/\\\n]+", text) if item.strip()]
    return parts[:12]


def _entity_evidence(
    entity: dict[str, Any],
    context: dict[str, list[dict[str, Any]]],
    models: ReferenceModels,
) -> list[Evidence]:
    evidence: list[Evidence] = []

    for identifier in context["identifiers"]:
        country = str(identifier.get("country_code") or "").upper()
        if country:
            evidence.append(
                Evidence(
                    country,
                    "IDENTIFIER_COUNTRY",
                    0.98,
                    str(identifier.get("identifier_value") or ""),
                    str(identifier.get("identifier_type") or "identifier"),
                )
            )

    for mention in context["mentions"]:
        country = str(mention.get("country_code") or "").upper()
        if country:
            evidence.append(
                Evidence(
                    country,
                    "TRADEMARK_MENTION_COUNTRY",
                    0.98,
                    country,
                    str(mention.get("role") or "mention"),
                )
            )
        address = str(mention.get("raw_address") or "")
        for code in _countries_in_text(address):
            evidence.append(
                Evidence(
                    code,
                    "MENTION_ADDRESS_COUNTRY_NAME",
                    0.97,
                    address,
                    "entity.entity_mention.raw_address",
                )
            )
        if country and mention.get("city"):
            evidence.append(
                Evidence(
                    country,
                    "MENTION_CITY_WITH_COUNTRY",
                    0.82,
                    str(mention["city"]),
                    "entity.entity_mention",
                )
            )

    if entity.get("city"):
        evidence.extend(
            _city_evidence(entity["city"], models, source="entity.entity.city", weight=0.76)
        )

    for raw in context["raw"]:
        default_country = str(raw.get("default_country_code") or "").upper()
        if default_country:
            evidence.append(
                Evidence(
                    default_country,
                    "SOURCE_DEFAULT_COUNTRY",
                    0.97,
                    default_country,
                    str(raw.get("source_name") or "source"),
                )
            )
        data = raw.get("raw_data") or {}
        if not isinstance(data, dict):
            continue
        for key, raw_value in data.items():
            value = str(raw_value or "").strip()
            if not value:
                continue
            normalized_key = _normalize_key(key)
            country_field = any(
                _normalize_key(hint) in normalized_key for hint in _COUNTRY_FIELD_HINTS
            ) and "nationality" not in normalized_key
            if country_field:
                country = _country_from_explicit_value(value)
                if country:
                    evidence.append(
                        Evidence(country, "RAW_EXPLICIT_COUNTRY_FIELD", 0.995, value, str(key))
                    )
                    continue
            is_city = any(
                _normalize_key(hint) == normalized_key
                or _normalize_key(hint) in normalized_key
                for hint in _CITY_FIELD_HINTS
            )
            is_address = any(
                _normalize_key(hint) in normalized_key for hint in _ADDRESS_FIELD_HINTS
            )
            if is_city:
                evidence.extend(
                    _city_evidence(value, models, source=f"raw:{key}", weight=0.78)
                )
            if is_address:
                for code in _countries_in_text(value):
                    evidence.append(
                        Evidence(code, "RAW_ADDRESS_COUNTRY_NAME", 0.98, value, str(key))
                    )
                for token in _address_city_tokens(value):
                    evidence.extend(
                        _city_evidence(
                            token,
                            models,
                            source=f"raw-address:{key}",
                            weight=0.60,
                        )
                    )

    # Entity normalized_address can still contain a country name even when the
    # source did not map a dedicated country field.
    normalized_address = str(entity.get("normalized_address") or "")
    for code in _countries_in_text(normalized_address):
        evidence.append(
            Evidence(
                code,
                "ENTITY_ADDRESS_COUNTRY_NAME",
                0.96,
                normalized_address,
                "entity.entity.normalized_address",
            )
        )

    for channel in context["channels"]:
        channel_type = str(channel.get("channel_type") or "").upper()
        raw_value = str(channel.get("channel_value") or "")
        normalized_value = str(channel.get("normalized_value") or "")
        if channel_type in {"PHONE_UNKNOWN", "LANDLINE", "MOBILE", "WHATSAPP"}:
            phone = _phone_country(raw_value or normalized_value)
            if phone:
                country, weight = phone
                evidence.append(
                    Evidence(
                        country,
                        "INTERNATIONAL_PHONE",
                        weight,
                        raw_value or normalized_value,
                        channel_type,
                    )
                )
            continue
        if channel_type not in {"EMAIL", "WEBSITE"}:
            continue
        host = _host_from_channel(channel_type, normalized_value or raw_value)
        if not host:
            continue
        known = models.domain_country.get(host)
        if known and host not in _FREE_EMAIL_DOMAINS:
            country, dominance, sample_count = known
            evidence.append(
                Evidence(
                    country,
                    "KNOWN_CORPORATE_DOMAIN",
                    min(0.94, 0.90 + 0.04 * dominance),
                    host,
                    f"corpus;n={sample_count};share={dominance:.3f}",
                )
            )
        cctld = _cctld_country(host)
        if cctld:
            country, weight = cctld
            evidence.append(
                Evidence(country, "COUNTRY_CODE_DOMAIN", weight, host, channel_type)
            )

    return evidence


def _upsert_inference(
    cur,
    run_id: str,
    entity_id: str,
    inference: Inference,
    applied: bool,
) -> None:
    cur.execute(
        """
        INSERT INTO contact.entity_country_inference AS current (
            entity_id, last_run_id, rule_version, status, country_code, confidence,
            runner_up_country_code, runner_up_confidence, evidence, reason_codes,
            applied_at
        ) VALUES (
            %s, %s, %s, %s, NULLIF(%s, ''), %s,
            NULLIF(%s, ''), %s, %s::jsonb, %s::jsonb,
            CASE WHEN %s THEN now() ELSE NULL END
        )
        ON CONFLICT (entity_id)
        DO UPDATE SET
            last_run_id = EXCLUDED.last_run_id,
            rule_version = EXCLUDED.rule_version,
            status = EXCLUDED.status,
            country_code = EXCLUDED.country_code,
            confidence = EXCLUDED.confidence,
            runner_up_country_code = EXCLUDED.runner_up_country_code,
            runner_up_confidence = EXCLUDED.runner_up_confidence,
            evidence = EXCLUDED.evidence,
            reason_codes = EXCLUDED.reason_codes,
            last_inferred_at = now(),
            applied_at = CASE
                WHEN EXCLUDED.applied_at IS NOT NULL THEN EXCLUDED.applied_at
                ELSE current.applied_at
            END
        """,
        (
            entity_id,
            run_id,
            COUNTRY_INFERENCE_VERSION,
            inference.status,
            inference.country_code,
            inference.confidence,
            inference.runner_up_country_code,
            inference.runner_up_confidence,
            _json([asdict(item) for item in inference.evidence]),
            _json(list(inference.reason_codes)),
            applied,
        ),
    )


def _unknown_contact_count(cur) -> int:
    cur.execute(
        """
        SELECT count(*) AS row_count
        FROM entity.entity AS e
        WHERE e.country_code IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM contact.entity_country_inference AS active_ci
              WHERE active_ci.entity_id = e.entity_id
                AND active_ci.status = 'ACCEPTED'
                AND active_ci.applied_at IS NOT NULL
          )
          AND (
              EXISTS (SELECT 1 FROM contact.raw_record rr WHERE rr.entity_id = e.entity_id)
              OR EXISTS (
                  SELECT 1 FROM contact.entity_person_relation r WHERE r.entity_id = e.entity_id
              )
              OR EXISTS (SELECT 1 FROM contact.channel c WHERE c.entity_id = e.entity_id)
          )
        """
    )
    return int(cur.fetchone()["row_count"] or 0)


def _update_run(
    run_id: str,
    *,
    status: str | None = None,
    metrics: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE contact.country_inference_run
                SET status = COALESCE(%s, status),
                    metrics = COALESCE(%s::jsonb, metrics),
                    error_message = %s,
                    finished_at = CASE
                        WHEN %s IN ('SUCCESS', 'FAILED', 'BUSY') THEN now()
                        ELSE finished_at
                    END
                WHERE run_id = %s
                """,
                (status, _json(metrics) if metrics is not None else None, error, status, run_id),
            )
        conn.commit()


def run_country_inference(
    *,
    apply: bool = False,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    min_margin: float = DEFAULT_MIN_MARGIN,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_entities: int | None = None,
    emit: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if not 0.5 <= min_confidence <= 0.999:
        raise ValueError("min_confidence must be between 0.5 and 0.999")
    if not 0.0 <= min_margin <= 0.5:
        raise ValueError("min_margin must be between 0 and 0.5")
    if batch_size < 10 or batch_size > 5000:
        raise ValueError("batch_size must be between 10 and 5000")
    if max_entities is not None and max_entities < 1:
        raise ValueError("max_entities must be positive")

    ensure_country_inference_schema()
    run_id = str(uuid.uuid4())
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO contact.country_inference_run(
                    run_id, rule_version, status, apply_mode,
                    min_confidence, min_margin, batch_size
                ) VALUES (%s, %s, 'RUNNING', %s, %s, %s, %s)
                """,
                (
                    run_id,
                    COUNTRY_INFERENCE_VERSION,
                    apply,
                    min_confidence,
                    min_margin,
                    batch_size,
                ),
            )
            cur.execute(
                "SELECT pg_try_advisory_lock(hashtext(%s)) AS acquired",
                (COUNTRY_INFERENCE_LOCK,),
            )
            acquired = bool(cur.fetchone()["acquired"])
            conn.commit()
        if not acquired:
            metrics = {"run_id": run_id, "status": "BUSY", "apply": apply}
            _update_run(run_id, status="BUSY", metrics=metrics)
            return metrics

        started = time.monotonic()
        status_counts: Counter[str] = Counter()
        country_counts: Counter[str] = Counter()
        evidence_counts: Counter[str] = Counter()
        applied_entities = 0
        evaluated = 0
        batches = 0
        after_entity_id: str | None = None
        try:
            with conn.cursor() as cur:
                unknown_before = _unknown_contact_count(cur)
            models = build_reference_models()
            while True:
                remaining_limit = None if max_entities is None else max_entities - evaluated
                if remaining_limit is not None and remaining_limit <= 0:
                    break
                take = batch_size if remaining_limit is None else min(batch_size, remaining_limit)
                batch = _candidate_batch(after_entity_id, take)
                if not batch:
                    break
                batches += 1
                entity_ids = [str(row["entity_id"]) for row in batch]
                context = _load_context(entity_ids)
                batch_results: list[tuple[str, Inference]] = []
                for entity in batch:
                    entity_id = str(entity["entity_id"])
                    inference = infer_from_evidence(
                        _entity_evidence(entity, context[entity_id], models),
                        min_confidence=min_confidence,
                        min_margin=min_margin,
                    )
                    batch_results.append((entity_id, inference))
                    evaluated += 1
                    status_counts[inference.status] += 1
                    if inference.country_code:
                        country_counts[inference.country_code] += 1
                    evidence_counts.update({item.kind for item in inference.evidence})

                with postgres_conn() as batch_conn:
                    with batch_conn.cursor() as cur:
                        applied_set: set[str] = set()
                        if apply:
                            for entity_id, inference in batch_results:
                                if inference.status != "ACCEPTED" or not inference.country_code:
                                    continue
                                # Source facts win. If a concurrent import filled a real
                                # country after planning, do not activate the inference.
                                cur.execute(
                                    """
                                    SELECT 1
                                    FROM entity.entity
                                    WHERE entity_id = %s AND country_code IS NULL
                                    """,
                                    (entity_id,),
                                )
                                if cur.fetchone():
                                    applied_set.add(entity_id)
                                    applied_entities += 1
                        for entity_id, inference in batch_results:
                            _upsert_inference(
                                cur,
                                run_id,
                                entity_id,
                                inference,
                                entity_id in applied_set,
                            )
                    batch_conn.commit()

                after_entity_id = entity_ids[-1]
                elapsed = round(time.monotonic() - started, 2)
                progress = {
                    "event": "CONTACT_COUNTRY_INFERENCE_PROGRESS",
                    "run_id": run_id,
                    "apply": apply,
                    "batch": batches,
                    "evaluated": evaluated,
                    "unknown_before": unknown_before,
                    "accepted": status_counts["ACCEPTED"],
                    "conflict": status_counts["CONFLICT"],
                    "insufficient": status_counts["INSUFFICIENT"],
                    "applied": applied_entities,
                    "elapsed_seconds": elapsed,
                    "reference_city_keys": len(models.city_country),
                    "reference_domain_keys": len(models.domain_country),
                }
                _update_run(run_id, metrics=progress)
                if emit is not None:
                    emit(progress)

            with postgres_conn() as final_conn:
                with final_conn.cursor() as cur:
                    unknown_after = _unknown_contact_count(cur)
            metrics = {
                "run_id": run_id,
                "status": "SUCCESS",
                "rule_version": COUNTRY_INFERENCE_VERSION,
                "apply": apply,
                "min_confidence": min_confidence,
                "min_margin": min_margin,
                "batch_size": batch_size,
                "max_entities": max_entities,
                "unknown_before": unknown_before,
                "unknown_after": unknown_after,
                "evaluated": evaluated,
                "accepted": status_counts["ACCEPTED"],
                "conflict": status_counts["CONFLICT"],
                "insufficient": status_counts["INSUFFICIENT"],
                "applied": applied_entities,
                "batches": batches,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "country_counts": dict(country_counts.most_common()),
                "evidence_kind_counts": dict(evidence_counts.most_common()),
                "reference_city_keys": len(models.city_country),
                "reference_domain_keys": len(models.domain_country),
                "semantics": "INFERRED_CONTACT_GEO_OVERLAY_NOT_OFFICIAL_TRADEMARK_FACT",
                "source_country_fields_mutated": False,
            }
            _update_run(run_id, status="SUCCESS", metrics=metrics)
            if apply and applied_entities:
                try:
                    from app.contact_ingest.directory_cached import invalidate_contact_view_cache

                    invalidate_contact_view_cache()
                except Exception:
                    # Cross-process cache generation also observes applied_at; cache
                    # invalidation must not turn a successful enrichment into failure.
                    pass
            return metrics
        except Exception as exc:
            failure = {
                "run_id": run_id,
                "status": "FAILED",
                "apply": apply,
                "evaluated": evaluated,
                "applied": applied_entities,
                "error": f"{type(exc).__name__}: {exc}",
            }
            _update_run(run_id, status="FAILED", metrics=failure, error=failure["error"])
            raise
        finally:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_unlock(hashtext(%s))",
                    (COUNTRY_INFERENCE_LOCK,),
                )
            conn.commit()


def latest_country_inference_run() -> dict[str, Any] | None:
    ensure_country_inference_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id::text, rule_version, status, apply_mode,
                       min_confidence, min_margin, batch_size, metrics,
                       error_message, started_at, finished_at
                FROM contact.country_inference_run
                ORDER BY started_at DESC, run_id DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
    return dict(row) if row else None


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Infer missing contact countries from traceable multi-signal evidence"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Activate only ACCEPTED high-confidence inferred countries as a view overlay",
    )
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE)
    parser.add_argument("--min-margin", type=float, default=DEFAULT_MIN_MARGIN)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--max-entities", type=int, default=None)
    args = parser.parse_args()
    try:
        result = run_country_inference(
            apply=args.apply,
            min_confidence=args.min_confidence,
            min_margin=args.min_margin,
            batch_size=args.batch_size,
            max_entities=args.max_entities,
            emit=_emit,
        )
    except Exception as exc:
        _emit(
            {
                "event": "CONTACT_COUNTRY_INFERENCE_FATAL",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return 2
    _emit({"event": "CONTACT_COUNTRY_INFERENCE_COMPLETE", **result})
    return 0 if result.get("status") in {"SUCCESS", "BUSY"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
