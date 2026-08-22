from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


ACQUISITION_FRAMEWORK_VERSION = "TRADEMARK_SOURCE_ACQUISITION_V1"
_SESSION_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PAGE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class AcquisitionStatus(StrEnum):
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"


@dataclass(frozen=True, slots=True)
class AcquisitionPageRequest:
    jurisdiction: str
    source_id: str
    sequence: int
    cursor: str | None


@dataclass(frozen=True, slots=True)
class AcquisitionPage:
    page_key: str
    payload: bytes
    next_cursor: str | None
    media_type: str = "application/octet-stream"

    def validate(self) -> None:
        if not _PAGE_KEY_RE.fullmatch(self.page_key):
            raise ValueError(
                "page_key must be a stable logical key using letters/digits and ._:- only"
            )
        if not isinstance(self.payload, bytes):
            raise TypeError("acquisition page payload must be bytes")
        if not self.media_type.strip():
            raise ValueError("acquisition page media_type is required")


class SourceAcquisitionAdapter(Protocol):
    adapter_id: str

    def initial_cursor(self) -> str | None: ...

    def fetch_page(self, request: AcquisitionPageRequest) -> AcquisitionPage: ...


@dataclass(frozen=True, slots=True)
class MaterializedPage:
    sequence: int
    page_key: str
    object_key: str
    sha256: str
    size_bytes: int
    media_type: str
    request_cursor: str | None
    next_cursor: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "page_key": self.page_key,
            "object_key": self.object_key,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
            "request_cursor": self.request_cursor,
            "next_cursor": self.next_cursor,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "MaterializedPage":
        return cls(
            sequence=int(payload["sequence"]),
            page_key=str(payload["page_key"]),
            object_key=str(payload["object_key"]),
            sha256=str(payload["sha256"]),
            size_bytes=int(payload["size_bytes"]),
            media_type=str(payload["media_type"]),
            request_cursor=(
                str(payload["request_cursor"])
                if payload.get("request_cursor") is not None
                else None
            ),
            next_cursor=(
                str(payload["next_cursor"])
                if payload.get("next_cursor") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class AcquisitionLedger:
    framework_version: str
    adapter_id: str
    jurisdiction: str
    source_id: str
    session_key: str
    status: AcquisitionStatus
    initial_cursor: str | None
    next_cursor: str | None
    pages: tuple[MaterializedPage, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "framework_version": self.framework_version,
            "adapter_id": self.adapter_id,
            "jurisdiction": self.jurisdiction,
            "source_id": self.source_id,
            "session_key": self.session_key,
            "status": self.status.value,
            "initial_cursor": self.initial_cursor,
            "next_cursor": self.next_cursor,
            "pages": [page.as_dict() for page in self.pages],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "AcquisitionLedger":
        pages = payload.get("pages")
        if not isinstance(pages, list):
            raise ValueError("acquisition ledger pages must be a list")
        return cls(
            framework_version=str(payload["framework_version"]),
            adapter_id=str(payload["adapter_id"]),
            jurisdiction=str(payload["jurisdiction"]),
            source_id=str(payload["source_id"]),
            session_key=str(payload["session_key"]),
            status=AcquisitionStatus(str(payload["status"])),
            initial_cursor=(
                str(payload["initial_cursor"])
                if payload.get("initial_cursor") is not None
                else None
            ),
            next_cursor=(
                str(payload["next_cursor"])
                if payload.get("next_cursor") is not None
                else None
            ),
            pages=tuple(MaterializedPage.from_dict(page) for page in pages),
        )


@dataclass(frozen=True, slots=True)
class AcquisitionResult:
    status: AcquisitionStatus
    invocation_pages: int
    cumulative_pages: int
    ledger_path: Path
    pages: tuple[MaterializedPage, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "framework_version": ACQUISITION_FRAMEWORK_VERSION,
            "status": self.status.value,
            "invocation_pages": self.invocation_pages,
            "cumulative_pages": self.cumulative_pages,
            "ledger_path": str(self.ledger_path),
            "pages": [page.as_dict() for page in self.pages],
        }


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    _atomic_write_bytes(path, serialized)


def _load_ledger(path: Path) -> AcquisitionLedger | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("acquisition ledger root must be an object")
    return AcquisitionLedger.from_dict(payload)


def _validate_existing_pages(root: Path, ledger: AcquisitionLedger) -> None:
    expected_sequence = 1
    seen_page_keys: set[str] = set()
    seen_object_keys: set[str] = set()
    for page in ledger.pages:
        if page.sequence != expected_sequence:
            raise RuntimeError("acquisition ledger page sequence is not contiguous")
        expected_sequence += 1
        if page.page_key in seen_page_keys:
            raise RuntimeError(f"duplicate acquisition page_key in ledger: {page.page_key}")
        if page.object_key in seen_object_keys:
            raise RuntimeError(f"duplicate acquisition object_key in ledger: {page.object_key}")
        seen_page_keys.add(page.page_key)
        seen_object_keys.add(page.object_key)
        object_path = (root / page.object_key).resolve()
        if root not in object_path.parents:
            raise RuntimeError("acquisition object key escaped session root")
        if not object_path.is_file():
            raise RuntimeError(f"materialized acquisition object is missing: {page.object_key}")
        payload = object_path.read_bytes()
        if len(payload) != page.size_bytes or _sha256(payload) != page.sha256:
            raise RuntimeError(f"materialized acquisition object changed: {page.object_key}")


def _validate_ledger_identity(
    ledger: AcquisitionLedger,
    *,
    adapter_id: str,
    jurisdiction: str,
    source_id: str,
    session_key: str,
    initial_cursor: str | None,
) -> None:
    expected = (
        ACQUISITION_FRAMEWORK_VERSION,
        adapter_id,
        jurisdiction,
        source_id,
        session_key,
        initial_cursor,
    )
    actual = (
        ledger.framework_version,
        ledger.adapter_id,
        ledger.jurisdiction,
        ledger.source_id,
        ledger.session_key,
        ledger.initial_cursor,
    )
    if actual != expected:
        raise RuntimeError(
            "acquisition ledger identity does not match requested source/session; "
            "refusing to reuse it"
        )


def materialize_acquisition(
    *,
    adapter: SourceAcquisitionAdapter,
    jurisdiction: str,
    source_id: str,
    session_key: str,
    output_root: Path,
    max_pages: int | None = None,
) -> AcquisitionResult:
    """Materialize source/API pages as immutable raw objects with resumable lineage.

    The adapter owns only source-native request/cursor interpretation. This shared executor owns
    page ordering, atomic raw-object writes, SHA256 evidence, resume, loop detection and a durable
    local ledger. Authentication/secrets belong to the adapter/transport and are never accepted by
    this function, so they cannot accidentally be serialized into the ledger.

    A bounded invocation leaves the session PARTIAL and can be resumed later. Re-running a COMPLETE
    session is read-only after verifying every materialized object's bytes against the ledger.
    """

    jurisdiction = jurisdiction.strip().upper()
    source_id = source_id.strip()
    adapter_id = adapter.adapter_id.strip()
    session_key = session_key.strip()
    if not jurisdiction or not source_id or not adapter_id:
        raise ValueError("jurisdiction, source_id and adapter.adapter_id are required")
    if not _SESSION_KEY_RE.fullmatch(session_key):
        raise ValueError("session_key must use letters/digits and ._- only, max 128 chars")
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be positive when provided")

    output_root = output_root.resolve()
    session_root = (output_root / jurisdiction / source_id / session_key).resolve()
    if output_root not in session_root.parents:
        raise RuntimeError("acquisition session path escaped output root")
    ledger_path = session_root / "acquisition-ledger.json"
    objects_root = session_root / "objects"
    initial_cursor = adapter.initial_cursor()

    ledger = _load_ledger(ledger_path)
    if ledger is None:
        ledger = AcquisitionLedger(
            framework_version=ACQUISITION_FRAMEWORK_VERSION,
            adapter_id=adapter_id,
            jurisdiction=jurisdiction,
            source_id=source_id,
            session_key=session_key,
            status=AcquisitionStatus.PARTIAL,
            initial_cursor=initial_cursor,
            next_cursor=initial_cursor,
            pages=(),
        )
    else:
        _validate_ledger_identity(
            ledger,
            adapter_id=adapter_id,
            jurisdiction=jurisdiction,
            source_id=source_id,
            session_key=session_key,
            initial_cursor=initial_cursor,
        )
        _validate_existing_pages(session_root, ledger)

    if ledger.status == AcquisitionStatus.COMPLETE:
        return AcquisitionResult(
            status=ledger.status,
            invocation_pages=0,
            cumulative_pages=len(ledger.pages),
            ledger_path=ledger_path,
            pages=ledger.pages,
        )

    request_cursor = ledger.next_cursor
    seen_request_cursors = {page.request_cursor for page in ledger.pages}
    seen_page_keys = {page.page_key for page in ledger.pages}
    invocation_pages = 0
    pages = list(ledger.pages)

    while max_pages is None or invocation_pages < max_pages:
        sequence = len(pages) + 1
        if request_cursor in seen_request_cursors and pages:
            raise RuntimeError(
                f"acquisition cursor loop detected before request sequence {sequence}: "
                f"{request_cursor!r}"
            )
        request = AcquisitionPageRequest(
            jurisdiction=jurisdiction,
            source_id=source_id,
            sequence=sequence,
            cursor=request_cursor,
        )
        page = adapter.fetch_page(request)
        page.validate()
        if page.page_key in seen_page_keys:
            raise RuntimeError(f"acquisition page_key repeated: {page.page_key}")
        if page.next_cursor == request_cursor and page.next_cursor is not None:
            raise RuntimeError(f"acquisition cursor did not advance: {page.next_cursor!r}")
        if page.next_cursor in seen_request_cursors and page.next_cursor is not None:
            raise RuntimeError(f"acquisition cursor loop detected: {page.next_cursor!r}")

        digest = _sha256(page.payload)
        object_key = f"objects/{sequence:08d}-{digest[:16]}.raw"
        object_path = session_root / object_key
        if object_path.exists():
            existing = object_path.read_bytes()
            if _sha256(existing) != digest:
                raise RuntimeError(f"conflicting acquisition object already exists: {object_key}")
        else:
            _atomic_write_bytes(object_path, page.payload)

        materialized = MaterializedPage(
            sequence=sequence,
            page_key=page.page_key,
            object_key=object_key,
            sha256=digest,
            size_bytes=len(page.payload),
            media_type=page.media_type,
            request_cursor=request_cursor,
            next_cursor=page.next_cursor,
        )
        pages.append(materialized)
        seen_page_keys.add(page.page_key)
        seen_request_cursors.add(request_cursor)
        request_cursor = page.next_cursor
        invocation_pages += 1

        status = (
            AcquisitionStatus.COMPLETE
            if page.next_cursor is None
            else AcquisitionStatus.PARTIAL
        )
        ledger = AcquisitionLedger(
            framework_version=ACQUISITION_FRAMEWORK_VERSION,
            adapter_id=adapter_id,
            jurisdiction=jurisdiction,
            source_id=source_id,
            session_key=session_key,
            status=status,
            initial_cursor=initial_cursor,
            next_cursor=page.next_cursor,
            pages=tuple(pages),
        )
        _atomic_write_json(ledger_path, ledger.as_dict())
        if status == AcquisitionStatus.COMPLETE:
            break

    return AcquisitionResult(
        status=ledger.status,
        invocation_pages=invocation_pages,
        cumulative_pages=len(ledger.pages),
        ledger_path=ledger_path,
        pages=ledger.pages,
    )
