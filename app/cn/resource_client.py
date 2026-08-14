from __future__ import annotations

from typing import Any


CN_MAX_THREADS = 1
CN_MAX_MEMORY_USAGE = 8_589_934_592
CN_EXTERNAL_GROUP_BY_BYTES = 67_108_864
CN_EXTERNAL_SORT_BYTES = 67_108_864

_CN_QUERY_SETTINGS = {
    "max_threads": CN_MAX_THREADS,
    "max_memory_usage": CN_MAX_MEMORY_USAGE,
    "max_bytes_before_external_group_by": CN_EXTERNAL_GROUP_BY_BYTES,
    "max_bytes_before_external_sort": CN_EXTERNAL_SORT_BYTES,
}


class CNResourceClient:
    """Apply the CN batch aggregation profile to every ClickHouse operation."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    @staticmethod
    def _merge_settings(kwargs: dict[str, Any]) -> dict[str, Any]:
        merged = dict(kwargs.get("settings") or {})
        merged.update(_CN_QUERY_SETTINGS)
        kwargs["settings"] = merged
        return kwargs

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        return self._delegate.command(sql, *args, **self._merge_settings(kwargs))

    def query(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        return self._delegate.query(sql, *args, **self._merge_settings(kwargs))

    def insert(self, table: str, data: Any, *args: Any, **kwargs: Any) -> Any:
        return self._delegate.insert(table, data, *args, **self._merge_settings(kwargs))


def cn_resource_client(factory) -> CNResourceClient:
    return CNResourceClient(factory())
