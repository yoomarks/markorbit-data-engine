from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import csv
import io
import re
from typing import Iterator

from app.cn.schema import (
    FileSchema,
    canonical_header,
    canonical_record,
)
from app.cn.text import clean_text
from app.cn.zipio import PackageMember


EXPECTED_TERMS = (
    "注册号", "申请号", "国际分类", "商标", "注册人", "商品", "服务",
    "优先权", "共有人", "代理", "名称", "地址", "公告",
)
MOJIBAKE_MARKERS = (
    "ä", "å", "æ", "ç", "è", "é", "ï¼", "Â", "Ã",
    "锛", "锝", "鍙", "鍚", "鍏", "鍟", "鐢", "鐮", "浠", "悊", "浜", "�",
)
ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "gb18030", "gbk", "cp936", "big5", "latin1")

APP_RE = re.compile(r"^(?:G)?\d{5,}[A-Z]*$", re.I)
CLASS_RE = re.compile(r"^\d{1,2}$")
DATE_RE = re.compile(r"^\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2})?")


def score_decoded_text(text: str) -> int:
    head = (text or "")[:12_000]
    cjk = sum(1 for ch in head if "\u4e00" <= ch <= "\u9fff")
    expected = sum(head.count(term) for term in EXPECTED_TERMS)
    mojibake = sum(head.count(marker) for marker in MOJIBAKE_MARKERS)
    replacement = head.count("\ufffd")
    return expected * 500 + cjk * 2 - mojibake * 80 - replacement * 200


def detect_encoding(sample: bytes, forced: str = "auto") -> str:
    if forced and forced.lower() != "auto":
        return forced
    scored: list[tuple[int, str]] = []
    for encoding in ENCODING_CANDIDATES:
        try:
            text = sample.decode(encoding, errors="replace")
        except Exception:
            continue
        scored.append((score_decoded_text(text), encoding))
    return max(scored)[1] if scored else "utf-8-sig"



def _cjk_score(value: str) -> int:
    cjk = sum(1 for char in value if "\u4e00" <= char <= "\u9fff")
    mojibake = sum(value.count(marker) for marker in MOJIBAKE_MARKERS)
    bad = value.count("\ufffd")
    expected = sum(
        value.count(term)
        for term in ("公司", "商标", "注册", "申请", "商品", "代理", "地址", "有限")
    )
    return cjk * 2 + expected * 100 - mojibake * 80 - bad * 300


def repair_mojibake_cell(value: str) -> tuple[str, bool]:
    if not value or not any(marker in value for marker in MOJIBAKE_MARKERS):
        return value, False

    candidates = [value]
    for wrong_encoding in ("gb18030", "gbk", "cp936", "latin1", "cp1252"):
        try:
            candidate = value.encode(
                wrong_encoding, errors="replace"
            ).decode("utf-8", errors="replace")
        except Exception:
            continue
        if candidate.count("\ufffd") <= value.count("\ufffd"):
            candidates.append(candidate)

    best = max(candidates, key=_cjk_score)
    return (best, best != value) if _cjk_score(best) > _cjk_score(value) else (value, False)


def _record_start(schema: FileSchema, physical_line: str) -> bool:
    """Identify a physical line that begins a new logical CSV record.

    CN exports exist in both unquoted and fully quoted forms. Parsing only by raw
    comma splitting makes a quoted application number look like ``\"123...\"``
    and can concatenate millions of physical lines into one logical record. Use
    ``csv.reader`` for the prefix probe so production ingestion, audits and raw
    scans all share the same quoted/unquoted boundary semantics.
    """
    line = physical_line.lstrip("\ufeff")
    try:
        values = next(csv.reader([line], strict=False))
    except (csv.Error, StopIteration):
        values = line.split(",", 3)

    if schema.role == "agent":
        return True
    if not values or not APP_RE.fullmatch((values[0] or "").strip()):
        return False
    if schema.requires_class:
        if len(values) < 2 or not CLASS_RE.fullmatch((values[1] or "").strip()):
            return False
    if schema.requires_date:
        if len(values) < 3 or not DATE_RE.match((values[2] or "").strip()):
            return False
    return True


@dataclass
class ParsedRow:
    source_start_line: int
    source_end_line: int
    record: dict[str, str]
    repair_status: str
    replacement_chars: int
    raw_record_length: int


@dataclass
class FileProfile:
    role: str
    internal_name: str
    encoding: str
    header_raw: list[str]
    header_canonical: list[str]
    physical_rows: int = 0
    logical_rows: int = 0
    continuation_rows: int = 0
    records_with_continuation: int = 0
    replacement_chars: int = 0
    mojibake_cells_repaired: int = 0
    max_record_length: int = 0
    max_field_length: int = 0
    repairs: Counter = field(default_factory=Counter)
    failed_rows: int = 0
    failed_examples: list[dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "internal_name": self.internal_name,
            "encoding": self.encoding,
            "header_raw": self.header_raw,
            "header_canonical": self.header_canonical,
            "physical_rows": self.physical_rows,
            "logical_rows": self.logical_rows,
            "continuation_rows": self.continuation_rows,
            "records_with_continuation": self.records_with_continuation,
            "replacement_chars": self.replacement_chars,
            "mojibake_cells_repaired": self.mojibake_cells_repaired,
            "max_record_length": self.max_record_length,
            "max_field_length": self.max_field_length,
            "repairs": dict(self.repairs),
            "failed_rows": self.failed_rows,
            "failed_examples": self.failed_examples,
        }


