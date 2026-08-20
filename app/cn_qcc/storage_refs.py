from __future__ import annotations

from pathlib import Path, PurePosixPath


EXPORT_PREFIX = "cn_qcc/outgoing"
RESULT_PREFIX = "cn_qcc/incoming"


def export_object_key(batch_key: str) -> str:
    return f"{EXPORT_PREFIX}/{batch_key}.tasks.csv"


def result_object_key(batch_key: str) -> str:
    return f"{RESULT_PREFIX}/{batch_key}.result.csv"


def resolve_object_key(root: Path, object_key: str, *, prefix: str) -> Path:
    """Resolve one namespaced logical QCC object key under a runtime root.

    Persistent provenance stores the POSIX object key, never a host absolute
    path. Runtime roots may differ between Windows, Docker and future collectors.
    """
    key = PurePosixPath(object_key)
    expected = PurePosixPath(prefix)
    try:
        relative = key.relative_to(expected)
    except ValueError as exc:
        raise ValueError(f"QCC object key is outside {prefix}: {object_key!r}") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"invalid QCC object key: {object_key!r}")
    if len(relative.parts) != 1:
        raise ValueError(f"QCC object key must name one handoff file: {object_key!r}")
    return root.resolve() / relative.name


def export_path(root: Path, batch_key: str) -> Path:
    return resolve_object_key(root, export_object_key(batch_key), prefix=EXPORT_PREFIX)


def result_path(root: Path, batch_key: str) -> Path:
    return resolve_object_key(root, result_object_key(batch_key), prefix=RESULT_PREFIX)


__all__ = [
    "EXPORT_PREFIX",
    "RESULT_PREFIX",
    "export_object_key",
    "export_path",
    "resolve_object_key",
    "result_object_key",
    "result_path",
]
