from app.snapshot_delta.detector import Observation, compare_observations


def test_update_detection():
    old = Observation("application", "SG1", {"status": "PENDING"})
    new = Observation("application", "SG1", {"status": "REGISTERED"})
    event = compare_observations(old, new)
    assert event.event_type == "UPDATE_DETECTED"
    assert event.entity_id == "SG1"


def test_create_detection():
    event = compare_observations(None, Observation("application", "SG1", {}))
    assert event.event_type == "CREATE_DETECTED"
    assert event.after == {}


def test_delete_detection():
    event = compare_observations(Observation("application", "SG1", {"status": "PENDING"}), None)
    assert event.event_type == "DELETE_DETECTED"
    assert event.before["status"] == "PENDING"


def test_same_observation_has_no_delta():
    old = Observation("application", "SG1", {"status": "PENDING"})
    new = Observation("application", "SG1", {"status": "PENDING"})
    assert compare_observations(old, new) is None
