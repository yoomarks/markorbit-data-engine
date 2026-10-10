from __future__ import annotations

import json

import pytest

from app.tmclass.import_cli import import_bundle, load_bundle


def test_import_bundle_admits_every_package_and_reports_page_kinds():
    observed = []

    def admit(package):
        observed.append(package)
        return {
            "page_kind": package["page"]["pageKind"],
            "replayed": len(observed) == 2,
        }

    evidence = [
        {"page": {"pageKind": "TERM"}},
        {"page": {"pageKind": "CONCEPT_LANGUAGE"}},
    ]

    assert import_bundle(evidence, admit) == {
        "outcome": "TMCLASS_EVIDENCE_BUNDLE_ADMITTED",
        "evidence_count": 2,
        "replayed_count": 1,
        "page_kinds": {"TERM": 1, "CONCEPT_LANGUAGE": 1},
    }
    assert observed == evidence


def test_load_bundle_rejects_a_count_mismatch(tmp_path):
    path = tmp_path / "bundle.json"
    path.write_text(
        json.dumps(
            {
                "contractVersion": "TMCLASS_SOURCE_EVIDENCE_BUNDLE_V1",
                "objectType": "TMCLASS_SOURCE_EVIDENCE_BUNDLE",
                "evidenceCount": 2,
                "evidence": [{}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="count"):
        load_bundle(path)
