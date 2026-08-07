from __future__ import annotations

from dataclasses import dataclass
import re

from app.cn.text import clean_text


GOODS_STATUS_MAPPING_VERSION = "CN_GOODS_STATUS_V2_LIFECYCLE_EMPIRICAL"
GOODS_STATUS_EVIDENCE_LABEL = "EMPIRICAL_DOMAIN_MAPPING"


@dataclass(frozen=True)
class GoodsStatus:
    raw: str
    bucket: str
    reason: str
    semantic: str
    source_finality: str
    operational_effect: str
    mapping_version: str = GOODS_STATUS_MAPPING_VERSION
    evidence_label: str = GOODS_STATUS_EVIDENCE_LABEL


_INACTIVE_MARKERS = (
    "删除",
    "删减",
    "无效",
    "注销",
    "撤销",
    "失效",
    "removed",
    "deleted",
    "invalid",
    "cancelled",
    "canceled",
)
_ACTIVE_MARKERS = ("有效", "正常", "active", "valid")


def classify_goods_status(value: object) -> GoodsStatus:
    """Classify one CN goods/service item status without inventing case status.

    Codes 0/1/2 are empirically mapped at the *goods item* layer only. They do
    not identify the trademark's legal status and do not identify the legal
    cause of inactivity. Those questions belong to a separate evidence and
    inference layer.
    """
    raw = clean_text(value)
    normalized = re.sub(r"\s+", "", raw).lower()

    if normalized == "":
        return GoodsStatus(
            raw=raw,
            bucket="ACTIVE",
            reason="BLANK_NO_NEGATIVE_SIGNAL",
            semantic="NO_NEGATIVE_SIGNAL",
            source_finality="OPEN",
            operational_effect="EFFECTIVE_UNLESS_CONTRADICTED",
        )

    if normalized == "0":
        return GoodsStatus(
            raw=raw,
            bucket="ACTIVE",
            reason="EMPIRICAL_CODE_0_REVERSIBLE_OR_UNRESOLVED_RISK",
            semantic="REVERSIBLE_OR_UNRESOLVED_RISK",
            source_finality="REVERSIBLE",
            operational_effect="EFFECTIVE_AT_RISK",
        )

    if normalized == "1":
        return GoodsStatus(
            raw=raw,
            bucket="INACTIVE",
            reason="EMPIRICAL_CODE_1_INACTIVE_HIGH_CONFIDENCE",
            semantic="INACTIVE_HIGH_CONFIDENCE",
            source_finality="SOURCE_NOT_FINALIZED",
            operational_effect="INACTIVE_HIGH_CONFIDENCE",
        )

    if normalized == "2":
        return GoodsStatus(
            raw=raw,
            bucket="INACTIVE",
            reason="EMPIRICAL_CODE_2_FINAL_INACTIVE",
            semantic="FINAL_INACTIVE",
            source_finality="FINAL",
            operational_effect="INACTIVE_CONFIRMED",
        )

    if normalized.isdigit():
        return GoodsStatus(
            raw=raw,
            bucket="UNKNOWN",
            reason="UNMAPPED_NUMERIC_CODE",
            semantic="UNKNOWN",
            source_finality="UNKNOWN",
            operational_effect="UNKNOWN",
        )

    if any(marker in normalized for marker in _INACTIVE_MARKERS):
        return GoodsStatus(
            raw=raw,
            bucket="INACTIVE",
            reason="EXPLICIT_INACTIVE_TEXT",
            semantic="FINAL_INACTIVE_TEXT",
            source_finality="EXPLICIT_TEXT",
            operational_effect="INACTIVE_CONFIRMED",
            evidence_label="SOURCE_TEXT_MAPPING",
        )

    if any(marker in normalized for marker in _ACTIVE_MARKERS):
        return GoodsStatus(
            raw=raw,
            bucket="ACTIVE",
            reason="EXPLICIT_ACTIVE_TEXT",
            semantic="ACTIVE_TEXT",
            source_finality="OPEN",
            operational_effect="EFFECTIVE_UNLESS_CONTRADICTED",
            evidence_label="SOURCE_TEXT_MAPPING",
        )

    return GoodsStatus(
        raw=raw,
        bucket="UNKNOWN",
        reason="UNMAPPED_TEXT_CODE",
        semantic="UNKNOWN",
        source_finality="UNKNOWN",
        operational_effect="UNKNOWN",
    )
