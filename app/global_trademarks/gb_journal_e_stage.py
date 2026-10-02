"""Governed UKIPO journal structured-stage relocation from D: to E:.

The accepted Knowledge handoff predates the post-#837 topology and stores its
structured JSONL/manifests on D:. This operator verifies the accepted 78-issue
audit, all issue handoffs, all official F: ZIPs, and the accepted F: original-
visual relocation receipt before performing an additive byte-identical copy to
E:. It never deletes evidence, writes a database, changes a VHDX, or authorizes
serving cutover/current-register truth.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path

from app.global_trademarks.gb_historical_e_stage import (
    FrozenFile,
    canonical_text_sha,
    check_no_reparse_ancestors,
    frozen_files_payload,
    reserve_floor,
    sha256_file,
    verify_files,
    write_json_exclusive,
)

SOURCE_ROOT = Path(r"D:\yoomarks\governed-plans\910\ukipo-journal")
TARGET_ROOT = Path(r"E:\MarkOrbitData\structured-stage\gb\ukipo\journal-v1")
RAW_ROOT = Path(r"F:\MarkOrbitData\raw\incoming\uk")
VISUAL_ROOT = Path(r"F:\MarkOrbitData\visual-raw\assets\raw\gb\mark-images")
GOV_910 = Path(r"D:\yoomarks\governed-plans\910")
GOV_855 = Path(r"D:\yoomarks\governed-plans\855")
ACCEPTED_AUDIT = GOV_910 / "ukipo-78-stage-independent-audit-r1.json"
ACCEPTED_AUDIT_SHA = "c112d15007b7afe9d0c8f5a12ceb7726d088538fb5ac089cb37c935ab4079e00"
MISSING_AUDIT = GOV_910 / "ukipo-journal-missing-image-audit-r1.json"
MISSING_AUDIT_SHA = "06e667cb72b6466dff8f4c7db8cffbbba35458f223f949e285966e3c5ca6e1e5"
VISUAL_RECEIPT = GOV_910 / "ukipo-gb-original-visual-f-relocation-r1.json"
VISUAL_RECEIPT_SHA = "197abd78bc72120d1704dac108a4e9fa59fc91de432cac99cd4e065943005cc7"
TARGET_MANIFEST = TARGET_ROOT / "gb-journal-e-stage-manifest-v1.json"
RECEIPT = GOV_855 / "gb-journal-e-stage-relocation-r1.json"
EXPECTED_ISSUES = tuple(
    [f"2025-{number:03d}" for number in range(14, 53)]
    + [f"2026-{number:03d}" for number in range(1, 40)]
)
EXPECTED_DETAILS = 295_930
EXPECTED_IMAGE_LINKS = 132_482
EXPECTED_PARTIAL_ISSUES = 6
EXPECTED_MISSING_IMAGES = 7
EXPECTED_VISUAL_OBJECTS = 131_210
EXPECTED_VISUAL_BYTES = 836_495_412


class GBJournalEStageError(RuntimeError):
    pass


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise GBJournalEStageError(reason)


def verified_json(path: Path, expected_sha: str, label: str) -> dict:
    require(
        path.is_file() and not path.is_symlink() and sha256_file(path) == expected_sha,
        f"{label} SHA identity drift",
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def _frozen(path: Path, verified_sha: str) -> FrozenFile:
    return FrozenFile(path.name, path.stat().st_size, verified_sha)


def accepted_inventory() -> tuple[tuple[FrozenFile, ...], tuple[FrozenFile, ...], list[dict]]:
    check_no_reparse_ancestors(SOURCE_ROOT)
    check_no_reparse_ancestors(RAW_ROOT)
    check_no_reparse_ancestors(VISUAL_ROOT)
    audit = verified_json(ACCEPTED_AUDIT, ACCEPTED_AUDIT_SHA, "accepted journal audit")
    missing = verified_json(MISSING_AUDIT, MISSING_AUDIT_SHA, "missing-image audit")
    visual = verified_json(
        VISUAL_RECEIPT, VISUAL_RECEIPT_SHA, "F original-visual relocation receipt"
    )
    require(
        audit.get("kind") == "UKIPO_78_STAGE_INDEPENDENT_AUDIT_V1"
        and audit.get("status") == "ALL_78_STAGED_SIX_IMAGE_PARTIAL_NO_DATA_ENGINE_IMPORT"
        and audit.get("issue_count") == len(EXPECTED_ISSUES)
        and audit.get("original_zip_count") == len(EXPECTED_ISSUES)
        and audit.get("details") == EXPECTED_DETAILS
        and audit.get("image_links") == EXPECTED_IMAGE_LINKS
        and audit.get("missing_image_issue_count") == EXPECTED_PARTIAL_ISSUES
        and audit.get("missing_original_image_links") == EXPECTED_MISSING_IMAGES
        and audit.get("missing_media_audit_sha256") == MISSING_AUDIT_SHA
        and audit.get("verified_unique_cas_objects") == EXPECTED_VISUAL_OBJECTS
        and audit.get("verified_unique_cas_bytes") == EXPECTED_VISUAL_BYTES
        and audit.get("canonical_raw_artifact_published") is False
        and audit.get("data_engine_ingested") is False,
        "accepted journal audit contract drift",
    )
    require(
        missing.get("kind") == "UKIPO_MISSING_IMAGE_AUDIT_V1"
        and isinstance(missing.get("issues"), list)
        and len(missing["issues"]) == EXPECTED_PARTIAL_ISSUES
        and sum(row.get("missing_original_links", 0) for row in missing["issues"])
        == EXPECTED_MISSING_IMAGES,
        "missing-image audit contract drift",
    )
    require(
        visual.get("kind") == "UKIPO_GB_ORIGINAL_VISUAL_F_RELOCATION_RECEIPT_V1"
        and visual.get("status") == "F_ORIGINAL_VISUAL_RELOCATION_ACCEPTED_E_RETAINED"
        and visual.get("accepted_78_issue_audit_sha256") == ACCEPTED_AUDIT_SHA
        and visual.get("target_root") == str(VISUAL_ROOT)
        and visual.get("target_file_count_verified") == EXPECTED_VISUAL_OBJECTS
        and visual.get("target_bytes_verified") == EXPECTED_VISUAL_BYTES
        and visual.get("source_retained") is True
        and visual.get("source_deleted") is False
        and visual.get("source_delete_authorized") is False
        and visual.get("data_engine_ingest_authorized") is False
        and visual.get("serving_cutover_authorized") is False,
        "F original-visual relocation receipt contract drift",
    )

    issue_rows = audit.get("issues")
    require(isinstance(issue_rows, list), "accepted journal issue inventory missing")
    by_issue = {row.get("issue"): row for row in issue_rows if isinstance(row, dict)}
    require(
        tuple(sorted(by_issue)) == tuple(sorted(EXPECTED_ISSUES))
        and len(by_issue) == len(issue_rows),
        "accepted journal issue set drift",
    )
    missing_by_issue = {row["issue"]: row for row in missing["issues"]}
    stage_files: list[FrozenFile] = []
    raw_files: list[FrozenFile] = []
    normalized_issues: list[dict] = []
    for issue in EXPECTED_ISSUES:
        evidence = by_issue[issue]
        source_sha = evidence.get("source_sha256")
        manifest_sha = evidence.get("manifest_sha256")
        require(
            isinstance(source_sha, str)
            and re.fullmatch(r"[0-9a-f]{64}", source_sha) is not None
            and isinstance(manifest_sha, str)
            and re.fullmatch(r"[0-9a-f]{64}", manifest_sha) is not None,
            f"journal issue hash identity invalid: {issue}",
        )
        prefix = f"{issue}-{source_sha[:12]}"
        manifest_path = SOURCE_ROOT / f"{prefix}-manifest.json"
        records_path = SOURCE_ROOT / f"{prefix}-details.jsonl"
        manifest = verified_json(manifest_path, manifest_sha, f"journal manifest {issue}")
        missing_count = int(evidence.get("missing_image_links", -1))
        require(
            manifest.get("kind") == "UKIPO_JOURNAL_PILOT_MANIFEST_V1"
            and manifest.get("issue") == issue
            and manifest.get("zip_filename") == issue + ".zip"
            and manifest.get("zip_sha256") == source_sha
            and manifest.get("detail_count") == evidence.get("detail_rows")
            and manifest.get("mark_image_links") == evidence.get("image_links")
            and Path(str(manifest.get("records_path"))).name == records_path.name
            and manifest.get("assets_staged") is True
            and manifest.get("canonical_raw_artifact_published") is False
            and manifest.get("data_engine_ingested") is False,
            f"journal manifest contract drift: {issue}",
        )
        if missing_count:
            missing_row = missing_by_issue.get(issue)
            require(
                manifest.get("image_evidence_complete") is False
                and manifest.get("approved_missing_image_audit_sha256") == MISSING_AUDIT_SHA
                and manifest.get("missing_original_image_links") == missing_count
                and missing_row is not None
                and missing_row.get("zip_sha256") == source_sha
                and missing_row.get("missing_original_links") == missing_count,
                f"partial image evidence drift: {issue}",
            )
        else:
            require(issue not in missing_by_issue, f"unexpected missing-image issue: {issue}")
        require(
            records_path.is_file()
            and records_path.stat().st_size > 0
            and sha256_file(records_path) == manifest.get("records_sha256"),
            f"journal records identity drift: {issue}",
        )
        raw_path = RAW_ROOT / (issue + ".zip")
        require(
            raw_path.is_file()
            and raw_path.stat().st_size == manifest.get("zip_bytes")
            and sha256_file(raw_path) == source_sha,
            f"official F journal ZIP identity drift: {issue}",
        )
        stage_files.extend(
            (
                _frozen(manifest_path, manifest_sha),
                _frozen(records_path, manifest["records_sha256"]),
            )
        )
        raw_files.append(_frozen(raw_path, source_sha))
        normalized_issues.append(
            {
                "issue": issue,
                "source_sha256": source_sha,
                "manifest_sha256": manifest_sha,
                "records_sha256": manifest["records_sha256"],
                "detail_rows": evidence["detail_rows"],
                "image_links": evidence["image_links"],
                "missing_image_links": missing_count,
            }
        )
    expected_stage_names = {item.relative for item in stage_files}
    observed_stage_names = {
        path.name
        for path in SOURCE_ROOT.iterdir()
        if path.is_file() and path.name.endswith(("-manifest.json", "-details.jsonl"))
    }
    require(observed_stage_names == expected_stage_names, "journal D stage file set drift")
    return tuple(stage_files), tuple(raw_files), normalized_issues


def verify_target_partial_state(stage_files: tuple[FrozenFile, ...]) -> None:
    if not TARGET_ROOT.exists():
        return
    require(TARGET_ROOT.is_dir(), "E journal stage target is not a directory")
    check_no_reparse_ancestors(TARGET_ROOT)
    expected = {item.relative: item for item in stage_files}
    allowed = set(expected) | {name + ".partial" for name in expected}
    observed = {
        path.relative_to(TARGET_ROOT).as_posix()
        for path in TARGET_ROOT.rglob("*")
        if path.is_file()
    }
    require(observed.issubset(allowed), "E journal stage contains unexpected files")
    for relative in observed:
        base = relative.removesuffix(".partial")
        item = expected[base]
        path = TARGET_ROOT / relative
        require(
            path.stat().st_size == item.size and sha256_file(path) == item.sha256,
            f"existing E journal stage mismatch: {relative}",
        )
        require(
            not (relative.endswith(".partial") and base in observed),
            f"E journal stage has both final and partial file: {base}",
        )


def make_plan() -> dict:
    stage_files, raw_files, issues = accepted_inventory()
    require(not TARGET_ROOT.exists(), "E journal stage target already exists; review state")
    require(not RECEIPT.exists(), "E journal stage receipt already exists")
    check_no_reparse_ancestors(TARGET_ROOT)
    disk = shutil.disk_usage(TARGET_ROOT.anchor)
    floor = reserve_floor(disk.total)
    stage_bytes = sum(item.size for item in stage_files)
    require(disk.free - stage_bytes >= floor, "E capacity gate failed for GB journal stage")
    return {
        "kind": "GB_UKIPO_JOURNAL_E_STRUCTURED_STAGE_PLAN_V1",
        "status": "FROZEN_ADDITIVE_COPY_NO_DELETE_NO_DATABASE_APPLY",
        "source_root": str(SOURCE_ROOT),
        "source_role": "LEGACY_D_JOURNAL_STAGE_COPY_SOURCE_ONLY",
        "target_root": str(TARGET_ROOT),
        "target_drive": "E",
        "target_role": "GB_JOURNAL_STRUCTURED_STAGE_FOR_FUTURE_HOT_GLOBAL_ADMISSION",
        "official_raw_root": str(RAW_ROOT),
        "official_raw_authority_drive": "F",
        "original_visual_root": str(VISUAL_ROOT),
        "original_visual_authority_drive": "F",
        "accepted_78_issue_audit_sha256": ACCEPTED_AUDIT_SHA,
        "missing_image_audit_sha256": MISSING_AUDIT_SHA,
        "f_original_visual_receipt_sha256": VISUAL_RECEIPT_SHA,
        "issues": issues,
        "stage_files": frozen_files_payload(stage_files),
        "stage_bytes": stage_bytes,
        "official_raw_files": frozen_files_payload(raw_files),
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
        "journal_observation_only": True,
        "current_registry_state_verified": False,
    }


def validate_plan(plan: dict, plan_sha: str, token: str) -> None:
    stage_files, raw_files, issues = accepted_inventory()
    require(
        plan.get("kind") == "GB_UKIPO_JOURNAL_E_STRUCTURED_STAGE_PLAN_V1"
        and plan.get("status") == "FROZEN_ADDITIVE_COPY_NO_DELETE_NO_DATABASE_APPLY"
        and plan.get("source_root") == str(SOURCE_ROOT)
        and plan.get("source_role") == "LEGACY_D_JOURNAL_STAGE_COPY_SOURCE_ONLY"
        and plan.get("target_root") == str(TARGET_ROOT)
        and plan.get("target_drive") == "E"
        and plan.get("target_role") == "GB_JOURNAL_STRUCTURED_STAGE_FOR_FUTURE_HOT_GLOBAL_ADMISSION"
        and plan.get("official_raw_root") == str(RAW_ROOT)
        and plan.get("official_raw_authority_drive") == "F"
        and plan.get("original_visual_root") == str(VISUAL_ROOT)
        and plan.get("original_visual_authority_drive") == "F"
        and plan.get("accepted_78_issue_audit_sha256") == ACCEPTED_AUDIT_SHA
        and plan.get("missing_image_audit_sha256") == MISSING_AUDIT_SHA
        and plan.get("f_original_visual_receipt_sha256") == VISUAL_RECEIPT_SHA
        and plan.get("issues") == issues
        and plan.get("stage_files") == frozen_files_payload(stage_files)
        and plan.get("stage_bytes") == sum(item.size for item in stage_files)
        and plan.get("official_raw_files") == frozen_files_payload(raw_files)
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
        and plan.get("journal_observation_only") is True
        and plan.get("current_registry_state_verified") is False,
        "GB journal E-stage frozen plan/operator contract drift",
    )
    expected = f"GO #855 GB-JOURNAL-E-STAGE {plan_sha} ADDITIVE-COPY-VERIFY-NO-DELETE"
    require(token == expected, "exact GB journal E-stage authority required")


def copy_file(item: FrozenFile) -> bool:
    source = SOURCE_ROOT / item.relative
    target = TARGET_ROOT / item.relative
    partial = target.with_name(target.name + ".partial")
    if target.exists():
        require(
            target.is_file()
            and target.stat().st_size == item.size
            and sha256_file(target) == item.sha256,
            f"existing E journal stage file mismatch: {item.relative}",
        )
        require(not partial.exists(), f"unexpected partial beside accepted target: {partial}")
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    if partial.exists():
        require(
            partial.is_file()
            and partial.stat().st_size == item.size
            and sha256_file(partial) == item.sha256,
            f"incomplete or mismatching E journal partial requires review: {partial}",
        )
    else:
        with source.open("rb") as src, partial.open("xb") as out:
            shutil.copyfileobj(src, out, length=1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        require(
            partial.stat().st_size == item.size and sha256_file(partial) == item.sha256,
            f"E journal partial verification failed: {item.relative}",
        )
    require(not target.exists(), f"E journal target appeared during copy: {item.relative}")
    partial.rename(target)
    return True


def apply_stage(plan: dict, plan_sha: str) -> dict:
    require(not RECEIPT.exists(), "E journal stage receipt already exists; refuse replay")
    require(not TARGET_MANIFEST.exists(), "E journal stage manifest already exists; review state")
    stage_files, raw_files, issues = accepted_inventory()
    verify_target_partial_state(stage_files)
    disk = shutil.disk_usage(TARGET_ROOT.anchor)
    require(disk.total == plan["target_total_bytes"], "E volume total changed since freeze")
    copied = 0
    reused = 0
    for item in stage_files:
        if copy_file(item):
            copied += 1
        else:
            reused += 1
    verify_files(TARGET_ROOT, stage_files)
    verify_files(SOURCE_ROOT, stage_files)
    verify_files(RAW_ROOT, raw_files)
    disk_after = shutil.disk_usage(TARGET_ROOT.anchor)
    require(
        disk_after.free >= plan["target_reserve_floor_bytes"],
        "E free space fell below frozen journal reserve floor",
    )
    manifest = {
        "kind": "GB_UKIPO_JOURNAL_E_STRUCTURED_STAGE_MANIFEST_V1",
        "status": "E_JOURNAL_STAGE_BYTE_IDENTICAL_OBSERVATION_ONLY",
        "plan_sha256": plan_sha,
        "target_root": str(TARGET_ROOT),
        "issues": issues,
        "stage_files": frozen_files_payload(stage_files),
        "accepted_78_issue_audit_sha256": ACCEPTED_AUDIT_SHA,
        "missing_image_audit_sha256": MISSING_AUDIT_SHA,
        "official_raw_root": str(RAW_ROOT),
        "official_raw_files": frozen_files_payload(raw_files),
        "original_visual_root": str(VISUAL_ROOT),
        "f_original_visual_receipt_sha256": VISUAL_RECEIPT_SHA,
        "journal_observation_only": True,
        "current_registry_state_verified": False,
        "database_ingested": False,
        "serving_cutover_authorized": False,
    }
    write_json_exclusive(TARGET_MANIFEST, manifest)
    receipt = {
        "kind": "GB_UKIPO_JOURNAL_E_STRUCTURED_STAGE_RECEIPT_V1",
        "status": "E_JOURNAL_STAGE_ACCEPTED_D_AND_F_RETAINED",
        "plan_sha256": plan_sha,
        "target_manifest_sha256": sha256_file(TARGET_MANIFEST),
        "target_root": str(TARGET_ROOT),
        "files_verified": len(stage_files),
        "bytes_verified": sum(item.size for item in stage_files),
        "files_copied_this_run": copied,
        "files_reused_this_run": reused,
        "source_D_retained": True,
        "official_raw_F_retained": True,
        "original_visual_F_retained": True,
        "source_delete_authorized": False,
        "postgres_apply_authorized": False,
        "clickhouse_apply_authorized": False,
        "vhdx_operation_authorized": False,
        "serving_cutover_authorized": False,
        "journal_observation_only": True,
        "current_registry_state_verified": False,
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
            and args.freeze_plan.parent.resolve() == GOV_855.resolve(),
            "freeze plan must be under GB governed plans",
        )
        plan = make_plan()
        write_json_exclusive(args.freeze_plan, plan)
        print("GB_JOURNAL_E_STAGE_PLAN_SHA256=" + sha256_file(args.freeze_plan), flush=True)
        print(
            "GB_JOURNAL_E_STAGE_PLAN_STATUS=FROZEN_ADDITIVE_COPY_NO_DELETE_NO_DATABASE_APPLY",
            flush=True,
        )
        return
    require(
        args.plan is not None
        and re.fullmatch(r"[0-9a-f]{64}", args.plan_sha)
        and sha256_file(args.plan) == args.plan_sha,
        "exact frozen GB journal E-stage plan SHA required",
    )
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    validate_plan(plan, args.plan_sha, args.authority_token)
    result = apply_stage(plan, args.plan_sha)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
