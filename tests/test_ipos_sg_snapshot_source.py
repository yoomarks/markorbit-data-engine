from app.snapshot_delta.ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS


def test_ipos_sg_snapshot_source_contract():
    assert IPOS_SG_TRADEMARK_APPLICATIONS.source_id == "IPOS_SG_TRADEMARK_APPLICATIONS"
    assert IPOS_SG_TRADEMARK_APPLICATIONS.dataset_id == "d_6145acb2130bf781165258e76a584383"
    assert IPOS_SG_TRADEMARK_APPLICATIONS.filename == "IPOSTradeMarkApplications.csv"
    assert IPOS_SG_TRADEMARK_APPLICATIONS.source_type == "current_snapshot"
