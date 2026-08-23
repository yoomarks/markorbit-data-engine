"""Singapore IPOS snapshot source definition for M1.7 activation.

Keeps source metadata separate from ingestion and projection layers.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SnapshotSource:
    source_id: str
    dataset_id: str
    filename: str
    source_type: str = "current_snapshot"


IPOS_SG_TRADEMARK_APPLICATIONS = SnapshotSource(
    source_id="IPOS_SG_TRADEMARK_APPLICATIONS",
    dataset_id="d_6145acb2130bf781165258e76a584383",
    filename="IPOSTradeMarkApplications.csv",
)
