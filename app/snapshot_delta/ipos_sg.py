"""Singapore IPOS snapshot source definition for M1.7 activation.

Keeps provider-specific acquisition metadata out of generic snapshot primitives.
"""

from dataclasses import dataclass

from .source import SnapshotSource


@dataclass(frozen=True)
class DataGovSgSnapshotSource(SnapshotSource):
    dataset_url: str
    api_url: str
    initiate_download_url: str
    poll_download_url: str


_DATASET_ID = "d_6145acb2130bf781165258e76a584383"
_DOWNLOAD_API_BASE = f"https://api-open.data.gov.sg/v1/public/api/datasets/{_DATASET_ID}"

IPOS_SG_TRADEMARK_APPLICATIONS = DataGovSgSnapshotSource(
    source_id="IPOS_SG_TRADEMARK_APPLICATIONS",
    dataset_id=_DATASET_ID,
    filename="IPOSTradeMarkApplications.csv",
    source_type="current_snapshot",
    dataset_url=f"https://data.gov.sg/datasets/{_DATASET_ID}/view",
    api_url=(
        "https://data.gov.sg/api/action/datastore_search?resource_id="
        f"{_DATASET_ID}"
    ),
    initiate_download_url=f"{_DOWNLOAD_API_BASE}/initiate-download",
    poll_download_url=f"{_DOWNLOAD_API_BASE}/poll-download",
)
