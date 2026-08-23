from app.snapshot_delta.ipos_sg_native_facts import native_facts_from_ipos_row


def test_blank_source_json_cells_remain_absent():
    facts = native_facts_from_ipos_row(
        {
            "applicationNumber": "40202600001A",
            "markStatus": "Pending",
            "markData_json": "   ",
            "goodsAndServicesSpecifications_json": "\t",
        }
    )

    assert facts.mark_data == ()
    assert facts.goods_services == ()
