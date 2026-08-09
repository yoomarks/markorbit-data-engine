from __future__ import annotations

import json

from app.config import get_settings
from app.us_assignment.jobs import run_assignment_once


def main() -> None:
    result = run_assignment_once(get_settings().raw_data_root, retry=False)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
