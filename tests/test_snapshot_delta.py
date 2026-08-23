from app.snapshot_delta.detector import Observation, compare_observations


def test_update_detection():
    old = Observation("application", "SG1", {"status": "PENDING"})
    new = Observation("application", "SG1", {"status": "REGISTERED"})
    event = compare_observations(old, new)
    assert event.event_type == "UPDATE_DETECTED"


def test_create_detection():
    event = compare_observations(None, Observation("application", "SG1", {}))
    assert event.event_type == "CREATE_DETECTED"
