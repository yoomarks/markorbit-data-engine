from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.trademark_factory.audit import audit_country_factory
from app.trademark_factory.capabilities import derive_country_capabilities
from app.trademark_factory.registry import factory_registry
from app.trademark_factory.scaffold import build_country_scaffold
from app.trademark_framework.contracts import (
    DataFormat,
    SourceAdapterKind,
    TransportKind,
    UpdateSemantics,
)


def _enum_choice(enum_type, value: str):
    try:
        return enum_type(value.strip().upper())
    except ValueError as exc:
        choices = ", ".join(member.value for member in enum_type)
        raise argparse.ArgumentTypeError(f"expected one of: {choices}") from exc


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.trademark_factory.cli",
        description="Audit and scaffold reusable trademark jurisdiction packs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("audit", help="Audit factory/framework contracts without DB writes.")

    profile = subparsers.add_parser("profile", help="Show one framework-backed country profile.")
    profile.add_argument("--jurisdiction", required=True)

    capabilities = subparsers.add_parser(
        "capabilities", help="Show derived source/store capabilities for one or all countries."
    )
    capabilities.add_argument("--jurisdiction")

    scaffold = subparsers.add_parser(
        "scaffold",
        help="Generate a framework-aligned country skeleton. No writes unless --write is supplied.",
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
    scaffold.add_argument("--write", action="store_true")
    scaffold.add_argument("--output-root", type=Path, default=Path("."))
    scaffold.add_argument("--include-content", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    registry = factory_registry()

    if args.command == "audit":
        report = audit_country_factory(registry)
        _print(report.as_dict())
        return 0 if report.ready else 2

    if args.command == "profile":
        _print(registry.profile(args.jurisdiction).as_dict())
        return 0

    if args.command == "capabilities":
        if args.jurisdiction:
            report = derive_country_capabilities(registry.country_pack(args.jurisdiction))
            _print(report.as_dict())
        else:
            _print(
                [derive_country_capabilities(pack).as_dict() for pack in registry.packs]
            )
        return 0

    if args.command == "scaffold":
        plan = build_country_scaffold(
            jurisdiction=args.jurisdiction,
            source_id=args.source_id,
            store_schema=args.store_schema,
            adapter_kind=args.adapter_kind,
            data_format=args.data_format,
            update_semantics=args.update_semantics,
            transport=args.transport,
        )
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

    raise RuntimeError(f"unsupported factory command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
