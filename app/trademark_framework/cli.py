from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.trademark_framework.contracts import (
    DataFormat,
    SourceAdapterKind,
    TransportKind,
    UpdateSemantics,
)
from app.trademark_framework.registry import country_pack, country_packs, framework_audit
from app.trademark_framework.scaffold import ScaffoldRequest, build_scaffold


def _enum_choice(enum_type, value: str):
    try:
        return enum_type(value.strip().upper())
    except ValueError as exc:
        choices = ", ".join(member.value for member in enum_type)
        raise argparse.ArgumentTypeError(f"expected one of: {choices}") from exc


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.trademark_framework.cli",
        description="Reusable trademark-jurisdiction source/store framework utilities.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("audit", help="Validate the registered country packs without DB writes.")

    show = subparsers.add_parser("show", help="Show one country pack or all registered packs.")
    show.add_argument("--jurisdiction")

    scaffold = subparsers.add_parser(
        "scaffold",
        help="Plan a new country adapter skeleton. No files are written unless --write is supplied.",
    )
    scaffold.add_argument("--jurisdiction", required=True)
    scaffold.add_argument("--source-id", required=True)
    scaffold.add_argument("--store-schema")
    scaffold.add_argument(
        "--adapter-kind",
        required=True,
        type=lambda value: _enum_choice(SourceAdapterKind, value),
    )
    scaffold.add_argument(
        "--data-format",
        required=True,
        type=lambda value: _enum_choice(DataFormat, value),
    )
    scaffold.add_argument(
        "--update-semantics",
        required=True,
        type=lambda value: _enum_choice(UpdateSemantics, value),
    )
    scaffold.add_argument(
        "--transport",
        required=True,
        type=lambda value: _enum_choice(TransportKind, value),
    )
    scaffold.add_argument(
        "--write",
        action="store_true",
        help="Write the scaffold under --output-root; existing files are never overwritten.",
    )
    scaffold.add_argument("--output-root", type=Path, default=Path("."))
    scaffold.add_argument(
        "--include-content",
        action="store_true",
        help="Include generated file content in the no-write plan output.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "audit":
        audit = framework_audit()
        _print(audit.as_dict())
        return 0 if audit.ready else 2

    if args.command == "show":
        if args.jurisdiction:
            _print(country_pack(args.jurisdiction).as_dict())
        else:
            _print([pack.as_dict() for pack in country_packs()])
        return 0

    if args.command == "scaffold":
        request = ScaffoldRequest(
            jurisdiction=args.jurisdiction,
            source_id=args.source_id,
            store_schema=args.store_schema,
            adapter_kind=args.adapter_kind,
            data_format=args.data_format,
            update_semantics=args.update_semantics,
            transport=args.transport,
        )
        plan = build_scaffold(request)
        if not args.write:
            payload = plan.as_dict(include_content=args.include_content)
            payload["mutation"] = False
            payload["write_required"] = True
            _print(payload)
            return 0

        written = plan.write(args.output_root)
        _print(
            {
                **plan.as_dict(include_content=False),
                "mutation": True,
                "written": [str(path) for path in written],
            }
        )
        return 0

    raise RuntimeError(f"unsupported framework command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
