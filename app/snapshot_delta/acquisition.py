"""Network acquisition for snapshot-first data.gov.sg sources."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS, SnapshotSource
from .ipos_sg_observation import validate_ipos_snapshot_schema
from .loader import SnapshotCsvLoader


class SnapshotDownloadError(RuntimeError):
    """Raised when an authoritative snapshot cannot be resolved or downloaded."""


@dataclass(frozen=True)
class AcquiredSnapshot:
    path: Path
    source_uri: str
    retrieved_at: datetime
    bytes_written: int


class DataGovSgSnapshotDownloader:
    """Resolve and atomically download one data.gov.sg CSV snapshot."""

    def __init__(
        self,
        source: SnapshotSource = IPOS_SG_TRADEMARK_APPLICATIONS,
        *,
        opener: Callable[..., Any] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
        timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 15.0,
        max_poll_attempts: int = 40,
        chunk_size: int = 1024 * 1024,
        api_key: str | None = None,
    ) -> None:
        if max_poll_attempts < 1:
            raise ValueError("max_poll_attempts must be positive")
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        self.source = source
        self._opener = opener
        self._sleeper = sleeper
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.max_poll_attempts = max_poll_attempts
        self.chunk_size = chunk_size
        self.api_key = api_key

    def _request_json(self, url: str) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        request = Request(url, headers=headers)
        with self._opener(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise SnapshotDownloadError(f"unexpected JSON response from {url}")
        return payload

    @staticmethod
    def _download_url(payload: dict[str, Any]) -> str | None:
        if payload.get("code") != 0:
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        url = data.get("url")
        return str(url) if url else None

    def resolve_download_url(self) -> str:
        """Initiate dataset materialization and poll for the signed download URL."""
        self._request_json(self.source.initiate_download_url)
        last_payload: dict[str, Any] | None = None

        for attempt in range(self.max_poll_attempts):
            payload = self._request_json(self.source.poll_download_url)
            last_payload = payload
            download_url = self._download_url(payload)
            if download_url:
                return download_url
            if attempt + 1 < self.max_poll_attempts:
                self._sleeper(self.poll_interval_seconds)

        detail = ""
        if last_payload:
            detail = str(last_payload.get("errMsg") or last_payload.get("message") or "")
        suffix = f": {detail}" if detail else ""
        raise SnapshotDownloadError(
            f"data.gov.sg download was not ready after {self.max_poll_attempts} polls{suffix}"
        )

    def download(self, destination_directory: str | Path) -> AcquiredSnapshot:
        """Stream the current snapshot to disk and publish it with an atomic rename."""
        destination = Path(destination_directory)
        destination.mkdir(parents=True, exist_ok=True)
        final_path = destination / self.source.filename
        partial_path = destination / f".{self.source.filename}.part"
        download_url = self.resolve_download_url()
        retrieved_at = datetime.now(timezone.utc)
        # Do not forward the data.gov.sg API key to the signed object-storage URL.
        request = Request(download_url, headers={"Accept": "text/csv,application/octet-stream"})
        bytes_written = 0

        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                with partial_path.open("wb") as target:
                    while True:
                        chunk = response.read(self.chunk_size)
                        if not chunk:
                            break
                        target.write(chunk)
                        bytes_written += len(chunk)
                    target.flush()
                    os.fsync(target.fileno())

            if bytes_written == 0:
                raise SnapshotDownloadError("data.gov.sg returned an empty snapshot")

            validate_ipos_snapshot_schema(SnapshotCsvLoader(partial_path))
            os.replace(partial_path, final_path)
        except Exception:
            partial_path.unlink(missing_ok=True)
            raise

        return AcquiredSnapshot(
            path=final_path,
            source_uri=download_url,
            retrieved_at=retrieved_at,
            bytes_written=bytes_written,
        )
