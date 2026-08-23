"""Singapore IPOS snapshot source definition for M1.7 activation.

Keeps source metadata separate from ingestion and projection layers.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SnapshotSource:
    source_id: str
    dataset_id: str
    filename: str
    dataset_url: str
    api_url: str
    initiate_download_url: str
    poll_download_url: str
    source_type: str = "current_snapshot"


_DATASET_ID = "d_6145acb2130bf781165258e76a584383"
_DOWNLOAD_API_BASE = f"https://api-open.data.gov.sg/v1/public/api/datasets/{_DATASET_ID}"

IPOS_SG_TRADEMARK_APPLICATIONS = SnapshotSource(
    source_id="IPOS_SG_TRADEMARK_APPLICATIONS",
    dataset_id=_DATASET_ID,
    filename="IPOSTradeMarkApplications.csv",
    dataset_url=f"https://data.gov.sg/datasets/{_DATASET_ID}/view",
    api_url=(
        "https://data.gov.sg/api/action/datastore_search?resource_id="
        f"{_DATASET_ID}"
    ),
    initiate_download_url=f"{_DOWNLOAD_API_BASE}/initiate-download",
    poll_download_url=f"{_DOWNLOAD_API_BASE}/poll-download",
)
