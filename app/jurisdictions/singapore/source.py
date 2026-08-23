"""IPOS Singapore source metadata.

Snapshot-first source declaration. This module does not infer legal meaning.
"""

SOURCE_ID = "IPOS_SG_TRADEMARK_APPLICATIONS"
DATASET_ID = "d_6145acb2130bf781165258e76a584383"
FILE_NAME = "IPOSTradeMarkApplications.csv"


def source_metadata() -> dict[str, str]:
    return {
        "jurisdiction": "SG",
        "source_id": SOURCE_ID,
        "dataset_id": DATASET_ID,
        "file_name": FILE_NAME,
        "source_type": "current_snapshot",
    }
