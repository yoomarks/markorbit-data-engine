"""Singapore IPOS snapshot source definition for M1.7 activation.

Keeps provider-specific acquisition metadata out of generic snapshot primitives.
"""

from dataclasses import dataclass

from app.jurisdictions.singapore.source import (
    DATASET_ID as IPOS_DATASET_ID,
    FILE_NAME as IPOS_FILE_NAME,
    SOURCE_ID as IPOS_SOURCE_ID,
    SOURCE_TYPE as IPOS_SOURCE_TYPE,
)

from .source import SnapshotSource


@dataclass(frozen=True)
class DataGovSgSnapshotSource(SnapshotSource):
    dataset_url: str
    api_url: str
    initiate_download_url: str
    poll_download_url: str


_DOWNLOAD_API_BASE = (
    "https://api-open.data.gov.sg/v1/public/api/datasets/"
    f"{IPOS_DATASET_ID}"
)

IPOS_SG_TRADEMARK_APPLICATIONS = DataGovSgSnapshotSource(
    source_id=IPOS_SOURCE_ID,
    dataset_id=IPOS_DATASET_ID,
    filename=IPOS_FILE_NAME,
    source_type=IPOS_SOURCE_TYPE,
    dataset_url=f"https://data.gov.sg/datasets/{IPOS_DATASET_ID}/view",
    api_url=(
        "https://data.gov.sg/api/action/datastore_search?resource_id="
        f"{IPOS_DATASET_ID}"
    ),
    initiate_download_url=f"{_DOWNLOAD_API_BASE}/initiate-download",
    poll_download_url=f"{_DOWNLOAD_API_BASE}/poll-download",
)
