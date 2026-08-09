from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.cn.package_meta import PackageDescriptor, infer_package_descriptor
from app.cn.preflight_m16_real_data import build_preflight
from app.config import get_settings


PLAN_NAME = "CN_M16_DETERMINISTIC_REPLAY_PLAN"
PLAN_VERSION = "CN_M16_REPLAY_PLAN_V1_CLEAN_RESET"


@dataclass(frozen=True)
class PlannedPackage:
    path: Path
    descriptor: PackageDescriptor
    file_size: int
    sha256: str
    registration_order: int
    hypothetical_source_rank: int

    @property
    def partition_key(self) -> str:
        return f"{self.descriptor.partition_dimension}:{self.descriptor.partition_value}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def collect_incoming_packages(raw_root: Path) -> list[PlannedPackage]:
    incoming = raw_root / "incoming" / "cn"
    paths = sorted(
        (path for path in incoming.glob("*.zip") if path.is_file()),
        key=lambda path: path.name,
    )
    packages: list[PlannedPackage] = []
    for registration_order, path in enumerate(paths, start=1):
        descriptor = infer_package_descriptor(path)
        packages.append(
            PlannedPackage(
                path=path,
                descriptor=descriptor,
                file_size=path.stat().st_size,
                sha256=_sha256(path),
                registration_order=registration_order,
                hypothetical_source_rank=descriptor.source_rank(registration_order),
            )
        )
    return packages


def evaluate_replay_plan(
    packages: list[PlannedPackage],
    *,
    preflight: dict[str, Any],
) -> dict[str, Any]:
    hard_fail_reasons: list[str] = []
    warning_reasons: list[str] = []

    if preflight.get("status") == "FAIL":
        hard_fail_reasons.append("real_data_preflight_failed")
    if not preflight.get("safe_to_run_replay_command"):
        hard_fail_reasons.append("preflight_does_not_allow_replay")
    if preflight.get("mode") != "CLEAN_RESET_READY_FOR_REPLAY":
        hard_fail_reasons.append("replay_plan_requires_clean_reset_mode")
    if not packages:
        hard_fail_reasons.append("no_incoming_cn_zip_packages")

    unknown = [package for package in packages if package.descriptor.package_kind == "UNKNOWN"]
    if unknown:
        hard_fail_reasons.append("unknown_package_precedence")

    sha_counts = Counter(package.sha256 for package in packages)
    duplicate_sha = sorted(sha for sha, count in sha_counts.items() if count > 1)
    if duplicate_sha:
        hard_fail_reasons.append("duplicate_incoming_package_content")

    partition_groups: dict[str, list[PlannedPackage]] = defaultdict(list)
    for package in packages:
        if package.descriptor.package_kind != "UNKNOWN":
            partition_groups[package.partition_key].append(package)
    ambiguous_partitions = {
        key: group for key, group in partition_groups.items() if len({item.sha256 for item in group}) > 1
    }
    if ambiguous_partitions:
        hard_fail_reasons.append("ambiguous_partition_revision")

    base_count = sum(package.descriptor.package_kind == "BASE_PARTITION" for package in packages)
    monthly_count = sum(package.descriptor.package_kind == "MONTHLY_PATCH" for package in packages)
    if base_count == 0:
        warning_reasons.append("no_base_partition_in_clean_replay_plan")
    if monthly_count == 0:
        warning_reasons.append("no_monthly_patch_in_clean_replay_plan")

    registration_order = sorted(packages, key=lambda package: package.registration_order)
    processing_order = sorted(
        packages,
        key=lambda package: (package.hypothetical_source_rank, package.registration_order),
    )

    def serialize(package: PlannedPackage) -> dict[str, Any]:
        descriptor = package.descriptor
        return {
            "file_name": package.path.name,
            "file_size": package.file_size,
            "sha256": package.sha256,
            "package_kind": descriptor.package_kind,
            "partition_dimension": descriptor.partition_dimension,
            "partition_value": descriptor.partition_value,
            "source_period_start": descriptor.source_period_start,
            "source_period_end": descriptor.source_period_end,
            "source_sequence": descriptor.source_sequence,
            "registration_order": package.registration_order,
            "hypothetical_source_rank": package.hypothetical_source_rank,
        }

    status = "FAIL" if hard_fail_reasons else ("PASS_WITH_WARNINGS" if warning_reasons else "PASS")
    return {
        "status": status,
        "plan": PLAN_NAME,
        "plan_version": PLAN_VERSION,
        "hard_fail_reasons": hard_fail_reasons,
        "warning_reasons": warning_reasons,
        "package_count": len(packages),
        "base_partition_count": base_count,
        "monthly_patch_count": monthly_count,
        "duplicate_sha256": duplicate_sha,
        "ambiguous_partitions": {
            key: [serialize(package) for package in group]
            for key, group in sorted(ambiguous_partitions.items())
        },
        "scanner_registration_order": [serialize(package) for package in registration_order],
        "expected_processing_order": [serialize(package) for package in processing_order],
        "invariants": [
            "Plan is valid only for CLEAN_RESET_READY_FOR_REPLAY.",
            "Scanner registration order is lexical incoming filename order.",
            "Expected processing order uses the same PackageDescriptor.source_rank contract as registration.",
            "Unknown package precedence is rejected rather than guessed.",
            "Multiple different ZIPs for the same semantic partition are rejected without explicit revision evidence.",
        ],
        "preflight": {
            "status": preflight.get("status"),
            "mode": preflight.get("mode"),
            "safe_to_run_replay_command": preflight.get("safe_to_run_replay_command"),
            "preflight_version": preflight.get("preflight_version"),
        },
    }


def build_replay_plan() -> dict[str, Any]:
    settings = get_settings()
    preflight = build_preflight()
    packages = collect_incoming_packages(settings.raw_data_root)
    return evaluate_replay_plan(packages, preflight=preflight)


def main() -> None:
    print(json.dumps(build_replay_plan(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
