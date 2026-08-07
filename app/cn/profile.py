from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from app.cn.package_meta import infer_package_descriptor
from app.cn.reader import iter_member_rows
from app.cn.zipio import iter_package_members


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def infer_package_kind(path: Path) -> str:
    return infer_package_descriptor(path).package_kind


def profile_package(
    path: Path,
    forced_encoding: str = "auto",
    consume_rows: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "file_name": path.name,
        "file_size": path.stat().st_size,
        "sha256": sha256_file(path),
        "package_kind": infer_package_kind(path),
        "package_descriptor": infer_package_descriptor(path).__dict__,
        "profiled_at": datetime.now(timezone.utc).isoformat(),
        "members": [],
        "unclassified_members": [],
        "totals": {
            "physical_rows": 0,
            "logical_rows": 0,
            "continuation_rows": 0,
            "replacement_chars": 0,
            "failed_rows": 0,
        },
    }

    for member in iter_package_members(path):
        if member.schema is None:
            result["unclassified_members"].append(
                {
                    "internal_name": member.internal_name,
                    "original_internal_name": member.original_internal_name,
                    "size": member.size,
                    "filename_repaired": member.filename_repaired,
                    "filename_encoding": member.filename_encoding,
                }
            )
            continue

        profile, rows = iter_member_rows(
            member,
            forced_encoding=forced_encoding,
            profile_only=not consume_rows,
        )
        # The parser updates its profile while the iterator is consumed.
        for _ in rows:
            pass

        item = profile.as_dict()
        item.update(
            {
                "original_internal_name": member.original_internal_name,
                "size": member.size,
                "compressed_size": member.compressed_size,
                "filename_repaired": member.filename_repaired,
                "filename_encoding": member.filename_encoding,
            }
        )
        result["members"].append(item)
        for key in result["totals"]:
            result["totals"][key] += int(item.get(key, 0))

    result["success"] = (
        bool(result["members"])
        and result["totals"]["failed_rows"] == 0
        and any(item["role"] == "basic" for item in result["members"])
    )
    return result


def write_profile(path: Path, output: Path) -> dict[str, Any]:
    profile = profile_package(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile
