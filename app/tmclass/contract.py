from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Mapping
from urllib.parse import urlsplit


CONTRACT_VERSION = "TMCLASS_SOURCE_EVIDENCE_V1"
SOURCE_ID = "EUIPO_TMCLASS"
SOURCE_OWNER = "MARKORBIT_KNOWLEDGE"

_DECIMAL_ID = re.compile(r"^[0-9]+$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_ARTIFACT_ID = re.compile(r"^art_[0-9A-HJKMNP-TV-Z]{26}$")

_ROOT_KEYS = frozenset(
    {"contractVersion", "objectType", "sourceOwner", "sourceId", "observedAt", "evidence", "page"}
)
_EVIDENCE_KEYS = frozenset(
    {
        "workspaceId",
        "sourceDefinitionId",
        "collectionRunId",
        "rawArtifactId",
        "artifactVersion",
        "canonicalUri",
        "sourceUri",
        "sha256",
    }
)
_TERM_KEYS = frozenset(
    {
        "pageKind",
        "termId",
        "text",
        "niceClass",
        "languageCode",
        "languageLabel",
        "acceptedBy",
        "taxonomy",
        "translationTargets",
        "sources",
    }
)
_CONCEPT_IDENTITY_KEYS = frozenset(
    {
        "pageKind",
        "conceptId",
        "title",
        "status",
        "niceClass",
        "sourceName",
        "sourceDateText",
        "referenceId",
        "scopeStatus",
        "taxonomy",
    }
)
_CONCEPT_OVERVIEW_KEYS = _CONCEPT_IDENTITY_KEYS | frozenset(
    {"languages", "masterCount", "variantCount"}
)
_CONCEPT_LANGUAGE_KEYS = _CONCEPT_IDENTITY_KEYS | frozenset({"languageCode", "terms"})


class TmclassAdmissionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class NormalizedTmclassEvidence:
    observed_at: datetime
    evidence: dict[str, Any]
    page: dict[str, Any]

    @property
    def page_kind(self) -> str:
        return str(self.page["pageKind"])

    @property
    def raw_artifact_id(self) -> str:
        return str(self.evidence["rawArtifactId"])


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise TmclassAdmissionError(f"{label} must be an object")
    return value


def _exact(value: Mapping[str, Any], keys: frozenset[str], label: str) -> None:
    missing = keys - set(value)
    extra = set(value) - keys
    if missing or extra:
        parts = []
        if missing:
            parts.append("missing=" + ",".join(sorted(missing)))
        if extra:
            parts.append("unsupported=" + ",".join(sorted(extra)))
        raise TmclassAdmissionError(f"{label} fields invalid ({'; '.join(parts)})")


def _text(value: Any, label: str, maximum: int = 4096, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise TmclassAdmissionError(f"{label} must be a bounded non-empty string")
    if re.search(r"(?i)jsessionid|cookie|authorization|(?:^|[?&])token=", value):
        raise TmclassAdmissionError(f"{label} contains session or authentication material")
    return value.strip()


def _identifier(value: Any, label: str) -> str:
    text = _text(value, label, 32)
    assert text is not None
    if not _DECIMAL_ID.fullmatch(text):
        raise TmclassAdmissionError(f"{label} must be a decimal TMclass identifier")
    return text


def _language(value: Any, label: str = "languageCode") -> str:
    text = _text(value, label, 16)
    assert text is not None
    if not _LANGUAGE.fullmatch(text):
        raise TmclassAdmissionError(f"{label} is invalid")
    return text


def _nice_class(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 45:
        raise TmclassAdmissionError("niceClass must be an integer from 1 to 45")
    return value


def _nonnegative(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TmclassAdmissionError(f"{label} must be a non-negative integer")
    return value


def _bounded_list(value: Any, label: str, maximum: int, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum or (nonempty and not value):
        suffix = "1.." if nonempty else "0.."
        raise TmclassAdmissionError(f"{label} must contain {suffix}{maximum} items")
    return value


def _taxonomy(value: Any) -> list[dict[str, str | None]]:
    nodes = _bounded_list(value, "taxonomy", 32, nonempty=True)
    normalized = []
    for index, item in enumerate(nodes):
        node = _mapping(item, f"taxonomy[{index}]")
        _exact(node, frozenset({"label", "sourceNodeId"}), f"taxonomy[{index}]")
        normalized.append(
            {
                "label": _text(node.get("label"), f"taxonomy[{index}].label", 1000),
                "sourceNodeId": _text(
                    node.get("sourceNodeId"),
                    f"taxonomy[{index}].sourceNodeId",
                    128,
                    nullable=True,
                ),
            }
        )
    return normalized


def _concept_identity(page: Mapping[str, Any]) -> dict[str, Any]:
    source_date = page.get("sourceDateText")
    if source_date is not None and not isinstance(source_date, str):
        raise TmclassAdmissionError("sourceDateText must be a string or null")
    return {
        "conceptId": _identifier(page.get("conceptId"), "conceptId"),
        "title": _text(page.get("title"), "title"),
        "status": _text(page.get("status"), "status", 128),
        "niceClass": _nice_class(page.get("niceClass")),
        "sourceName": _text(page.get("sourceName"), "sourceName", 512),
        "sourceDateText": source_date.strip() if isinstance(source_date, str) else None,
        "referenceId": _text(page.get("referenceId"), "referenceId", 512),
        "scopeStatus": _text(page.get("scopeStatus"), "scopeStatus", 128),
        "taxonomy": _taxonomy(page.get("taxonomy")),
    }


def _term_page(page: Mapping[str, Any]) -> dict[str, Any]:
    _exact(page, _TERM_KEYS, "page")
    accepted = []
    for index, item in enumerate(_bounded_list(page.get("acceptedBy"), "acceptedBy", 256)):
        office = _mapping(item, f"acceptedBy[{index}]")
        _exact(office, frozenset({"name", "code"}), f"acceptedBy[{index}]")
        accepted.append(
            {
                "name": _text(office.get("name"), f"acceptedBy[{index}].name", 512),
                "code": _text(office.get("code"), f"acceptedBy[{index}].code", 64, nullable=True),
            }
        )
    translations = []
    for index, item in enumerate(
        _bounded_list(page.get("translationTargets"), "translationTargets", 256)
    ):
        target = _mapping(item, f"translationTargets[{index}]")
        _exact(
            target,
            frozenset({"termId", "languageCode", "niceClass", "text", "quality"}),
            f"translationTargets[{index}]",
        )
        translations.append(
            {
                "termId": _identifier(target.get("termId"), f"translationTargets[{index}].termId"),
                "languageCode": _language(
                    target.get("languageCode"), f"translationTargets[{index}].languageCode"
                ),
                "niceClass": _nice_class(target.get("niceClass")),
                "text": _text(target.get("text"), f"translationTargets[{index}].text"),
                "quality": _text(
                    target.get("quality"), f"translationTargets[{index}].quality", 128
                ),
            }
        )
    sources = []
    for index, item in enumerate(_bounded_list(page.get("sources"), "sources", 256)):
        source = _mapping(item, f"sources[{index}]")
        _exact(
            source,
            frozenset({"conceptId", "sourceName", "referenceId"}),
            f"sources[{index}]",
        )
        sources.append(
            {
                "conceptId": _identifier(source.get("conceptId"), f"sources[{index}].conceptId"),
                "sourceName": _text(source.get("sourceName"), f"sources[{index}].sourceName", 512),
                "referenceId": _text(
                    source.get("referenceId"), f"sources[{index}].referenceId", 512
                ),
            }
        )
    return {
        "pageKind": "TERM",
        "termId": _identifier(page.get("termId"), "termId"),
        "text": _text(page.get("text"), "text"),
        "niceClass": _nice_class(page.get("niceClass")),
        "languageCode": _language(page.get("languageCode")),
        "languageLabel": _text(page.get("languageLabel"), "languageLabel", 128),
        "acceptedBy": accepted,
        "taxonomy": _taxonomy(page.get("taxonomy")),
        "translationTargets": translations,
        "sources": sources,
    }


def _concept_overview(page: Mapping[str, Any]) -> dict[str, Any]:
    _exact(page, _CONCEPT_OVERVIEW_KEYS, "page")
    normalized = {"pageKind": "CONCEPT_OVERVIEW", **_concept_identity(page)}
    languages = []
    for index, item in enumerate(
        _bounded_list(page.get("languages"), "languages", 128, nonempty=True)
    ):
        language = _mapping(item, f"languages[{index}]")
        _exact(
            language,
            frozenset(
                {
                    "languageCode",
                    "masterTermId",
                    "masterTermText",
                    "variantCount",
                    "totalTermCount",
                }
            ),
            f"languages[{index}]",
        )
        variants = _nonnegative(language.get("variantCount"), "variantCount")
        total = _nonnegative(language.get("totalTermCount"), "totalTermCount")
        if total != variants + 1:
            raise TmclassAdmissionError("totalTermCount must equal one master plus variants")
        languages.append(
            {
                "languageCode": _language(language.get("languageCode")),
                "masterTermId": _identifier(language.get("masterTermId"), "masterTermId"),
                "masterTermText": _text(language.get("masterTermText"), "masterTermText"),
                "variantCount": variants,
                "totalTermCount": total,
            }
        )
    normalized["languages"] = languages
    normalized["masterCount"] = _nonnegative(page.get("masterCount"), "masterCount")
    normalized["variantCount"] = _nonnegative(page.get("variantCount"), "variantCount")
    return normalized


def _concept_language(page: Mapping[str, Any]) -> dict[str, Any]:
    _exact(page, _CONCEPT_LANGUAGE_KEYS, "page")
    normalized = {"pageKind": "CONCEPT_LANGUAGE", **_concept_identity(page)}
    language_code = _language(page.get("languageCode"))
    terms = []
    seen: set[str] = set()
    masters = 0
    for index, item in enumerate(_bounded_list(page.get("terms"), "terms", 512, nonempty=True)):
        term = _mapping(item, f"terms[{index}]")
        _exact(term, frozenset({"termId", "text", "role", "ordinal"}), f"terms[{index}]")
        term_id = _identifier(term.get("termId"), f"terms[{index}].termId")
        if term_id in seen:
            raise TmclassAdmissionError("concept language contains duplicate termId")
        seen.add(term_id)
        role = term.get("role")
        if role not in ("MASTER", "VARIANT"):
            raise TmclassAdmissionError("term role must be MASTER or VARIANT")
        masters += int(role == "MASTER")
        ordinal = term.get("ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
            raise TmclassAdmissionError("term ordinal must be a positive integer")
        terms.append(
            {
                "termId": term_id,
                "text": _text(term.get("text"), f"terms[{index}].text"),
                "role": role,
                "ordinal": ordinal,
            }
        )
    if masters != 1:
        raise TmclassAdmissionError("concept language must contain exactly one MASTER term")
    normalized["languageCode"] = language_code
    normalized["terms"] = terms
    return normalized


def _source_uri(value: Any, page: Mapping[str, Any]) -> str:
    uri = _text(value, "evidence.sourceUri")
    assert uri is not None
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "euipo.europa.eu"
        or parsed.query
        or parsed.fragment
    ):
        raise TmclassAdmissionError("sourceUri must be an exact public EUIPO TMclass page")
    kind = page["pageKind"]
    expected = (
        f"/ec2/term/{page['termId']}"
        if kind == "TERM"
        else f"/ec2/concept/{page['conceptId']}"
        if kind == "CONCEPT_OVERVIEW"
        else f"/ec2/concept/{page['conceptId']}/{page['languageCode']}"
    )
    if parsed.path != expected:
        raise TmclassAdmissionError("sourceUri does not match the page identity")
    return uri


def normalize(value: Mapping[str, Any]) -> NormalizedTmclassEvidence:
    root = _mapping(value, "evidence package")
    _exact(root, _ROOT_KEYS, "evidence package")
    expected = {
        "contractVersion": CONTRACT_VERSION,
        "objectType": "TMCLASS_SOURCE_EVIDENCE",
        "sourceOwner": SOURCE_OWNER,
        "sourceId": SOURCE_ID,
    }
    for key, required in expected.items():
        if root.get(key) != required:
            raise TmclassAdmissionError(f"{key} must equal {required}")
    timestamp = _text(root.get("observedAt"), "observedAt", 64)
    try:
        observed_at = datetime.fromisoformat((timestamp or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise TmclassAdmissionError("observedAt must be an ISO-8601 instant") from exc
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise TmclassAdmissionError("observedAt requires a timezone")

    raw_page = _mapping(root.get("page"), "page")
    kind = raw_page.get("pageKind")
    if kind == "TERM":
        page = _term_page(raw_page)
    elif kind == "CONCEPT_OVERVIEW":
        page = _concept_overview(raw_page)
    elif kind == "CONCEPT_LANGUAGE":
        page = _concept_language(raw_page)
    else:
        raise TmclassAdmissionError("pageKind is not supported")

    raw_evidence = _mapping(root.get("evidence"), "evidence")
    _exact(raw_evidence, _EVIDENCE_KEYS, "evidence")
    raw_artifact_id = _text(raw_evidence.get("rawArtifactId"), "rawArtifactId", 64)
    if not _ARTIFACT_ID.fullmatch(raw_artifact_id or ""):
        raise TmclassAdmissionError("rawArtifactId must be a Knowledge RawArtifact ULID")
    version = raw_evidence.get("artifactVersion")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise TmclassAdmissionError("artifactVersion must be a positive integer")
    sha = _text(raw_evidence.get("sha256"), "sha256", 64)
    if not _SHA256.fullmatch(sha or ""):
        raise TmclassAdmissionError("sha256 must be lowercase SHA-256")
    source_uri = _source_uri(raw_evidence.get("sourceUri"), page)
    evidence = {
        "workspaceId": _text(raw_evidence.get("workspaceId"), "workspaceId", 128),
        "sourceDefinitionId": _text(
            raw_evidence.get("sourceDefinitionId"), "sourceDefinitionId", 128
        ),
        "collectionRunId": _text(raw_evidence.get("collectionRunId"), "collectionRunId", 128),
        "rawArtifactId": raw_artifact_id,
        "artifactVersion": version,
        "canonicalUri": _text(raw_evidence.get("canonicalUri"), "canonicalUri"),
        "sourceUri": source_uri,
        "sha256": sha,
    }
    return NormalizedTmclassEvidence(observed_at.astimezone(timezone.utc), evidence, page)
