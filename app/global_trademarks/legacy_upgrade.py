from textwrap import dedent

from app.db import postgres_conn


LEGACY_UPGRADE_SQL = dedent(
    r"""
    DO $$
    DECLARE
        au_table text;
        pk_name text;
        has_rows boolean;
    BEGIN
        FOREACH au_table IN ARRAY ARRAY[
            'party_activity',
            'application_link',
            'application_event',
            'application_classification',
            'application_description'
        ]
        LOOP
            IF to_regclass('trademark_au.' || au_table) IS NULL THEN
                CONTINUE;
            END IF;

            IF EXISTS (
                SELECT 1
                FROM information_schema.columns AS cols
                WHERE cols.table_schema = 'trademark_au'
                  AND cols.table_name = au_table
                  AND cols.column_name = 'source_row_hash'
            ) THEN
                CONTINUE;
            END IF;

            EXECUTE format(
                'SELECT EXISTS (SELECT 1 FROM trademark_au.%I LIMIT 1)',
                au_table
            ) INTO has_rows;
            IF has_rows THEN
                RAISE EXCEPTION
                    'legacy trademark_au.% contains rows; automatic schema-shape upgrade is intentionally blocked',
                    au_table;
            END IF;

            EXECUTE format(
                'ALTER TABLE trademark_au.%I ADD COLUMN source_row_hash text',
                au_table
            );

            SELECT c.conname
            INTO pk_name
            FROM pg_constraint AS c
            WHERE c.conrelid = to_regclass('trademark_au.' || au_table)
              AND c.contype = 'p'
            LIMIT 1;

            IF pk_name IS NOT NULL THEN
                EXECUTE format(
                    'ALTER TABLE trademark_au.%I DROP CONSTRAINT %I',
                    au_table,
                    pk_name
                );
            END IF;

            EXECUTE format(
                'ALTER TABLE trademark_au.%I '
                'ALTER COLUMN source_row_hash SET NOT NULL, '
                'ADD PRIMARY KEY (source_row_hash)',
                au_table
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
        relation_name text;
        constraint_name text;
        has_rows boolean;
    BEGIN
        IF to_regclass('trademark_ca.st96_record') IS NULL THEN
            RETURN;
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns AS cols
            WHERE cols.table_schema = 'trademark_ca'
              AND cols.table_name = 'st96_record'
              AND cols.column_name = 'record_key'
        ) THEN
            SELECT EXISTS (SELECT 1 FROM trademark_ca.st96_record LIMIT 1)
            INTO has_rows;
            IF has_rows THEN
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
                IF to_regclass('trademark_ca.' || child_table) IS NULL THEN
                    CONTINUE;
                END IF;
                EXECUTE format(
                    'SELECT EXISTS (SELECT 1 FROM trademark_ca.%I LIMIT 1)',
                    child_table
                ) INTO has_rows;
                IF has_rows THEN
                    RAISE EXCEPTION
                        'legacy trademark_ca.% contains rows; automatic schema-shape upgrade is intentionally blocked',
                        child_table;
                END IF;
            END LOOP;

            FOR relation_name, constraint_name IN
                SELECT c.conrelid::regclass::text, c.conname
                FROM pg_constraint AS c
                WHERE c.confrelid = 'trademark_ca.st96_record'::regclass
                  AND c.contype = 'f'
            LOOP
                EXECUTE format(
                    'ALTER TABLE %s DROP CONSTRAINT %I',
                    relation_name,
                    constraint_name
                );
            END LOOP;

            ALTER TABLE trademark_ca.st96_record ADD COLUMN record_key text;

            SELECT c.conname
            INTO constraint_name
            FROM pg_constraint AS c
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
        END IF;

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

            IF EXISTS (
                SELECT 1
                FROM information_schema.columns AS cols
                WHERE cols.table_schema = 'trademark_ca'
                  AND cols.table_name = child_table
                  AND cols.column_name = 'source_row_hash'
            ) THEN
                CONTINUE;
            END IF;

            EXECUTE format(
                'SELECT EXISTS (SELECT 1 FROM trademark_ca.%I LIMIT 1)',
                child_table
            ) INTO has_rows;
            IF has_rows THEN
                RAISE EXCEPTION
                    'legacy trademark_ca.% contains rows; automatic schema-shape upgrade is intentionally blocked',
                    child_table;
            END IF;

            FOR relation_name, constraint_name IN
                SELECT c.conrelid::regclass::text, c.conname
                FROM pg_constraint AS c
                WHERE c.conrelid = to_regclass('trademark_ca.' || child_table)
                  AND c.confrelid = 'trademark_ca.st96_record'::regclass
                  AND c.contype = 'f'
            LOOP
                EXECUTE format(
                    'ALTER TABLE %s DROP CONSTRAINT %I',
                    relation_name,
                    constraint_name
                );
            END LOOP;

            EXECUTE format(
                'ALTER TABLE trademark_ca.%I '
                'ADD COLUMN source_row_hash text, '
                'ADD COLUMN record_key text',
                child_table
            );

            SELECT c.conname
            INTO constraint_name
            FROM pg_constraint AS c
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

    The first country-store release created AU/CA tables before any ingestion
    command existed. Their record identities were refined when the real parsers
    landed. Empty legacy tables can therefore be upgraded automatically; any
    unexpected legacy rows fail closed rather than being silently re-keyed with
    invented provenance.
    """

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(LEGACY_UPGRADE_SQL)
        conn.commit()
