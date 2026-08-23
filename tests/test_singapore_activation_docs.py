from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE_DOC = ROOT / "docs" / "SNAPSHOT_DELTA_ARCHITECTURE_V1.md"
ACTIVATION_DOC = ROOT / "docs" / "SINGAPORE_IPOS_ACTIVATION_PLAN.md"


def test_singapore_activation_docs_reflect_accepted_source_lifecycle():
    architecture = ARCHITECTURE_DOC.read_text(encoding="utf-8")
    activation = ACTIVATION_DOC.read_text(encoding="utf-8")

    assert "Future SG adapter MUST implement" not in architecture
    assert "Singapore IPOS is the first activated source" in architecture
    assert "Source/lifecycle activation is accepted" in activation
    assert "source-native facts remain separated from interpretation" in activation
    assert "recurring production" in activation.lower()


def test_singapore_activation_docs_preserve_snapshot_first_storage_boundary():
    architecture = ARCHITECTURE_DOC.read_text(encoding="utf-8")
    activation = ACTIVATION_DOC.read_text(encoding="utf-8")

    assert "daily identical full csv files are not permanent history by default" in architecture.lower()
    assert "accepted current full snapshot" in activation
    assert "durable delta/event evidence" in activation


def test_singapore_activation_docs_record_native_facts_without_overclaiming():
    activation = ACTIVATION_DOC.read_text(encoding="utf-8")

    assert "first field-level native fact extraction slice is implemented" in activation
    assert "Malformed JSON families fail closed" in activation
    assert "does not introduce legal interpretation" in activation
