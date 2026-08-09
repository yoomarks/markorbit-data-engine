from __future__ import annotations

import sys

from app import main_core as _core
from app.us.change_history_api import router as us_change_history_router
from app.us.deadline_docket_api import router as us_deadline_docket_router


_core.app.description = f"MarkOrbit trademark data engine {_core.ENGINE_VERSION} with US M1.4"
_core.app.include_router(us_deadline_docket_router)
_core.app.include_router(us_change_history_router)

# Preserve the historical app.main module identity. Existing callers and tests often
# monkeypatch database/guard functions directly on app.main; aliasing to the core
# module keeps those patches in the exact globals used by the original route functions.
sys.modules[__name__] = _core
