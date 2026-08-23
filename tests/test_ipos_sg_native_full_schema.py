from app.snapshot_delta.ipos_sg_native_facts import (
    IPOS_NATIVE_SOURCE_FIELDS,
    ipos_native_schema_drift,
    native_facts_from_ipos_row,
)


def test_full_official_ipos_source_schema_is_covered_without_interpretation():
    row = {
        "applicationNumber": "T0210675G",
        "filingDate": "2002-02-19",
        "internationalRegDate": "2002-02-19",
        "singaporeProtectionDate": "2002-02-19",
        "seriesMarkNum": "0",
        "applicationType": "Trade Mark",
        "tradeMarkType": "Conventional Mark",
        "descriptionParticularFeatureOfMark": "Three-dimensional mark.",
        "applicationDate": "2002-07-11",
        "markStatus": "Expired",
        "markStatusDate": "2012-02-19",
        "statusUpdateDate": "2012-12-20",
        "registrationProcedureCompletionDate": "2003-10-16",
        "expiryDate": "2012-02-19",
        "publicationDate": "2003-08-15",
        "lastModifiedDate": "2026-05-14",
        "journalData_json": (
            '[{"journalNum":"059/2003","journalDate":"2003-08-15",'
            '"journalStatus":{"code":"PUB","description":"Published"}}]'
        ),
        "irDetails_json": '[{"irNum":"782203","irDate":"2002-02-19"}]',
        "iaDetails_json": '[{"iaNum":"M0400084H","irNum":"908024"}]',
        "transformationData_json": (
            '[{"applicationNum":"T1410917I","irNum":"1207285"}]'
        ),
        "transformationIntoData_json": (
            '[{"applicationNum":"T0818139D","classNum":"02, 07",'
            '"dateOfProtection":"2006-09-25"}]'
        ),
        "replacementData_json": (
            '[{"applicationNum":"T1311850F","irNum":"933116","classNum":"17"}]'
        ),
        "priorityData_json": '[{"sourceValue":"preserved"}]',
        "replacementReplacesData_json": '[{"sourceValue":"preserved"}]',
        "markClausesData_json": '[{"value":"Priority Date Claimed"}]',
        "markData_json": '[{"wordsInMark":"rsea one stop safety shop"}]',
        "hmgCases_json": (
            '[{"caseNum":"C0101T0320668B","caseType":{"code":"TM_REVOCATION"},'
            '"caseStatus":{"code":"PENDING"},"classNum":"12"}]'
        ),
        "otherEntriesData_json": (
            '[{"events":{"code":"TYPE_CF_CM8","description":"Full Transfer of Ownership",'
            '"eventDate":"2010-01-28"}}]'
        ),
        "logogramData_json": (
            '[{"applicationStatus":"Recorded","lodgementDate":"1994-07-14",'
            '"name":"GERMANY","address":""}]'
        ),
        "licenseData_json": '[{"licenseType":"Non-Exclusive Licence"}]',
        "grantorData_json": '[{"grantorName":"Example Grantor"}]',
        "granteeData_json": '[{"granteeName":"Example Grantee"}]',
        "securityInterestData_json": (
            '[{"securityInterestRefNo":"2006-SI1012G",'
            '"lodgementDate":"2006-12-18"}]'
        ),
        "transferData_json": (
            '[{"dateOfTransferOfOwnership":"2005-12-21",'
            '"fullOrPartial":"Full Transfer of Ownership"}]'
        ),
        "documents_json": '[{"fileId":"DEADF62D","fileName":"T0210675G.jpg"}]',
        "goodsAndServicesSpecifications_json": '[{"classNum":"Class 02"}]',
        "priorityClaimsDetails_json": '[{"classNum":"02","country":"AUSTRALIA"}]',
        "currentApplicantProprietorDetails_json": '[{"name":"RSEA PTY LTD"}]',
        "agentCorrespondenceDetails_json": '[{"representationType":"Agent"}]',
    }

    facts = native_facts_from_ipos_row(row)

    assert facts.international_registration_date == "2002-02-19"
    assert facts.singapore_protection_date == "2002-02-19"
    assert facts.series_mark_number == "0"
    assert facts.description_particular_feature_of_mark == "Three-dimensional mark."
    assert facts.application_date == "2002-07-11"
    assert facts.journal_data[0]["journalNum"] == "059/2003"
    assert facts.ia_details[0]["iaNum"] == "M0400084H"
    assert facts.transformation_data[0]["applicationNum"] == "T1410917I"
    assert facts.transformation_into_data[0]["dateOfProtection"] == "2006-09-25"
    assert facts.replacement_data[0]["irNum"] == "933116"
    assert facts.priority_data[0]["sourceValue"] == "preserved"
    assert facts.replacement_replaces_data[0]["sourceValue"] == "preserved"
    assert facts.mark_clauses_data[0]["value"] == "Priority Date Claimed"
    assert facts.hmg_cases[0]["caseNum"] == "C0101T0320668B"
    assert facts.other_entries_data[0]["events"]["code"] == "TYPE_CF_CM8"
    assert facts.logogram_data[0]["applicationStatus"] == "Recorded"
    assert facts.grantor_data[0]["grantorName"] == "Example Grantor"
    assert facts.grantee_data[0]["granteeName"] == "Example Grantee"
    assert facts.security_interest_data[0]["securityInterestRefNo"] == "2006-SI1012G"


def test_full_schema_supports_official_csv_display_headings():
    facts = native_facts_from_ipos_row(
        {
            "Application Number": "40202600001A",
            "International Registration Date": "2026-01-01",
            "Singapore Protection Date": "2026-01-02",
            "Series Mark Number": "2",
            "Description Particular Feature Of Mark": "Source description",
            "Application Date": "2026-01-03",
            "Mark Status": "Registered",
            "Journal Data": [{"journalNum": "001/2026"}],
            "HMG Cases": [{"caseNum": "CASE-1"}],
            "Security Interest Data": [{"securityInterestRefNo": "SI-1"}],
        }
    )

    assert facts.international_registration_date == "2026-01-01"
    assert facts.singapore_protection_date == "2026-01-02"
    assert facts.series_mark_number == "2"
    assert facts.description_particular_feature_of_mark == "Source description"
    assert facts.application_date == "2026-01-03"
    assert facts.journal_data == ({"journalNum": "001/2026"},)
    assert facts.hmg_cases == ({"caseNum": "CASE-1"},)
    assert facts.security_interest_data == ({"securityInterestRefNo": "SI-1"},)


def test_native_schema_contract_covers_all_39_ipos_source_columns():
    assert len(IPOS_NATIVE_SOURCE_FIELDS) == 39
    assert len(set(IPOS_NATIVE_SOURCE_FIELDS)) == 39
    assert "applicationNumber" in IPOS_NATIVE_SOURCE_FIELDS
    assert "securityInterestData_json" in IPOS_NATIVE_SOURCE_FIELDS
    assert "agentCorrespondenceDetails_json" in IPOS_NATIVE_SOURCE_FIELDS
    assert "_id" not in IPOS_NATIVE_SOURCE_FIELDS


def test_native_schema_drift_ignores_provider_row_id_and_flags_changes():
    missing, unknown = ipos_native_schema_drift((*IPOS_NATIVE_SOURCE_FIELDS, "_id"))
    assert missing == ()
    assert unknown == ()

    missing, unknown = ipos_native_schema_drift(
        (
            *IPOS_NATIVE_SOURCE_FIELDS[:-1],
            "futureSourceField",
            "_id",
        )
    )
    assert missing == ("agentCorrespondenceDetails_json",)
    assert unknown == ("futureSourceField",)
