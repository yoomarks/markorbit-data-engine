from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.cn.profile import profile_package


def main() -> None:
    parser = argparse.ArgumentParser(description="MarkOrbit CN package tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    profile_parser = subparsers.add_parser("profile", help="Profile a CN ZIP without databases")
    profile_parser.add_argument("package", type=Path)
    profile_parser.add_argument("--output", type=Path)
    profile_parser.add_argument("--encoding", default="auto")

    args = parser.parse_args()

    if args.command == "profile":
        result = profile_package(args.package, forced_encoding=args.encoding)
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        print(text)


if __name__ == "__main__":
    main()
