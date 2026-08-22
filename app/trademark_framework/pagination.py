from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


PAGINATION_HELPER_VERSION = "TRADEMARK_API_PAGINATION_V1"


def append_query(url: str, params: dict[str, str]) -> str:
    """Merge deterministic query parameters into a source URL.

    This helper does not classify or persist query values. Source adapters remain responsible for
    keeping credentials out of URLs whenever the authority offers header-based authentication.
    """

    parts = urlsplit(url)
    existing = dict(parse_qsl(parts.query, keep_blank_values=True))
    for key, value in params.items():
        normalized_key = str(key).strip()
        if not normalized_key:
            raise ValueError("query parameter names must not be blank")
        existing[normalized_key] = str(value)
    query = urlencode(sorted(existing.items()))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


@dataclass(frozen=True, slots=True)
class PageNumberPagination:
    page_param: str = "page"
    start_page: int = 1
    page_size_param: str | None = None
    page_size: int | None = None

    def __post_init__(self) -> None:
        if not self.page_param.strip():
            raise ValueError("page_param is required")
        if self.start_page < 1:
            raise ValueError("start_page must be at least 1")
        if (self.page_size_param is None) != (self.page_size is None):
            raise ValueError("page_size_param and page_size must be configured together")
        if self.page_size_param is not None and not self.page_size_param.strip():
            raise ValueError("page_size_param must not be blank")
        if self.page_size is not None and self.page_size < 1:
            raise ValueError("page_size must be positive")

    def initial_cursor(self) -> str:
        return str(self.start_page)

    def query_for(self, cursor: str | None) -> dict[str, str]:
        page = self.start_page if cursor is None else int(cursor)
        if page < 1:
            raise ValueError("page cursor must be at least 1")
        query = {self.page_param: str(page)}
        if self.page_size_param is not None and self.page_size is not None:
            query[self.page_size_param] = str(self.page_size)
        return query

    def advance(self, cursor: str | None, *, has_more: bool) -> str | None:
        if not has_more:
            return None
        page = self.start_page if cursor is None else int(cursor)
        if page < 1:
            raise ValueError("page cursor must be at least 1")
        return str(page + 1)


@dataclass(frozen=True, slots=True)
class OffsetLimitPagination:
    offset_param: str = "offset"
    limit_param: str = "limit"
    start_offset: int = 0
    limit: int = 100

    def __post_init__(self) -> None:
        if not self.offset_param.strip() or not self.limit_param.strip():
            raise ValueError("offset_param and limit_param are required")
        if self.start_offset < 0:
            raise ValueError("start_offset must be non-negative")
        if self.limit < 1:
            raise ValueError("limit must be positive")

    def initial_cursor(self) -> str:
        return str(self.start_offset)

    def query_for(self, cursor: str | None) -> dict[str, str]:
        offset = self.start_offset if cursor is None else int(cursor)
        if offset < 0:
            raise ValueError("offset cursor must be non-negative")
        return {
            self.offset_param: str(offset),
            self.limit_param: str(self.limit),
        }

    def advance(self, cursor: str | None, *, has_more: bool) -> str | None:
        if not has_more:
            return None
        offset = self.start_offset if cursor is None else int(cursor)
        if offset < 0:
            raise ValueError("offset cursor must be non-negative")
        return str(offset + self.limit)


@dataclass(frozen=True, slots=True)
class OpaqueCursorPagination:
    cursor_param: str = "cursor"
    first_cursor: str | None = None

    def __post_init__(self) -> None:
        if not self.cursor_param.strip():
            raise ValueError("cursor_param is required")

    def initial_cursor(self) -> str | None:
        return self.first_cursor

    def query_for(self, cursor: str | None) -> dict[str, str]:
        if cursor is None or not str(cursor).strip():
            return {}
        return {self.cursor_param: str(cursor)}

    @staticmethod
    def normalize_next_cursor(value: object | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None
