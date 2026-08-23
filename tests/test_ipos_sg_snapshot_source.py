from app.snapshot_delta.ipos_sg import (
    DataGovSgSnapshotSource,
    IPOS_SG_TRADEMARK_APPLICATIONS,
)
from app.snapshot_delta.source import SnapshotSource


def test_ipos_sg_snapshot_source_contract():
    source = IPOS_SG_TRADEMARK_APPLICATIONS

    assert isinstance(source, SnapshotSource)
    assert isinstance(source, DataGovSgSnapshotSource)
    assert source.source_id == "IPOS_SG_TRADEMARK_APPLICATIONS"
    assert source.dataset_id == "d_6145acb2130bf781165258e76a584383"
    assert source.filename == "IPOSTradeMarkApplications.csv"
    assert source.source_type == "current_snapshot"
    assert source.dataset_url == (
        "https://data.gov.sg/datasets/d_6145acb2130bf781165258e76a584383/view"
    )
    assert source.api_url == (
        "https://data.gov.sg/api/action/datastore_search?resource_id="
        "d_6145acb2130bf781165258e76a584383"
    )
    assert source.initiate_download_url == (
        "https://api-open.data.gov.sg/v1/public/api/datasets/"
        "d_6145acb2130bf781165258e76a584383/initiate-download"
    )
    assert source.poll_download_url == (
        "https://api-open.data.gov.sg/v1/public/api/datasets/"
        "d_6145acb2130bf781165258e76a584383/poll-download"
    )


def test_generic_snapshot_source_does_not_require_provider_specific_urls():
    source = SnapshotSource(
        source_id="EXAMPLE",
        dataset_id="dataset-1",
        filename="snapshot.csv",
        source_type="current_snapshot",
    )

    assert source.source_id == "EXAMPLE"
    assert not hasattr(source, "poll_download_url")
