from app.us_tsdr.adapter import source_contract
from app.us_tsdr.intent import (
    INTENT_CONTRACT_VERSION,
    TsdrAcquisitionIntent,
    TsdrIntentError,
    TsdrResourceType,
    contract_descriptor,
    dedupe_intents,
)


def test_sparse_intent_contract_forbids_data_engine_opportunity_discovery() -> None:
    descriptor = contract_descriptor()
    assert descriptor["version"] == INTENT_CONTRACT_VERSION
    assert descriptor["decision_owner"] == "UPSTREAM_CAPABILITY_OR_PRODUCT"
    assert descriptor["opportunity_discovery_permitted"] is False
    assert descriptor["commercial_scoring_permitted"] is False
    assert source_contract()["data_engine_decides_acquisition_need"] is False


def test_sparse_intent_supports_three_independent_resource_types() -> None:
    intents = [
        TsdrAcquisitionIntent(
            "90817045",
            TsdrResourceType.STATUS_CONTACT,
            900_000,
            ("CLIENT_PORTFOLIO",),
            "capability.opportunity.discovery",
        ),
        TsdrAcquisitionIntent(
            "90817045",
            TsdrResourceType.DOCUMENT,
            800_000,
            ("OA_BUSINESS_CHAIN",),
            "markreg.case",
        ),
        TsdrAcquisitionIntent(
            "90817045",
            TsdrResourceType.LOGO,
            200_000,
            ("ASSET_COMPLETION",),
            "markreg.asset",
        ),
    ]
    result = dedupe_intents(intents)
    assert len(result) == 3
    assert {item.resource_type for item in result} == set(TsdrResourceType)


def test_sparse_intent_dedupes_by_serial_and_resource_without_inference() -> None:
    low = TsdrAcquisitionIntent(
        "90817045",
        TsdrResourceType.STATUS_CONTACT,
        100,
        ("RELATED_MARK",),
        "workspace.rule",
    )
    high = TsdrAcquisitionIntent(
        "90817045",
        TsdrResourceType.STATUS_CONTACT,
        900,
        ("CLIENT_PORTFOLIO",),
        "workspace.rule",
    )
    assert dedupe_intents([low, high]) == [high]


def test_sparse_intent_requires_explicit_reason_and_requester() -> None:
    try:
        TsdrAcquisitionIntent(
            "90817045",
            TsdrResourceType.STATUS_CONTACT,
            1,
            (),
            "workspace.rule",
        )
    except TsdrIntentError:
        pass
    else:
        raise AssertionError("intent without reason must fail closed")
