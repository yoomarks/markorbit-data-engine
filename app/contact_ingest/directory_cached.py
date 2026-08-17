from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import threading
import time
from typing import Any, Callable

from app.contact_ingest import directory_api as analytics_source
from app.contact_ingest import directory_runtime as runtime_source


CONTACT_OVERVIEW_CACHE_TTL_SECONDS = 600.0
CONTACT_COUNTRIES_CACHE_TTL_SECONDS = 600.0
CONTACT_DIRECTORY_CACHE_TTL_SECONDS = 300.0
CONTACT_DIRECTORY_CACHE_MAX_ENTRIES = 256

_cache_lock = threading.Lock()
_cache: OrderedDict[tuple[Any, ...], tuple[float, str, dict[str, Any]]] = OrderedDict()


def _decorate(
    value: dict[str, Any],
    *,
    hit: bool,
    age_seconds: float,
    ttl_seconds: float,
    cached_at: str,
) -> dict[str, Any]:
    result = deepcopy(value)
    result["_cache"] = {
        "hit": bool(hit),
        "age_seconds": round(max(0.0, age_seconds), 3),
        "ttl_seconds": int(ttl_seconds),
        "cached_at": cached_at,
        "invalidation": "CONTACT_IMPORT_SUCCESS_OR_FORCE_REFRESH_OR_TTL",
    }
    return result


def _get_or_load(
    key: tuple[Any, ...],
    *,
    ttl_seconds: float,
    loader: Callable[[], dict[str, Any]],
    force_refresh: bool = False,
) -> dict[str, Any]:
    now = time.monotonic()
    if not force_refresh:
        with _cache_lock:
            cached = _cache.get(key)
            if cached is not None:
                stored_at, cached_at, value = cached
                age = now - stored_at
                if age < ttl_seconds:
                    _cache.move_to_end(key)
                    return _decorate(
                        value,
                        hit=True,
                        age_seconds=age,
                        ttl_seconds=ttl_seconds,
                        cached_at=cached_at,
                    )
                _cache.pop(key, None)

    value = loader()
    cached_at = datetime.now(timezone.utc).isoformat()
    stored_at = time.monotonic()
    with _cache_lock:
        _cache[key] = (stored_at, cached_at, deepcopy(value))
        _cache.move_to_end(key)
        while len(_cache) > CONTACT_DIRECTORY_CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)
    return _decorate(
        value,
        hit=False,
        age_seconds=0.0,
        ttl_seconds=ttl_seconds,
        cached_at=cached_at,
    )


def invalidate_contact_view_cache() -> None:
    """Invalidate all read-model caches after a successful contact mutation."""
    with _cache_lock:
        _cache.clear()
    # directory_api still has a short compatibility cache around the expensive
    # analytical rollup. Keep both layers coherent until that legacy cache is
    # eventually removed.
    analytics_source.invalidate_contact_directory_cache()


def cached_contact_directory_analytics(*, force_refresh: bool = False) -> dict[str, Any]:
    if force_refresh:
        analytics_source.invalidate_contact_directory_cache()
    return _get_or_load(
        ("analytics",),
        ttl_seconds=CONTACT_OVERVIEW_CACHE_TTL_SECONDS,
        loader=analytics_source.contact_directory_analytics,
        force_refresh=force_refresh,
    )


def cached_contact_directory_countries(*, force_refresh: bool = False) -> dict[str, Any]:
    return _get_or_load(
        ("countries",),
        ttl_seconds=CONTACT_COUNTRIES_CACHE_TTL_SECONDS,
        loader=runtime_source.contact_directory_countries,
        force_refresh=force_refresh,
    )


def cached_contact_directory_list(
    *,
    country: str = "",
    segment: str = "",
    channel: str = "",
    query: str = "",
    limit: int = 100,
    offset: int = 0,
    force_refresh: bool = False,
) -> dict[str, Any]:
    normalized = (
        country.strip().upper(),
        segment.strip().upper(),
        channel.strip().upper(),
        query.strip(),
        max(1, min(int(limit), 500)),
        max(0, int(offset)),
    )
    return _get_or_load(
        ("directory", *normalized),
        ttl_seconds=CONTACT_DIRECTORY_CACHE_TTL_SECONDS,
        loader=lambda: runtime_source.contact_directory_list(
            country=normalized[0],
            segment=normalized[1],
            channel=normalized[2],
            query=normalized[3],
            limit=normalized[4],
            offset=normalized[5],
        ),
        force_refresh=force_refresh,
    )
