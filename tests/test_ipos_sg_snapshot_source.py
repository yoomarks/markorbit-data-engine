from app.snapshot_delta.ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS


def test_ipos_sg_snapshot_source_contract():
    source = IPOS_SG_TRADEMARK_APPLICATIONS

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
