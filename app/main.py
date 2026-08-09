from __future__ import annotations

import sys

from app import main_core as _core
from app.us.alert_api import router as us_alert_router
from app.us.case360_api import router as us_case_360_router
from app.us.change_history_api import router as us_change_history_router
from app.us.deadline_docket_api import router as us_deadline_docket_router
from app.us_assignment.api import router as us_assignment_router
from app.us_assignment.audit_api import router as us_assignment_audit_router
from app.us_ttab.api import router as us_ttab_router
from app.us_ttab.audit_api import router as us_ttab_audit_router


_core.app.description = (
    f"MarkOrbit trademark data engine {_core.ENGINE_VERSION} with US M1.4 + "
    "US Assignment M1.0 + US TTAB M1.1 + US Case 360 M1.0 + US Alert Engine M1.0"
)
_core.app.include_router(us_alert_router)
_core.app.include_router(us_case_360_router)
_core.app.include_router(us_deadline_docket_router)
_core.app.include_router(us_change_history_router)
_core.app.include_router(us_assignment_audit_router)
_core.app.include_router(us_assignment_router)
_core.app.include_router(us_ttab_audit_router)
_core.app.include_router(us_ttab_router)

# Preserve the historical app.main module identity. Existing callers and tests often
# monkeypatch database/guard functions directly on app.main; aliasing to the core
# module keeps those patches in the exact globals used by the original route functions.
sys.modules[__name__] = _core
