from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
from io import BytesIO
import json
from pathlib import Path
import ssl
import urllib.parse
import urllib.request

from app.config import get_settings
from app.us_ttab.parser import iter_ttab_bundles
from app.us_ttab.repository import normalize_snapshot_at, register_ttab_source


BASE_URL = "https://ttabvue.uspto.gov/ttabvue/v"
VALID_PTYS = {"OPP", "CAN", "EXA", "EXT"}


def _snapshot(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return normalize_snapshot_at(parsed)


def _fetch(pno: str, pty: str) -> tuple[bytes, str]:
    query = urllib.parse.urlencode({"pno": pno, "pty": pty, "rawxml": "1"})
    url = f"{BASE_URL}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MarkOrbit-Data-Engine/US-TTAB-source-capture",
            "Accept": "application/xml,text/xml,*/*;q=0.1",
        },
    )
    with urllib.request.urlopen(
        request,
        timeout=30,
        context=ssl.create_default_context(),
    ) as response:
        return response.read(), response.headers.get("Content-Type", "")


def capture(pno: str, pty: str, snapshot_at: datetime, raw_root: Path) -> dict[str, object]:
    pno = pno.strip()
    pty = pty.strip().upper()
    if not (pno.isdigit() and 6 <= len(pno) <= 8):
        raise ValueError("pno must contain 6 to 8 digits")
    if pty not in VALID_PTYS:
        raise ValueError(f"pty must be one of {sorted(VALID_PTYS)}")
    snapshot_at = normalize_snapshot_at(snapshot_at)
    payload, content_type = _fetch(pno, pty)

    bundles = list(iter_ttab_bundles(BytesIO(payload)))
    if len(bundles) != 1:
        raise RuntimeError(f"Expected exactly one TTAB proceeding in raw XML; got {len(bundles)}")
    proceeding = bundles[0].proceeding
    if proceeding.proceeding_number != pno:
        raise RuntimeError(
            f"TTABVUE response proceeding mismatch: requested={pno} "
            f"parsed={proceeding.proceeding_number}"
        )
    if proceeding.proceeding_type_code and proceeding.proceeding_type_code != pty:
        raise RuntimeError(
            f"TTABVUE response type mismatch: requested={pty} "
            f"parsed={proceeding.proceeding_type_code}"
        )

    incoming = raw_root / "incoming" / "us_ttab"
    incoming.mkdir(parents=True, exist_ok=True)
    stamp = snapshot_at.strftime("%Y%m%dT%H%M%S") + f"{snapshot_at.microsecond // 1000:03d}Z"
    path = incoming / f"ttabvue_{pno}_{pty}_{stamp}.xml"
    if path.exists():
        existing = path.read_bytes()
        if existing != payload:
            raise RuntimeError(f"Capture path already exists with different bytes: {path}")
    else:
        path.write_bytes(payload)

    package_id, inserted = register_ttab_source(path, snapshot_at=snapshot_at)
    return {
        "package_id": package_id,
        "inserted": inserted,
        "path": str(path),
        "snapshot_at": snapshot_at.isoformat(),
        "content_type": content_type,
        "byte_length": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "proceeding_number": proceeding.proceeding_number,
        "proceeding_type_code": proceeding.proceeding_type_code,
        "proceeding_type": proceeding.proceeding_type,
        "party_count": len(bundles[0].parties),
        "property_count": len(bundles[0].properties),
        "docket_count": len(bundles[0].docket_entries),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Capture public USPTO TTABVUE raw XML and register it as a US_TTAB source package"
        )
    )
    parser.add_argument("--pno", required=True)
    parser.add_argument("--pty", required=True)
    parser.add_argument(
        "--snapshot-at",
        required=True,
        help="Timezone-aware ISO-8601 timestamp chosen for this source observation",
    )
    args = parser.parse_args()
    result = capture(
        args.pno,
        args.pty,
        _snapshot(args.snapshot_at),
        Path(get_settings().raw_data_root),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
