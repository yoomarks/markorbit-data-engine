from __future__ import annotations

import json

import pytest

from app.tmclass.import_corpus_cli import (
    RECEIPT_VERSION,
    import_corpus_pass,
    receipt_path,
    sha256_file,
)


def write_bundle(path, evidence):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "contractVersion": "TMCLASS_SOURCE_EVIDENCE_BUNDLE_V1",
                "objectType": "TMCLASS_SOURCE_EVIDENCE_BUNDLE",
                "evidenceCount": len(evidence),
                "evidence": evidence,
            }
        ),
        encoding="utf-8",
    )


def test_import_corpus_pass_is_resumable_and_writes_bound_receipts(tmp_path):
    bundles = tmp_path / "bundles"
    receipts = tmp_path / "receipts"
    first = bundles / "term" / "one.bundle.json"
    second = bundles / "concept" / "two.bundle.json"
    write_bundle(first, [{"page": {"pageKind": "TERM"}}])
    write_bundle(second, [{"page": {"pageKind": "CONCEPT_OVERVIEW"}}])
    observed = []

    def admit(package):
        observed.append(package)
        return {"page_kind": package["page"]["pageKind"], "replayed": False}

    assert import_corpus_pass(bundles, receipts, admit) == {
        "admitted_batches": 2,
        "admitted_evidence": 2,
        "replayed_evidence": 0,
        "skipped_batches": 0,
    }
    assert import_corpus_pass(bundles, receipts, admit) == {
        "admitted_batches": 0,
        "admitted_evidence": 0,
        "replayed_evidence": 0,
        "skipped_batches": 2,
    }
    assert len(observed) == 2
    receipt = json.loads(receipt_path(bundles, receipts, first).read_text(encoding="utf-8"))
    assert receipt["schema_version"] == RECEIPT_VERSION
    assert receipt["bundle_sha256"] == sha256_file(first)


def test_import_corpus_pass_rejects_a_receipt_for_changed_bundle(tmp_path):
    bundles = tmp_path / "bundles"
    receipts = tmp_path / "receipts"
    bundle = bundles / "term" / "one.bundle.json"
    write_bundle(bundle, [{"page": {"pageKind": "TERM"}}])

    def admit(package):
        return {"page_kind": package["page"]["pageKind"], "replayed": False}

    import_corpus_pass(bundles, receipts, admit)
    write_bundle(bundle, [{"page": {"pageKind": "CONCEPT_LANGUAGE"}}])
    with pytest.raises(ValueError, match="does not match"):
        import_corpus_pass(bundles, receipts, admit)
