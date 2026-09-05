from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.admin_domain_tasks import engine_mutation_guard
from app.us.target_bulk_plan import build_bulk_plan, validate_bulk_plan
from app.us.target_bulk_replay import audit_bulk_plan, execute_bulk_plan
from app.us.target_canary import write_receipt


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be an object")
    return payload


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def _plan(args: argparse.Namespace) -> int:
    stage2 = _read_json(args.stage2_receipt, "accepted Package 2 receipt")
    plan = build_bulk_plan(
        args.raw_root,
        execution_main=args.execution_main,
        stage2_receipt=stage2,
        start_sequence=args.start_sequence,
        end_sequence=args.end_sequence,
        max_packages=args.max_packages,
    )
    write_receipt(args.output, plan)
    _emit(
        {
            "decision": "US_APPLICATION_TARGET_BULK_PLAN_FROZEN",
            "plan_path": str(args.output),
            "plan_sha256": plan["plan_sha256"],
            "inventory_sha256": plan["inventory_sha256"],
            "execution_main": plan["execution_main"],
            "bridge_sequence": plan["bridge_sequence"],
            "accepted_existing_target_sequence": plan["accepted_existing_target_sequence"],
            "start_sequence": plan["start_sequence"],
            "end_sequence": plan["end_sequence"],
            "suffix_package_count": plan["suffix_package_count"],
            "required_authority_token": plan["required_authority_token"],
            "production_mutation_authorized": False,
        }
    )
    return 0


def _execute(args: argparse.Namespace) -> int:
    plan = _read_json(args.plan, "US target bulk plan")
    validate_bulk_plan(plan)
    stage2 = _read_json(args.stage2_receipt, "accepted Package 2 receipt")
    with engine_mutation_guard() as acquired:
        if not acquired:
            raise RuntimeError(
                "global engine mutation lock is busy; US target bulk execute did not start"
            )
        receipt = execute_bulk_plan(
            plan=plan,
            stage2_receipt=stage2,
            journal_path=args.journal,
            state_dir=args.state_dir,
            authority_token=args.authority_token,
        )
    write_receipt(args.receipt, receipt)
    _emit(receipt)
    return 0


def _audit(args: argparse.Namespace) -> int:
    plan = _read_json(args.plan, "US target bulk plan")
    validate_bulk_plan(plan)
    stage2 = _read_json(args.stage2_receipt, "accepted Package 2 receipt")
    audit = audit_bulk_plan(
        plan=plan,
        stage2_receipt=stage2,
        journal_path=args.journal,
    )
    write_receipt(args.receipt, audit)
    _emit(audit)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bounded US Application replay into the accepted hot_us target"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="freeze a read-only bounded replay plan")
    plan.add_argument("--raw-root", type=Path, required=True)
    plan.add_argument("--execution-main", required=True)
    plan.add_argument("--stage2-receipt", type=Path, required=True)
    plan.add_argument("--start-sequence", type=int, default=3)
    bound = plan.add_mutually_exclusive_group(required=True)
    bound.add_argument("--end-sequence", type=int)
    bound.add_argument("--max-packages", type=int)
    plan.add_argument("--output", type=Path, required=True)
    plan.set_defaults(handler=_plan)

    execute = sub.add_parser("execute", help="execute exactly one frozen bounded plan")
    execute.add_argument("--plan", type=Path, required=True)
    execute.add_argument("--stage2-receipt", type=Path, required=True)
    execute.add_argument("--journal", type=Path, required=True)
    execute.add_argument("--state-dir", type=Path, required=True)
    execute.add_argument("--authority-token", required=True)
    execute.add_argument("--receipt", type=Path, required=True)
    execute.set_defaults(handler=_execute)

    audit = sub.add_parser("audit", help="read-only final audit of one completed plan")
    audit.add_argument("--plan", type=Path, required=True)
    audit.add_argument("--stage2-receipt", type=Path, required=True)
    audit.add_argument("--journal", type=Path, required=True)
    audit.add_argument("--receipt", type=Path, required=True)
    audit.set_defaults(handler=_audit)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
