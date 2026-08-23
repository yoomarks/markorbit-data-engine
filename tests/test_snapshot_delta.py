import pytest

from app.snapshot_delta.detector import Observation, compare_observations


def observation(
    entity_id: str,
    payload: dict,
    *,
    jurisdiction: str = "SG",
    entity_type: str = "application",
) -> Observation:
    return Observation(
        entity_type,
        entity_id,
        payload,
        jurisdiction=jurisdiction,
    )


def test_update_detection():
    old = observation("SG1", {"status": "PENDING"})
    new = observation("SG1", {"status": "REGISTERED"})
    event = compare_observations(old, new)
    assert event.event_type == "UPDATE_DETECTED"
    assert event.entity_id == "SG1"
    assert event.jurisdiction == "SG"


def test_create_detection():
    event = compare_observations(None, observation("SG1", {}))
    assert event.event_type == "CREATE_DETECTED"
    assert event.after == {}


def test_delete_detection():
    event = compare_observations(observation("SG1", {"status": "PENDING"}), None)
    assert event.event_type == "DELETE_DETECTED"
    assert event.before["status"] == "PENDING"


def test_same_observation_has_no_delta():
    old = observation("SG1", {"status": "PENDING"})
    new = observation("SG1", {"status": "PENDING"})
    assert compare_observations(old, new) is None


def test_non_sg_observation_propagates_jurisdiction():
    event = compare_observations(
        None,
        observation("US1", {"status": "PENDING"}, jurisdiction="US"),
    )

    assert event is not None
    assert event.jurisdiction == "US"


def test_jurisdiction_is_required_and_non_empty():
    with pytest.raises(TypeError):
        Observation("application", "X1", {})

    with pytest.raises(ValueError, match="jurisdiction must be non-empty"):
        Observation("application", "X1", {}, jurisdiction="  ")


def test_cross_jurisdiction_comparison_is_rejected():
    old = observation("X1", {"status": "PENDING"}, jurisdiction="SG")
    new = observation("X1", {"status": "PENDING"}, jurisdiction="US")

    with pytest.raises(ValueError, match="different jurisdictions"):
        compare_observations(old, new)


def test_cross_identity_comparison_is_rejected():
    old = observation("X1", {"status": "PENDING"})
    new_id = observation("X2", {"status": "REGISTERED"})
    new_type = observation(
        "X1",
        {"status": "REGISTERED"},
        entity_type="registration",
    )

    with pytest.raises(ValueError, match="different identities"):
        compare_observations(old, new_id)

    with pytest.raises(ValueError, match="different identities"):
        compare_observations(old, new_type)
