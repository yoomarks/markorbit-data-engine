from __future__ import annotations

from typing import Any

from app.cn.discovery_preliminary_publication import (
    PreliminaryPublicationDiscoveryRequest,
    execute_page,
)
from app.db import clickhouse_client


def execute_live_page(request: PreliminaryPublicationDiscoveryRequest) -> dict[str, Any]:
    """Execute the bounded discovery read through the Data Engine owner layer."""
    return execute_page(request, client=clickhouse_client())
