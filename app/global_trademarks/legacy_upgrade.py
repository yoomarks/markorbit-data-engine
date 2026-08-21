from textwrap import dedent

from app.db import postgres_conn


LEGACY_UPGRADE_SQL = dedent(
    r"""
    DO $$
    DECLARE
        table_name text;
        pk_name text;
    BEGIN
        FOREACH table_name IN ARRAY ARRAY[
            'party_activity',
            'application_link',
            'application_event',
            'application_classification',
            'application_description'
        ]
        LOOP
            IF to_regclass('trademark_au.' || table_name) IS NULL THEN
                CONTINUE;
            END IF;

            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'trademark_au'
                  AND table_name = table_name
                  AND column_name = 'source_row_hash'
            ) THEN
                CONTINUE;
            END IF;

            EXECUTE format('SELECT EXISTS (SELECT 1 FROM trademark_au.%I LIMIT 1)', table_name)
                INTO STRICT pk_name;
            IF pk_name::boolean THEN
                RAISE EXCEPTION
                    'legacy trademark_au.% contains rows; automatic schema-shape upgrade is intentionally blocked',
                    table_name;
            END IF;

            EXECUTE format('ALTER TABLE trademark_au.%I ADD COLUMN source_row_hash text', table_name);

            SELECT c.conname
            INTO pk_name
            FROM pg_constraint c
            WHERE c.conrelid = to_regclass('trademark_au.' || table_name)
              AND c.contype = 'p'
            LIMIT 1;

            IF pk_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE trademark_au.%I DROP CONSTRAINT %I', table_name, pk_name);
            END IF;

            EXECUTE format(
                'ALTER TABLE trademark_au.%I ALTER COLUMN source_row_hash SET NOT NULL',
                table_name
            );
            EXECUTE format(
                'ALTER TABLE trademark_au.%I ADD PRIMARY KEY (source_row_hash)',
                table_name
            );
        END LOOP;

        IF to_regclass('trademark_au.application_classification') IS NOT NULL THEN
            ALTER TABLE trademark_au.application_classification
                ALTER COLUMN classification_system DROP NOT NULL,
                ALTER COLUMN classification DROP NOT NULL;
        END IF;

        IF to_regclass('trademark_au.application_description') IS NOT NULL THEN
            ALTER TABLE trademark_au.application_description
                ALTER COLUMN description_type DROP NOT NULL,
                ALTER COLUMN description_value DROP NOT NULL;
        END IF;
    END
    $$;

    DO $$
    DECLARE
        child_table text;
        constraint_name text;
    BEGIN
        IF to_regclass('trademark_ca.st96_record') IS NULL THEN
            RETURN;
        END IF;

        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'trademark_ca'
              AND table_name = 'st96_record'
              AND column_name = 'record_key'
        ) THEN
            RETURN;
        END IF;

        IF EXISTS (SELECT 1 FROM trademark_ca.st96_record LIMIT 1) THEN
            RAISE EXCEPTION
                'legacy trademark_ca.st96_record contains rows; automatic schema-shape upgrade is intentionally blocked';
        END IF;

        FOREACH child_table IN ARRAY ARRAY[
            'party',
            'goods_service',
            'event',
            'relationship',
            'asset'
        ]
        LOOP
            IF to_regclass('trademark_ca.' || child_table) IS NOT NULL THEN
                EXECUTE format(
                    'SELECT CASE WHEN EXISTS (SELECT 1 FROM trademark_ca.%I LIMIT 1) THEN 1 ELSE 0 END',
                    child_table
                ) INTO constraint_name;
                IF constraint_name = '1' THEN
                    RAISE EXCEPTION
                        'legacy trademark_ca.% contains rows; automatic schema-shape upgrade is intentionally blocked',
                        child_table;
                END IF;
            END IF;
        END LOOP;

        FOR child_table, constraint_name IN
            SELECT c.conrelid::regclass::text, c.conname
            FROM pg_constraint c
            WHERE c.confrelid = 'trademark_ca.st96_record'::regclass
              AND c.contype = 'f'
        LOOP
            EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', child_table, constraint_name);
        END LOOP;

        ALTER TABLE trademark_ca.st96_record ADD COLUMN record_key text;

        SELECT c.conname
        INTO constraint_name
        FROM pg_constraint c
        WHERE c.conrelid = 'trademark_ca.st96_record'::regclass
          AND c.contype = 'p'
        LIMIT 1;
        IF constraint_name IS NOT NULL THEN
            EXECUTE format(
                'ALTER TABLE trademark_ca.st96_record DROP CONSTRAINT %I',
                constraint_name
            );
        END IF;

        ALTER TABLE trademark_ca.st96_record
            ALTER COLUMN record_key SET NOT NULL,
            ADD PRIMARY KEY (record_key),
            ADD UNIQUE (application_number, extension_counter);

        FOREACH child_table IN ARRAY ARRAY[
            'party',
            'goods_service',
            'event',
            'relationship',
            'asset'
        ]
        LOOP
            IF to_regclass('trademark_ca.' || child_table) IS NULL THEN
                CONTINUE;
            END IF;

            EXECUTE format(
                'ALTER TABLE trademark_ca.%I ADD COLUMN source_row_hash text, ADD COLUMN record_key text',
                child_table
            );

            SELECT c.conname
            INTO constraint_name
            FROM pg_constraint c
            WHERE c.conrelid = to_regclass('trademark_ca.' || child_table)
              AND c.contype = 'p'
            LIMIT 1;
            IF constraint_name IS NOT NULL THEN
                EXECUTE format(
                    'ALTER TABLE trademark_ca.%I DROP CONSTRAINT %I',
                    child_table,
                    constraint_name
                );
            END IF;

            EXECUTE format(
                'ALTER TABLE trademark_ca.%I '
                'ALTER COLUMN source_row_hash SET NOT NULL, '
                'ALTER COLUMN record_key SET NOT NULL, '
                'ADD PRIMARY KEY (source_row_hash), '
                'ADD FOREIGN KEY (record_key) REFERENCES trademark_ca.st96_record(record_key)',
                child_table
            );
        END LOOP;
    END
    $$;
    """
).strip()


def upgrade_pre_ingest_country_schemas() -> None:
    """Upgrade the empty schema-only layout created before seed ingestion existed.

    PR #181 could create AU/CA tables with identities that were refined when real
    parsers were added. No ingestion command existed in that version, so the only
    safe automatic migration is for empty legacy tables. Unexpected rows fail
    closed instead of being silently re-keyed with invented provenance.
    """

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(LEGACY_UPGRADE_SQL)
        conn.commit()
