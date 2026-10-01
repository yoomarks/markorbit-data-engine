"""Governed UKIPO historical structured-stage relocation from D: to E:.

The accepted D: stage is a legacy, superseded placement. This operator verifies
the accepted independent audit and the exact official F: ZIPs, then performs an
additive, resumable, byte-identical copy of the four accepted/quarantine JSONL
files to E:. It never deletes source evidence, writes a database, changes a
VHDX, or authorizes serving cutover.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SOURCE_ROOT = Path(r"D:\yoomarks\governed-plans\855\historical-stock")
TARGET_ROOT = Path(r"E:\MarkOrbitData\structured-stage\gb\ukipo\historical-stock-v2")
RAW_ROOT = Path(r"F:\MarkOrbitData\raw\incoming\uk")
GOV_ROOT = Path(r"D:\yoomarks\governed-plans\855")
ACCEPTED_AUDIT = GOV_ROOT / "gb-historical-stock-independent-audit-r1.json"
ACCEPTED_AUDIT_SHA = "320c40b1cea7d5593ee8dbb99dfcbc1f534b71bd682281ab42fd719873a9d0c5"
TARGET_MANIFEST = TARGET_ROOT / "gb-historical-e-stage-manifest-v1.json"
RECEIPT = GOV_ROOT / "gb-historical-e-stage-relocation-r1.json"
REQUIRED_FREE_BUFFER = 64 * 1024**3


@dataclass(frozen=True)
class FrozenFile:
    relative: str
    size: int
    sha256: str


STAGE_FILES = (
    FrozenFile(
        "domestic-3b6063bed36a-rows.jsonl",
        1_962_471_899,
        "e7fa216355bc8cc5b9d70b05adb9d562ed5fbd58583a4a220e4a14febb070a64",
    ),
    FrozenFile(
        "domestic-3b6063bed36a-quarantine.jsonl",
        81_684,
        "7b487b99737873c4be9f4ec759a03d9d6158a96d437f94265b327e7c54e56727",
    ),
    FrozenFile(
        "madrid_ir-2a93bb45f0c4-rows.jsonl",
        170_004_513,
        "7221e087f71cbf15a122b003dcb8e07d0878d5c866522db356de603d3ea99d80",
    ),
    FrozenFile(
        "madrid_ir-2a93bb45f0c4-quarantine.jsonl",
        6_004,
        "253376dadb34af1cd27b9d39d35dbf1226e0148e9a3abd70bf40dd43e9927526",
    ),
)
RAW_FILES = (
    FrozenFile(
        "opendatadomestic.zip",
        63_238_846,
        "3b6063bed36a78e8a04f10a5383f2881f4072a1be13e706a81ab097fb56ee571",
    ),
    FrozenFile(
        "opendataIR.zip",
        6_155_443,
        "2a93bb45f0c40c69813628b6f2ab3a6a443f6d0504d8edf009c51027f28f8c85",
    ),
)


class GBHistoricalEStageError(RuntimeError):
    pass


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise GBHistoricalEStageError(reason)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_text_sha(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def reserve_floor(total_bytes: int) -> int:
    return (total_bytes * 30 + 99) // 100 + REQUIRED_FREE_BUFFER


def check_no_reparse_ancestors(path: Path) -> None:
    for ancestor in (path, *path.parents):
        if not ancestor.exists():
            continue
        require(
            not ancestor.is_symlink() and not (os.name == "nt" and os.path.isjunction(ancestor)),
            f"GB structured stage may not traverse symlink/junction: {ancestor}",
        )


def verify_files(root: Path, expected: Iterable[FrozenFile]) -> None:
    require(root.is_dir(), f"required evidence root missing: {root}")
    check_no_reparse_ancestors(root)
    for item in expected:
        path = root / item.relative
        require(
            path.is_file()
            and not path.is_symlink()
            and path.stat().st_size == item.size
            and sha256_file(path) == item.sha256,
            f"frozen evidence file identity drift: {path}",
        )


def verify_target_partial_state() -> None:
    if not TARGET_ROOT.exists():
        return
    require(TARGET_ROOT.is_dir(), "E structured-stage target is not a directory")
    check_no_reparse_ancestors(TARGET_ROOT)
    expected = {item.relative: item for item in STAGE_FILES}
    allowed = set(expected) | {relative + ".partial" for relative in expected}
    observed = {
        path.relative_to(TARGET_ROOT).as_posix()
        for path in TARGET_ROOT.rglob("*")
        if path.is_file()
    }
    require(observed.issubset(allowed), "E structured-stage target contains unexpected files")
    for relative in observed:
        base = relative.removesuffix(".partial")
        item = expected[base]
        path = TARGET_ROOT / relative
        require(
            path.stat().st_size == item.size and sha256_file(path) == item.sha256,
            f"existing E structured-stage state mismatch: {relative}",
        )
        require(
            not (relative.endswith(".partial") and base in observed),
            f"E structured-stage has both final and partial file: {base}",
        )


def verify_accepted_source() -> None:
    require(
        ACCEPTED_AUDIT.is_file() and sha256_file(ACCEPTED_AUDIT) == ACCEPTED_AUDIT_SHA,
        "accepted UKIPO historical-stage audit identity drift",
    )
    audit = json.loads(ACCEPTED_AUDIT.read_text(encoding="utf-8"))
    require(
        audit.get("status") == "TWO_HISTORICAL_ZIPS_STAGED_NOT_DATABASE_INGESTED"
        and audit.get("accepted_source_rows") == 1_298_573
        and audit.get("quarantined_source_rows") == 114
        and audit.get("original_F_ZIPs_retained") is True
        and audit.get("data_engine_ingested") is False,
        "accepted UKIPO historical-stage audit contract drift",
    )
    verify_files(SOURCE_ROOT, STAGE_FILES)
    verify_files(RAW_ROOT, RAW_FILES)


def frozen_files_payload(files: Iterable[FrozenFile]) -> list[dict[str, int | str]]:
    return [
        {"relative_path": item.relative, "bytes": item.size, "sha256": item.sha256}
        for item in files
    ]


def make_plan() -> dict:
    verify_accepted_source()
    require(not TARGET_ROOT.exists(), "E structured-stage target already exists; review state")
    require(not RECEIPT.exists(), "E structured-stage receipt already exists")
    check_no_reparse_ancestors(TARGET_ROOT)
    disk = shutil.disk_usage(TARGET_ROOT.anchor)
    floor = reserve_floor(disk.total)
    stage_bytes = sum(item.size for item in STAGE_FILES)
    require(
        disk.free - stage_bytes >= floor,
        "E capacity gate failed for GB structured-stage additive copy",
    )
    return {
        "kind": "GB_UKIPO_HISTORICAL_E_STRUCTURED_STAGE_PLAN_V1",
        "status": "FROZEN_ADDITIVE_COPY_NO_DELETE_NO_DATABASE_APPLY",
        "source_root": str(SOURCE_ROOT),
        "source_role": "LEGACY_SUPERSEDED_D_STAGE_COPY_SOURCE_ONLY",
        "target_root": str(TARGET_ROOT),
        "target_drive": "E",
        "target_role": "GB_STRUCTURED_STAGE_FOR_FUTURE_HOT_GLOBAL_ADMISSION",
        "official_raw_root": str(RAW_ROOT),
        "official_raw_authority_drive": "F",
        "accepted_stage_audit_sha256": ACCEPTED_AUDIT_SHA,
        "stage_files": frozen_files_payload(STAGE_FILES),
        "stage_bytes": stage_bytes,
        "official_raw_files": frozen_files_payload(RAW_FILES),
        "operator_sha256": canonical_text_sha(Path(__file__)),
        "target_total_bytes": disk.total,
        "target_reserve_floor_bytes": floor,
        "copy_semantics": "ADDITIVE_BYTE_IDENTICAL_RESUMABLE_COPY",
        "source_retained_after_copy": True,
        "source_delete_authorized": False,
        "target_overwrite_authorized": False,
        "postgres_apply_authorized": False,
        "clickhouse_apply_authorized": False,
        "vhdx_operation_authorized": False,
        "serving_cutover_authorized": False,
        "historical_source_only": True,
        "current_state_verified": False,
    }


def validate_plan(plan: dict, plan_sha: str, token: str) -> None:
    require(
        plan.get("kind") == "GB_UKIPO_HISTORICAL_E_STRUCTURED_STAGE_PLAN_V1"
        and plan.get("status") == "FROZEN_ADDITIVE_COPY_NO_DELETE_NO_DATABASE_APPLY"
        and plan.get("source_root") == str(SOURCE_ROOT)
        and plan.get("source_role") == "LEGACY_SUPERSEDED_D_STAGE_COPY_SOURCE_ONLY"
        and plan.get("target_root") == str(TARGET_ROOT)
        and plan.get("target_drive") == "E"
        and plan.get("target_role") == "GB_STRUCTURED_STAGE_FOR_FUTURE_HOT_GLOBAL_ADMISSION"
        and plan.get("official_raw_root") == str(RAW_ROOT)
        and plan.get("official_raw_authority_drive") == "F"
        and plan.get("accepted_stage_audit_sha256") == ACCEPTED_AUDIT_SHA
        and plan.get("stage_files") == frozen_files_payload(STAGE_FILES)
        and plan.get("stage_bytes") == sum(item.size for item in STAGE_FILES)
        and plan.get("official_raw_files") == frozen_files_payload(RAW_FILES)
        and plan.get("operator_sha256") == canonical_text_sha(Path(__file__))
        and plan.get("target_reserve_floor_bytes") == reserve_floor(plan["target_total_bytes"])
        and plan.get("copy_semantics") == "ADDITIVE_BYTE_IDENTICAL_RESUMABLE_COPY"
        and plan.get("source_retained_after_copy") is True
        and plan.get("source_delete_authorized") is False
        and plan.get("target_overwrite_authorized") is False
        and plan.get("postgres_apply_authorized") is False
        and plan.get("clickhouse_apply_authorized") is False
        and plan.get("vhdx_operation_authorized") is False
        and plan.get("serving_cutover_authorized") is False
        and plan.get("historical_source_only") is True
        and plan.get("current_state_verified") is False,
        "GB E structured-stage frozen plan/operator contract drift",
    )
    expected = f"GO #855 GB-HISTORICAL-E-STAGE {plan_sha} ADDITIVE-COPY-VERIFY-NO-DELETE"
    require(token == expected, "exact GB E structured-stage authority required")


def copy_file(item: FrozenFile) -> bool:
    source = SOURCE_ROOT / item.relative
    target = TARGET_ROOT / item.relative
    partial = target.with_name(target.name + ".partial")
    if target.exists():
        require(
            target.is_file()
            and target.stat().st_size == item.size
            and sha256_file(target) == item.sha256,
            f"existing E structured-stage file mismatch: {item.relative}",
        )
        require(not partial.exists(), f"unexpected partial beside accepted target: {partial}")
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    if partial.exists():
        require(
            partial.is_file()
            and partial.stat().st_size == item.size
            and sha256_file(partial) == item.sha256,
            f"incomplete or mismatching E partial requires review: {partial}",
        )
    else:
        with source.open("rb") as src, partial.open("xb") as out:
            shutil.copyfileobj(src, out, length=1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        require(
            partial.stat().st_size == item.size and sha256_file(partial) == item.sha256,
            f"E structured-stage partial verification failed: {item.relative}",
        )
    require(not target.exists(), f"E target appeared during copy: {item.relative}")
    partial.rename(target)
    return True


def write_json_exclusive(path: Path, payload: dict) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def apply_stage(plan: dict, plan_sha: str) -> dict:
    require(not RECEIPT.exists(), "E structured-stage receipt already exists; refuse replay")
    require(
        not TARGET_MANIFEST.exists(), "E structured-stage manifest already exists; review state"
    )
    verify_accepted_source()
    verify_target_partial_state()
    disk = shutil.disk_usage(TARGET_ROOT.anchor)
    require(disk.total == plan["target_total_bytes"], "E volume total changed since freeze")

    copied = 0
    reused = 0
    for item in STAGE_FILES:
        if copy_file(item):
            copied += 1
        else:
            reused += 1
    verify_files(TARGET_ROOT, STAGE_FILES)
    verify_files(SOURCE_ROOT, STAGE_FILES)
    verify_files(RAW_ROOT, RAW_FILES)
    disk_after = shutil.disk_usage(TARGET_ROOT.anchor)
    require(
        disk_after.free >= plan["target_reserve_floor_bytes"],
        "E free space fell below frozen reserve floor",
    )
    manifest = {
        "kind": "GB_UKIPO_HISTORICAL_E_STRUCTURED_STAGE_MANIFEST_V1",
        "status": "E_STRUCTURED_STAGE_BYTE_IDENTICAL_HISTORICAL_ONLY",
        "plan_sha256": plan_sha,
        "target_root": str(TARGET_ROOT),
        "stage_files": frozen_files_payload(STAGE_FILES),
        "accepted_stage_audit_sha256": ACCEPTED_AUDIT_SHA,
        "official_raw_root": str(RAW_ROOT),
        "official_raw_files": frozen_files_payload(RAW_FILES),
        "historical_source_only": True,
        "current_state_verified": False,
        "database_ingested": False,
        "serving_cutover_authorized": False,
    }
    write_json_exclusive(TARGET_MANIFEST, manifest)
    receipt = {
        "kind": "GB_UKIPO_HISTORICAL_E_STRUCTURED_STAGE_RECEIPT_V1",
        "status": "E_STRUCTURED_STAGE_ACCEPTED_D_AND_F_RETAINED",
        "plan_sha256": plan_sha,
        "target_manifest_sha256": sha256_file(TARGET_MANIFEST),
        "target_root": str(TARGET_ROOT),
        "files_verified": len(STAGE_FILES),
        "bytes_verified": sum(item.size for item in STAGE_FILES),
        "files_copied_this_run": copied,
        "files_reused_this_run": reused,
        "source_D_retained": True,
        "official_raw_F_retained": True,
        "source_delete_authorized": False,
        "postgres_apply_authorized": False,
        "clickhouse_apply_authorized": False,
        "vhdx_operation_authorized": False,
        "serving_cutover_authorized": False,
        "historical_source_only": True,
        "current_state_verified": False,
        "target_free_bytes_after": disk_after.free,
        "target_reserve_floor_bytes": plan["target_reserve_floor_bytes"],
    }
    write_json_exclusive(RECEIPT, receipt)
    receipt["receipt_path"] = str(RECEIPT)
    receipt["receipt_sha256"] = sha256_file(RECEIPT)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--freeze-plan", type=Path)
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha", default="")
    parser.add_argument("--authority-token", default="")
    args = parser.parse_args()

    if args.preflight_only:
        require(not args.plan and not args.authority_token, "preflight accepts no Apply arguments")
        print(json.dumps(make_plan(), ensure_ascii=False, sort_keys=True), flush=True)
        return
    if args.freeze_plan is not None:
        require(
            not args.plan
            and not args.authority_token
            and args.freeze_plan.parent.resolve() == GOV_ROOT.resolve(),
            "freeze plan must be under GB governed plans",
        )
        plan = make_plan()
        write_json_exclusive(args.freeze_plan, plan)
        print("GB_E_STAGE_PLAN_SHA256=" + sha256_file(args.freeze_plan), flush=True)
        print(
            "GB_E_STAGE_PLAN_STATUS=FROZEN_ADDITIVE_COPY_NO_DELETE_NO_DATABASE_APPLY",
            flush=True,
        )
        return

    require(
        args.plan is not None
        and re.fullmatch(r"[0-9a-f]{64}", args.plan_sha)
        and sha256_file(args.plan) == args.plan_sha,
        "exact frozen GB E structured-stage plan SHA required",
    )
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    validate_plan(plan, args.plan_sha, args.authority_token)
    result = apply_stage(plan, args.plan_sha)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
