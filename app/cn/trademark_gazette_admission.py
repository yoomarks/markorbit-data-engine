from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence


ADMISSION_CONTRACT_VERSION = "CN_TRADEMARK_GAZETTE_ADMISSION_V1"
ISSUE_TABLE = "markorbit_facts.cn_trademark_gazette_issue"
ANNOUNCEMENT_TABLE = "markorbit_facts.cn_trademark_gazette_announcement"

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_FORBIDDEN_ROW_FIELDS = frozenset(
    {
        "applicant",
        "applicant_name",
        "applicantName",
        "registerCnName",
        "registerCnAddress",
        "registerEnName",
        "registerEnAddress",
        "trademark_name",
        "trademarkName",
        "tmName",
        "nice_class",
        "niceClass",
        "intlCls",
        "application_date",
        "applicationDate",
        "applyDate",
        "agent",
        "agent_name",
        "agentName",
    }
)
_ALLOWED_TOP_LEVEL = frozenset(
    {
        "contract_version",
        "source_authority",
        "completeness",
        "announcement_issue",
        "announcement_date",
        "record_count",
        "page_count",
        "page_size",
        "source_capture_schema",
        "source_dataset_sha256",
        "source_uri",
        "collected_at",
        "records",
    }
)
_ALLOWED_ROW_FIELDS = frozenset(
    {
        "source_row_id",
        "source_search_id",
        "registration_number",
        "announcement_issue",
        "announcement_type_code",
        "announcement_type_name",
        "detail_page_no",
        "announcement_page_count",
        "detail_file_id",
        "detail_asset_path",
        "announcement_detail_url",
    }
)


class GazetteAdmissionError(ValueError):
    pass


@dataclass(frozen=True)
class GazetteAdmissionReceipt:
    announcement_issue: int
    record_count: int
    source_dataset_sha256: str
    replayed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": "ADMITTED",
            "contract_version": ADMISSION_CONTRACT_VERSION,
            "announcement_issue": self.announcement_issue,
            "record_count": self.record_count,
            "source_dataset_sha256": self.source_dataset_sha256,
            "replayed": self.replayed,
        }


@dataclass(frozen=True)
class NormalizedGazetteRow:
    source_row_id: str
    source_search_id: str
    registration_number: str
    announcement_issue: int
    announcement_type_code: str
    announcement_type_name: str
    detail_page_no: int | None
    announcement_page_count: int | None
    detail_file_id: str
    detail_asset_path: str
    announcement_detail_url: str


@dataclass(frozen=True)
class NormalizedGazettePackage:
    announcement_issue: int
    announcement_date: date | None
    record_count: int
    page_count: int
    page_size: int
    source_capture_schema: str
    source_dataset_sha256: str
    source_uri: str
    collected_at: datetime
    records: tuple[NormalizedGazetteRow, ...]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _fingerprint(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: Any, label: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str):
        raise GazetteAdmissionError(f"{label} must be a string")
    text = value.strip()
    if not text or len(text) > maximum:
        raise GazetteAdmissionError(f"{label} must be non-empty and at most {maximum} characters")
    return text


def _optional_text(value: Any, label: str, *, maximum: int = 4096) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise GazetteAdmissionError(f"{label} must be a string when supplied")
    text = value.strip()
    if len(text) > maximum:
        raise GazetteAdmissionError(f"{label} must be at most {maximum} characters")
    return text