def _parse_csv_record(raw_record: str) -> list[str] | None:
    try:
        return next(csv.reader([raw_record], strict=False))
    except (csv.Error, StopIteration):
        return None


def _merge_variable_column(
    schema: FileSchema,
    header: list[str],
    values: list[str],
) -> list[str] | None:
    expected = len(header)
    if len(values) == expected:
        return values
    if len(values) < expected or not schema.variable_column:
        return None
    try:
        variable_index = header.index(schema.variable_column)
    except ValueError:
        return None
    stable_tail = expected - variable_index - 1
    if stable_tail < 0 or len(values) < variable_index + 1 + stable_tail:
        return None
    merged = (
        values[:variable_index]
        + [",".join(values[variable_index:len(values) - stable_tail])]
        + (values[-stable_tail:] if stable_tail else [])
    )
    return merged if len(merged) == expected else None


def _repair_values(
    schema: FileSchema,
    header: list[str],
    raw_record: str,
) -> tuple[list[str] | None, str]:
    values = _parse_csv_record(raw_record)
    if values is not None:
        if len(values) == len(header):
            return values, "OK"
        merged = _merge_variable_column(schema, header, values)
        if merged is not None:
            return merged, "MERGED_EXTRA_COLUMNS"

    # A malformed quote must never be allowed to absorb later records.
    # Record boundaries were already reconstructed from physical line starts.
    fallback_values = raw_record.replace('"', "＂").split(",")
    if len(fallback_values) == len(header):
        return fallback_values, "FALLBACK_QUOTE_NEUTRALIZED"
    merged = _merge_variable_column(schema, header, fallback_values)
    if merged is not None:
        return merged, "FALLBACK_QUOTE_NEUTRALIZED_AND_MERGED"
    return None, f"UNREPAIRABLE_COLUMNS:{len(fallback_values)}/{len(header)}"


def iter_member_rows(
    member: PackageMember,
    forced_encoding: str = "auto",
    profile_only: bool = False,
) -> tuple[FileProfile, Iterator[ParsedRow]]:
    if member.schema is None:
        raise ValueError(f"Unclassified member: {member.internal_name}")

    sample = member.sample(65_536)
    encoding = detect_encoding(sample, forced_encoding)
    binary = member.open_binary()
    text = io.TextIOWrapper(binary, encoding=encoding, errors="replace", newline="")

    raw_header_line = text.readline().rstrip("\r\n")
    raw_header = next(csv.reader([raw_header_line]))
    header = canonical_header(member.schema, raw_header)

    # New basic files can omit 商标名称. Header-driven parsing keeps them valid
    # without inventing a source column.
    profile = FileProfile(
        role=member.schema.role,
        internal_name=member.internal_name,
        encoding=encoding,
        header_raw=raw_header,
        header_canonical=header,
        physical_rows=1,
    )

    def generator() -> Iterator[ParsedRow]:
        current: str | None = None
        start_line = 0
        end_line = 0
        continuation_count = 0

        def emit(raw_record: str, first_line: int, last_line: int, continuations: int):
            profile.logical_rows += 1
            profile.continuation_rows += continuations
            if continuations:
                profile.records_with_continuation += 1
            profile.max_record_length = max(profile.max_record_length, len(raw_record))
            replacement_chars = raw_record.count("\ufffd")
            profile.replacement_chars += replacement_chars

            values, repair_status = _repair_values(member.schema, header, raw_record)
            profile.repairs[repair_status] += 1
            if values is None:
                profile.failed_rows += 1
                if len(profile.failed_examples) < 20:
                    profile.failed_examples.append(
                        {
                            "start_line": first_line,
                            "end_line": last_line,
                            "repair_status": repair_status,
                            "raw_excerpt": raw_record[:1_000],
                        }
                    )
                return None

            if values:
                profile.max_field_length = max(
                    profile.max_field_length, max(len(value) for value in values)
                )
            if profile_only:
                return None

            cleaned_values: list[str] = []
            for value in values:
                cleaned = clean_text(value, preserve_newlines=True)
                repaired, changed = repair_mojibake_cell(cleaned)
                if changed:
                    profile.mojibake_cells_repaired += 1
                cleaned_values.append(repaired)
            return ParsedRow(
                source_start_line=first_line,
                source_end_line=last_line,
                record=canonical_record(member.schema, header, cleaned_values),
                repair_status=repair_status,
                replacement_chars=replacement_chars,
                raw_record_length=len(raw_record),
            )

        for physical_line_no, physical_line in enumerate(text, start=2):
            profile.physical_rows += 1
            line = physical_line.rstrip("\r\n")
            if _record_start(member.schema, line):
                if current is not None:
                    parsed = emit(current, start_line, end_line, continuation_count)
                    if parsed is not None and not profile_only:
                        yield parsed
                current = line
                start_line = physical_line_no
                end_line = physical_line_no
                continuation_count = 0
            else:
                if current is None:
                    current = line
                    start_line = physical_line_no
                    end_line = physical_line_no
                else:
                    current += "\n" + line
                    end_line = physical_line_no
                    continuation_count += 1

        if current is not None:
            parsed = emit(current, start_line, end_line, continuation_count)
            if parsed is not None and not profile_only:
                yield parsed

    return profile, generator()
