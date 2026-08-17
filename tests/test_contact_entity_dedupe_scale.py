from __future__ import annotations

import inspect

from app.contact_ingest import entity_dedupe


def test_snapshot_loader_aggregates_each_evidence_family_once() -> None:
    source = inspect.getsource(entity_dedupe._load_snapshots)

    assert "contact_entity_ids AS MATERIALIZED" in source
    assert "eligible_entities AS MATERIALIZED" in source
    assert "mention_stats AS" in source
    assert "raw_stats AS" in source
    assert "identifier_stats AS" in source
    assert "channel_stats AS" in source

    # Real trademark corpora can dwarf the contact set. Never regress to one
    # full mention/raw/identifier/channel aggregate per contact entity.
    assert "(SELECT count(*) FROM entity.entity_mention" not in source
    assert "(SELECT count(*) FROM contact.raw_record" not in source
    assert "SELECT jsonb_agg(" not in source.split("identifier_stats AS", 1)[0]
    assert "SELECT array_agg(DISTINCT ec.channel_type" not in source.split(
        "channel_stats AS", 1
    )[0]


def test_snapshot_loader_filters_country_before_large_evidence_joins() -> None:
    source = inspect.getsource(entity_dedupe._load_snapshots)
    eligible_pos = source.index("eligible_entities AS MATERIALIZED")
    country_pos = source.index("{country_clause}")
    mention_pos = source.index("mention_stats AS")

    assert eligible_pos < country_pos < mention_pos
    assert "JOIN eligible_entities AS e ON e.entity_id = m.entity_id" in source
    assert "JOIN eligible_entities AS e ON e.entity_id = rr.entity_id" in source
    assert "JOIN eligible_entities AS e ON e.entity_id = i.entity_id" in source