def _positive_int(value: Any, label: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GazetteAdmissionError(f"{label} must be an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise GazetteAdmissionError(f"{label} must be >= {minimum}")
    return value


def _optional_positive_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, label)


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise GazetteAdmissionError("announcement_date must be ISO date text or null")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise GazetteAdmissionError("announcement_date must be YYYY-MM-DD") from exc


def _parse_instant(value: Any) -> datetime:
    text = _required_text(value, "collected_at", maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GazetteAdmissionError("collected_at must be an ISO-8601 instant") from exc
    if parsed.tzinfo is None:
        raise GazetteAdmissionError("collected_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _check_fields(value: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise GazetteAdmissionError(f"{label} contains unsupported fields: {', '.join(unknown)}")


def normalize_cn_trademark_gazette_package(
    package: Mapping[str, Any],
) -> NormalizedGazettePackage:
    _check_fields(package, _ALLOWED_TOP_LEVEL, "package")

    if package.get("contract_version") != ADMISSION_CONTRACT_VERSION:
        raise GazetteAdmissionError(f"contract_version must be {ADMISSION_CONTRACT_VERSION}")
    if package.get("source_authority") != "CNIPA":
        raise GazetteAdmissionError("source_authority must be CNIPA")
    if package.get("completeness") != "COMPLETE":
        raise GazetteAdmissionError("only COMPLETE Gazette issue packages may be admitted")

    issue = _positive_int(package.get("announcement_issue"), "announcement_issue")
    record_count = _positive_int(package.get("record_count"), "record_count", allow_zero=True)
    page_count = _positive_int(package.get("page_count"), "page_count")
    page_size = _positive_int(package.get("page_size"), "page_size")
    if page_size != 100:
        raise GazetteAdmissionError("page_size must equal the frozen Gazette page size 100")

    dataset_sha = _required_text(package.get("source_dataset_sha256"), "source_dataset_sha256", maximum=64).lower()
    if not _HEX64.fullmatch(dataset_sha):
        raise GazetteAdmissionError("source_dataset_sha256 must be 64 hexadecimal characters")

    raw_records = package.get("records")
    if not isinstance(raw_records, Sequence) or isinstance(raw_records, (str, bytes, bytearray)):
        raise GazetteAdmissionError("records must be an array")
    if len(raw_records) != record_count:
        raise GazetteAdmissionError("record_count does not match records length")

    normalized_rows: list[NormalizedGazetteRow] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, Mapping):
            raise GazetteAdmissionError(f"records[{index}] must be an object")
        forbidden = sorted(set(raw) & _FORBIDDEN_ROW_FIELDS)
        if forbidden:
            raise GazetteAdmissionError(
                f"records[{index}] duplicates trademark-entity fields: {', '.join(forbidden)}"
            )
        _check_fields(raw, _ALLOWED_ROW_FIELDS, f"records[{index}]")

        row_id = _required_text(raw.get("source_row_id"), f"records[{index}].source_row_id", maximum=256)
        if row_id in seen_ids:
            raise GazetteAdmissionError(f"duplicate source_row_id: {row_id}")
        seen_ids.add(row_id)

        search_id = _optional_text(raw.get("source_search_id"), f"records[{index}].source_search_id", maximum=256)
        if search_id and search_id != row_id:
            raise GazetteAdmissionError(f"records[{index}].source_search_id must match source_row_id")

        row_issue = _positive_int(raw.get("announcement_issue"), f"records[{index}].announcement_issue")
        if row_issue != issue:
            raise GazetteAdmissionError(f"records[{index}] belongs to announcement issue {row_issue}, expected {issue}")

        normalized_rows.append(
            NormalizedGazetteRow(
                source_row_id=row_id,
                source_search_id=search_id,
                registration_number=_required_text(
                    raw.get("registration_number"),
                    f"records[{index}].registration_number",
                    maximum=128,
                ),
                announcement_issue=row_issue,
                announcement_type_code=_required_text(
                    raw.get("announcement_type_code"),
                    f"records[{index}].announcement_type_code",
                    maximum=128,
                ),
                announcement_type_name=_optional_text(
                    raw.get("announcement_type_name"),
                    f"records[{index}].announcement_type_name",
                    maximum=512,
                ),
                detail_page_no=_optional_positive_int(
                    raw.get("detail_page_no"),
                    f"records[{index}].detail_page_no",
                ),
                announcement_page_count=_optional_positive_int(
                    raw.get("announcement_page_count"),
                    f"records[{index}].announcement_page_count",
                ),
                detail_file_id=_optional_text(
                    raw.get("detail_file_id"),
                    f"records[{index}].detail_file_id",
                    maximum=256,
                ),
                detail_asset_path=_optional_text(
                    raw.get("detail_asset_path"),
                    f"records[{index}].detail_asset_path",
                ),
                announcement_detail_url=_optional_text(
                    raw.get("announcement_detail_url"),
                    f"records[{index}].announcement_detail_url",
                ),
            )
        )

    if record_count > page_count * page_size:
        raise GazetteAdmissionError("record_count exceeds page_count * page_size")
    if record_count and record_count <= (page_count - 1) * page_size:
        raise GazetteAdmissionError("page_count is inconsistent with record_count/page_size")

    return NormalizedGazettePackage(
        announcement_issue=issue,
        announcement_date=_parse_date(package.get("announcement_date")),
        record_count=record_count,
        page_count=page_count,
        page_size=page_size,
        source_capture_schema=_required_text(
            package.get("source_capture_schema"),
            "source_capture_schema",
            maximum=256,
        ),
        source_dataset_sha256=dataset_sha,
        source_uri=_required_text(package.get("source_uri"), "source_uri"),
        collected_at=_parse_instant(package.get("collected_at")),
        records=tuple(normalized_rows),
    )


def _row_fingerprint(row: NormalizedGazetteRow) -> str:
    return _fingerprint(
        {
            "source_row_id": row.source_row_id,
            "source_search_id": row.source_search_id,
            "registration_number": row.registration_number,
            "announcement_issue": row.announcement_issue,
            "announcement_type_code": row.announcement_type_code,
            "announcement_type_name": row.announcement_type_name,
            "detail_page_no": row.detail_page_no,
            "announcement_page_count": row.announcement_page_count,
            "detail_file_id": row.detail_file_id,
            "detail_asset_path": row.detail_asset_path,
            "announcement_detail_url": row.announcement_detail_url,
        }
    )


def admit_cn_trademark_gazette_package(
    package: Mapping[str, Any],
    *,
    client: Any,
) -> GazetteAdmissionReceipt:
    normalized = normalize_cn_trademark_gazette_package(package)

    existing = client.query(
        f"""
        SELECT record_count
        FROM {ISSUE_TABLE} FINAL
        WHERE announcement_issue = %(issue)s
          AND source_dataset_sha256 = %(dataset_sha)s
        LIMIT 1
        """,
        parameters={
            "issue": normalized.announcement_issue,
            "dataset_sha": normalized.source_dataset_sha256,
        },
    ).result_rows
    if existing:
        if int(existing[0][0]) != normalized.record_count:
            raise GazetteAdmissionError("existing issue observation conflicts with record_count")
        return GazetteAdmissionReceipt(
            normalized.announcement_issue,
            normalized.record_count,
            normalized.source_dataset_sha256,
            True,
        )

    issue_observation_fingerprint = _fingerprint(
        {
            "announcement_issue": normalized.announcement_issue,
            "announcement_date": (
                normalized.announcement_date.isoformat()
                if normalized.announcement_date is not None
                else None
            ),
            "record_count": normalized.record_count,
            "page_count": normalized.page_count,
            "page_size": normalized.page_size,
            "source_dataset_sha256": normalized.source_dataset_sha256,
        }
    )

    rows: list[list[Any]] = []
    for row in normalized.records:
        row_fingerprint = _row_fingerprint(row)
        resolution_status = (
            "URL_OBSERVED"
            if row.announcement_detail_url
            else ("SOURCE_LOCATOR_OBSERVED" if row.detail_asset_path or row.detail_file_id else "UNRESOLVED")
        )
        observation_fingerprint = _fingerprint(
            {
                "announcement_issue": normalized.announcement_issue,
                "source_row_id": row.source_row_id,
                "source_dataset_sha256": normalized.source_dataset_sha256,
                "source_row_fingerprint": row_fingerprint,
            }
        )
        rows.append(
            [
                row.source_row_id,
                row.source_search_id,
                row.announcement_issue,
                row.registration_number,
                row.announcement_type_code,
                row.announcement_type_name,
                row.detail_page_no,
                row.announcement_page_count,
                row.detail_file_id,
                row.detail_asset_path,
                row.announcement_detail_url,
                resolution_status,
                row_fingerprint,
                normalized.source_dataset_sha256,
                normalized.source_uri,
                normalized.collected_at,
                observation_fingerprint,
            ]
        )

    if rows:
        client.insert(
            ANNOUNCEMENT_TABLE,
            rows,
            column_names=[
                "source_row_id",
                "source_search_id",
                "announcement_issue",
                "registration_number",
                "announcement_type_code",
                "announcement_type_name",
                "detail_page_no",
                "announcement_page_count",
                "detail_file_id",
                "detail_asset_path",
                "announcement_detail_url",
                "detail_resolution_status",
                "source_row_fingerprint",
                "source_dataset_sha256",
                "source_uri",
                "collected_at",
                "observation_fingerprint",
            ],
        )

    # COMPLETE is the commit marker. Write it only after all announcement rows
    # have been accepted, so a mid-admission failure cannot publish a false
    # complete issue observation.
    client.insert(
        ISSUE_TABLE,
        [[
            normalized.announcement_issue,
            normalized.announcement_date,
            normalized.record_count,
            normalized.page_count,
            normalized.page_size,
            "COMPLETE",
            normalized.source_capture_schema,
            normalized.source_dataset_sha256,
            normalized.source_uri,
            normalized.collected_at,
            issue_observation_fingerprint,
        ]],
        column_names=[
            "announcement_issue",
            "announcement_date",
            "record_count",
            "page_count",
            "capture_page_size",
            "capture_completeness",
            "source_capture_schema",
            "source_dataset_sha256",
            "source_uri",
            "collected_at",
            "observation_fingerprint",
        ],
    )

    return GazetteAdmissionReceipt(
        normalized.announcement_issue,
        normalized.record_count,
        normalized.source_dataset_sha256,
        False,
    )
