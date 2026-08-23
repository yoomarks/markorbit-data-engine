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
    assert "durable generic delta/event evidence" in activation


def test_singapore_activation_docs_record_native_facts_without_overclaiming():
    activation = ACTIVATION_DOC.read_text(encoding="utf-8")

    assert "current 39-column IPOS source schema" in activation
    assert "Malformed JSON families fail closed" in activation
    assert "without introducing legal interpretation" in activation
    assert "datastore `_id` field is treated as provider metadata" in activation


def test_singapore_activation_docs_keep_family_changes_neutral():
    activation = ACTIVATION_DOC.read_text(encoding="utf-8")

    assert "deterministic neutral source-family changes" in activation
    assert "does not convert source phrases" in activation
    assert "Creation and deletion remain responsibilities of the generic snapshot/delta layer" in activation
    assert "dedicated semantic event remains a separate reviewed layer" in activation


def test_singapore_activation_docs_record_authoritative_schema_gate():
    activation = ACTIVATION_DOC.read_text(encoding="utf-8")

    assert "complete authoritative 39-column source contract" in activation
    assert "before the partial file can replace the accepted snapshot" in activation
    assert "Missing or newly introduced source columns fail closed" in activation
    assert "authoritative source-column contract" in activation


def test_singapore_activation_docs_record_lifecycle_and_live_probe_schema_boundaries():
    activation = ACTIVATION_DOC.read_text(encoding="utf-8")

    assert "snapshot lifecycle is also an explicit acceptance boundary" in activation
    assert "alternate or custom downloader" in activation
    assert "prevents an incomplete schema from advancing the accepted-current pointer" in activation
    assert "lightweight live-source probe applies the same complete contract" in activation


def test_singapore_activation_docs_record_durable_native_family_evidence_boundary():
    activation = ACTIVATION_DOC.read_text(encoding="utf-8")

    assert "bounded-memory follow-up scans" in activation
    assert "durable neutral native-family evidence with snapshot lineage" in activation
    assert "before the accepted-current pointer advances" in activation
    assert "create/delete-only cycle therefore does not create an empty native-family sidecar" in activation
