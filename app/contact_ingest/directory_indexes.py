from __future__ import annotations

from app.db import postgres_conn


CONTACT_DIRECTORY_INDEX_VERSION = "CONTACT_DIRECTORY_INDEX_V1"

_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS ix_contact_relation_person_recent
ON contact.entity_person_relation(person_id, last_seen_at DESC, entity_id);

CREATE INDEX IF NOT EXISTS ix_contact_raw_record_entity_profile
ON contact.raw_record(entity_id, source_profile)
WHERE entity_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_contact_import_run_success_finished
ON contact.import_run(finished_at DESC)
WHERE status = 'SUCCESS';
"""


def ensure_contact_directory_indexes() -> None:
    """Install additive indexes used only by read-heavy Contacts admin views."""
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_INDEX_SQL)
        conn.commit()
