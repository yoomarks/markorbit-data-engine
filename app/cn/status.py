from __future__ import annotations

from dataclasses import dataclass
import re

from app.cn.text import clean_text


GOODS_STATUS_MAPPING_VERSION = "CN_GOODS_STATUS_V1_NUMERIC_UNMAPPED"


@dataclass(frozen=True)
class GoodsStatus:
    raw: str
    bucket: str
    reason: str
    mapping_version: str = GOODS_STATUS_MAPPING_VERSION


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
    raw = clean_text(value)
    normalized = re.sub(r"\s+", "", raw).lower()

    if normalized == "":
        return GoodsStatus(raw=raw, bucket="ACTIVE", reason="BLANK_DEFAULT_ACTIVE")
    if normalized.isdigit():
        # The source packages use 0/1/2, but the supplied materials do not
        # establish their legal meaning. Preserve them without guessing.
        return GoodsStatus(raw=raw, bucket="UNKNOWN", reason="UNMAPPED_NUMERIC_CODE")
    if any(marker in normalized for marker in _INACTIVE_MARKERS):
        return GoodsStatus(raw=raw, bucket="INACTIVE", reason="EXPLICIT_INACTIVE_TEXT")
    if any(marker in normalized for marker in _ACTIVE_MARKERS):
        return GoodsStatus(raw=raw, bucket="ACTIVE", reason="EXPLICIT_ACTIVE_TEXT")
    return GoodsStatus(raw=raw, bucket="UNKNOWN", reason="UNMAPPED_TEXT_CODE")
