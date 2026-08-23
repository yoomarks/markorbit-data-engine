import pytest

from app.snapshot_delta.detector import Observation, compare_observations


def test_update_carries_snapshot_evidence_references():
    old = Observation("application", "SG1", {"status": "PENDING"}, jurisdiction="SG")
    new = Observation("application", "SG1", {"status": "REGISTERED"}, jurisdiction="SG")

    event = compare_observations(
        old,
        new,
        previous_evidence_reference="manifest:old",
        current_evidence_reference="manifest:new",
    )

    assert event.jurisdiction == "SG"
    assert event.before_evidence_reference == "manifest:old"
    assert event.after_evidence_reference == "manifest:new"


def test_cross_jurisdiction_observations_cannot_be_compared():
    old = Observation("application", "1", {}, jurisdiction="SG")
    new = Observation("application", "1", {}, jurisdiction="US")

    with pytest.raises(ValueError, match="different jurisdictions"):
        compare_observations(old, new)
