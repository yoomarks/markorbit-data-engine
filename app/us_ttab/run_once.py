from __future__ import annotations

import json
from app.config import get_settings
from app.us_ttab.jobs import run_ttab_once


def main() -> None:
    print(json.dumps(run_ttab_once(get_settings().raw_data_root), indent=2, default=str))


if __name__ == "__main__":
    main()
