import pytest

from app.snapshot_delta.ipos_sg_observation import observation_from_ipos_row


def test_observation_uses_csv_application_number():
    row = {"Application Number": " T0123456A ", "Mark Status": "Pending"}

    observation = observation_from_ipos_row(row)

    assert observation.entity_type == "application"
    assert observation.entity_id == "T0123456A"
    assert observation.payload == row


def test_observation_accepts_official_api_column_name():
    observation = observation_from_ipos_row(
        {"applicationNumber": "40202600001A", "markStatus": "Registered"}
    )

    assert observation.entity_id == "40202600001A"


def test_observation_rejects_missing_application_number():
    with pytest.raises(ValueError, match="missing Application Number"):
        observation_from_ipos_row({"Mark Status": "Pending"})
