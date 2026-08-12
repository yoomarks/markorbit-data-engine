from __future__ import annotations

from app.contact_ingest import task_queue_v11 as _legacy

# V1.6 accepts a common historical filename typo (.josn) as JSON. Mutate the
# preserved task-control suffix set so all existing discovery/apply functions
# keep their V1.1 behavior while seeing the additional source type.
_legacy.SUPPORTED_CONTACT_SUFFIXES.add(".josn")

from app.contact_ingest.task_queue_v11 import *  # noqa: E402,F401,F403

SUPPORTED_CONTACT_SUFFIXES = _legacy.SUPPORTED_CONTACT_SUFFIXES
