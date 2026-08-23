import pytest

from app.snapshot_delta.detector import Observation
from app.snapshot_delta.ipos_sg_native_facts import (
    native_facts_from_ipos_observation,
    native_facts_from_ipos_row,
)
from app.snapshot_delta.ipos_sg_observation import observation_from_ipos_row


def test_extracts_official_api_native_fields_without_interpretation():
    row = {
        "applicationNumber": "T0210675G",
        "filingDate": "2002-02-19",
        "applicationType": "Trade Mark",
        "tradeMarkType": "Conventional Mark",
        "markStatus": "Expired",
        "markStatusDate": "2012-02-19",
        "statusUpdateDate": "2012-12-20",
        "registrationProcedureCompletionDate": "2003-10-16",
        "expiryDate": "2012-02-19",
        "publicationDate": "2003-08-15",
        "lastModifiedDate": "2026-05-14",
        "irDetails_json": '[{"irNum":"782203","irDate":"2002-02-19"}]',
        "markData_json": '[{"wordsInMark":"rsea one stop safety shop"}]',
        "goodsAndServicesSpecifications_json": (
            '[{"goodsServices":[{"itemCode":"","itemDesc":"Paints"}],'
            '"classNum":"Class 02","classStatus":{"code":"EXP",'
            '"description":"Expired"},"classExpiryDate":"2012-02-19"}]'
        ),
        "priorityClaimsDetails_json": (
            '[{"goodsAndServices":"All goods/services claimed in this application",'
            '"classNum":"02","priorityClaimsDate":"2001-12-19",'
            '"country":"AUSTRALIA"}]'
        ),
        "currentApplicantProprietorDetails_json": (
            '[{"applicantType":{"code":"C","description":"Corporate"},'
            '"uenCompanyCode":"MC003163I","name":"RSEA PTY LTD",'
            '"countryOfIncorporationOrResidence":{"code":"AU",'
            '"description":"AUSTRALIA"}}]'
        ),
        "agentCorrespondenceDetails_json": (
            '[{"representationType":"Agent","agent":'
            '{"uenCompanyCode":"53131173B","name":"RAMDAS & WONG"}}]'
        ),
        "transferData_json": None,
        "licenseData_json": None,
        "documents_json": (
            '[{"fileId":"DEADF62D-4FC9-42B2-A45F-0ABC55A2D843",'
            '"docType":{"code":"MarkLogo","description":"Trade Mark Logo"}}]'
        ),
    }

    facts = native_facts_from_ipos_row(row)

    assert facts.application_number == "T0210675G"
    assert facts.mark_status == "Expired"
    assert facts.filing_date == "2002-02-19"
    assert facts.application_type == "Trade Mark"
    assert facts.trade_mark_type == "Conventional Mark"
    assert facts.mark_status_date == "2012-02-19"
    assert facts.status_update_date == "2012-12-20"
    assert facts.registration_completion_date == "2003-10-16"
    assert facts.expiry_date == "2012-02-19"
    assert facts.publication_date == "2003-08-15"
    assert facts.last_modified_date == "2026-05-14"
    assert facts.international_registration_details[0]["irNum"] == "782203"
    assert facts.mark_data[0]["wordsInMark"] == "rsea one stop safety shop"
    assert facts.goods_services[0]["classNum"] == "Class 02"
    assert facts.priority_claims[0]["country"] == "AUSTRALIA"
    assert facts.applicants[0]["name"] == "RSEA PTY LTD"
    assert facts.agents[0]["agent"]["name"] == "RAMDAS & WONG"
    assert facts.documents[0]["docType"]["code"] == "MarkLogo"
    assert facts.transfer_data == ()
    assert facts.license_data == ()


def test_extracts_csv_heading_aliases_and_accepts_predecoded_arrays():
    facts = native_facts_from_ipos_row(
        {
            "Application Number": " 40202600001A ",
            "Filing Date": " 2026-01-02 ",
            "Mark Status": " Registered ",
            "Mark Data": [{"wordsInMark": "EXAMPLE"}],
            "Goods And Services Specifications": [{"classNum": "Class 09"}],
            "Current Applicant Proprietor Details": [{"name": "Example Pte Ltd"}],
            "Agent Correspondence Details": [],
        }
    )

    assert facts.application_number == "40202600001A"
    assert facts.filing_date == "2026-01-02"
    assert facts.mark_status == "Registered"
    assert facts.mark_data == ({"wordsInMark": "EXAMPLE"},)
    assert facts.goods_services == ({"classNum": "Class 09"},)
    assert facts.applicants == ({"name": "Example Pte Ltd"},)
    assert facts.agents == ()


def test_rejects_missing_required_source_native_identity_or_status():
    with pytest.raises(ValueError, match="Application Number"):
        native_facts_from_ipos_row({"markStatus": "Pending"})

    with pytest.raises(ValueError, match="Mark Status"):
        native_facts_from_ipos_row({"applicationNumber": "T0123456A"})


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("markData_json", "{bad-json", "Mark Data is not valid JSON"),
        ("markData_json", '{"wordsInMark":"EXAMPLE"}', "Mark Data must be a JSON array"),
        ("markData_json", '["EXAMPLE"]', "Mark Data must contain JSON objects"),
    ],
)
def test_rejects_malformed_source_json_arrays(field, value, message):
    row = {
        "applicationNumber": "40202600001A",
        "markStatus": "Pending",
        field: value,
    }

    with pytest.raises(ValueError, match=message):
        native_facts_from_ipos_row(row)


def test_null_optional_native_fields_remain_absent_not_inferred():
    facts = native_facts_from_ipos_row(
        {"applicationNumber": "T0123456A", "markStatus": "Pending"}
    )

    assert facts.filing_date is None
    assert facts.expiry_date is None
    assert facts.mark_data == ()
    assert facts.goods_services == ()
    assert facts.applicants == ()
    assert facts.agents == ()


def test_extracts_native_facts_from_canonical_sg_observation():
    observation = observation_from_ipos_row(
        {"applicationNumber": "40202600001A", "markStatus": "Pending"}
    )

    facts = native_facts_from_ipos_observation(observation)

    assert facts.application_number == observation.entity_id
    assert facts.mark_status == "Pending"


def test_native_fact_observation_boundary_rejects_wrong_jurisdiction_type_or_identity():
    payload = {"applicationNumber": "40202600001A", "markStatus": "Pending"}

    with pytest.raises(ValueError, match="require an SG observation"):
        native_facts_from_ipos_observation(
            Observation(
                "application",
                "40202600001A",
                payload,
                jurisdiction="US",
            )
        )

    with pytest.raises(ValueError, match="require an application observation"):
        native_facts_from_ipos_observation(
            Observation(
                "registration",
                "40202600001A",
                payload,
                jurisdiction="SG",
            )
        )

    with pytest.raises(ValueError, match="identity does not match"):
        native_facts_from_ipos_observation(
            Observation(
                "application",
                "DIFFERENT",
                payload,
                jurisdiction="SG",
            )
        )
