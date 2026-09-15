from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping

import clickhouse_connect

from app.us.target_canary import TARGET_DATABASE, TARGET_HTTP_PORT, TARGET_NATIVE_HOST

_READ_ONLY_SQL = re.compile(r"^\s*(SELECT|SHOW|DESCRIBE|EXISTS)\b", re.IGNORECASE)
_DOCKER_TARGET_HOST = "host.docker.internal"
_DOCKER_ENV_MARKER = Path("/.dockerenv")


def _in_container() -> bool:
    return _DOCKER_ENV_MARKER.exists()


def _accepted_target_host() -> str:
    return _DOCKER_TARGET_HOST if _in_container() else TARGET_NATIVE_HOST


@dataclass(frozen=True, slots=True)
class AcceptedUSTargetQueryResult:
    column_names: list[str]
    result_rows: list[list[Any]]


def _read_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8").rstrip("\x00")
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8").rstrip("\x00")
    if isinstance(value, list):
        return [_read_value(item) for item in value]
    if isinstance(value, tuple):
        return [_read_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _read_value(item) for key, item in value.items()}
    return value


class AcceptedUSTargetReadClient:
    """Read-only transport pinned to the accepted US target ClickHouse."""

    def __init__(self, base: Any | None = None) -> None:
        self._base = base or clickhouse_connect.get_client(
            host=_accepted_target_host(),
            port=TARGET_HTTP_PORT,
            username="default",
            password="",
            database=TARGET_DATABASE,
        )

    def query(
        self, sql: str, *, settings: Mapping[str, Any] | None = None
    ) -> Any:
        if not _READ_ONLY_SQL.match(str(sql or "")):
            raise RuntimeError("accepted US target read client permits only read-only SQL")
        result = self._base.query(sql, settings=dict(settings or {}))
        return AcceptedUSTargetQueryResult(
            column_names=[str(name) for name in result.column_names],
            result_rows=[[_read_value(item) for item in row] for row in result.result_rows],
        )

    def command(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("accepted US target read client does not permit commands")

    def insert(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("accepted US target read client does not permit inserts")


def accepted_us_target_read_client() -> AcceptedUSTargetReadClient:
    return AcceptedUSTargetReadClient()
