CREATE SCHEMA IF NOT EXISTS visual;
CREATE SCHEMA IF NOT EXISTS acquisition;

CREATE TABLE IF NOT EXISTS visual.canonical_asset (
    canonical_asset_id uuid PRIMARY KEY,
    sha256 char(64) NOT NULL UNIQUE,
    mime_type text NOT NULL,
    file_extension text NOT NULL,
    byte_size bigint NOT NULL CHECK (byte_size >= 0),
    width integer NOT NULL CHECK (width > 0),
    height integer NOT NULL CHECK (height > 0),
    storage_key text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (sha256 ~ '^[0-9a-fA-F]{64}$')
);

CREATE TABLE IF NOT EXISTS visual.asset_derivative (
    source_asset_id uuid NOT NULL REFERENCES visual.asset(asset_id) ON DELETE RESTRICT,
    canonical_asset_id uuid NOT NULL REFERENCES visual.canonical_asset(canonical_asset_id) ON DELETE RESTRICT,
    derivative_kind text NOT NULL DEFAULT 'CANONICAL',
    transform_version text NOT NULL,
    normalized_pixel_sha256 char(64) NOT NULL,
    transformed boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_asset_id, derivative_kind, transform_version),
    CHECK (normalized_pixel_sha256 ~ '^[0-9a-fA-F]{64}$')
);

CREATE INDEX IF NOT EXISTS ix_visual_asset_derivative_canonical
ON visual.asset_derivative (canonical_asset_id);

CREATE TABLE IF NOT EXISTS acquisition.cn_mark_image_package (
    package_id uuid PRIMARY KEY,
    source_package_sha256 char(64) NOT NULL UNIQUE,
    source_package_name text NOT NULL,
    package_kind text NOT NULL,
    source_rank bigint NOT NULL CHECK (source_rank >= 0),
    state text NOT NULL,
    compressed_bytes bigint NOT NULL DEFAULT 0 CHECK (compressed_bytes >= 0),
    zip_entry_count bigint NOT NULL DEFAULT 0 CHECK (zip_entry_count >= 0),
    jpeg_entry_count bigint NOT NULL DEFAULT 0 CHECK (jpeg_entry_count >= 0),
    processed_jpeg_count bigint NOT NULL DEFAULT 0 CHECK (processed_jpeg_count >= 0),
    mapped_application_count bigint NOT NULL DEFAULT 0 CHECK (mapped_application_count >= 0),
    unmapped_subject_count bigint NOT NULL DEFAULT 0 CHECK (unmapped_subject_count >= 0),
    new_raw_asset_count bigint NOT NULL DEFAULT 0 CHECK (new_raw_asset_count >= 0),
    reused_raw_asset_count bigint NOT NULL DEFAULT 0 CHECK (reused_raw_asset_count >= 0),
    new_canonical_asset_count bigint NOT NULL DEFAULT 0 CHECK (new_canonical_asset_count >= 0),
    reused_canonical_asset_count bigint NOT NULL DEFAULT 0 CHECK (reused_canonical_asset_count >= 0),
    unique_raw_asset_count bigint NOT NULL DEFAULT 0 CHECK (unique_raw_asset_count >= 0),
    unique_canonical_asset_count bigint NOT NULL DEFAULT 0 CHECK (unique_canonical_asset_count >= 0),
    started_at timestamptz NOT NULL DEFAULT now(),
    accepted_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    last_error text,
    CHECK (source_package_sha256 ~ '^[0-9a-fA-F]{64}$'),
    CHECK (package_kind IN ('HISTORICAL', 'UPDATE')),
    CHECK (state IN ('PROCESSING', 'ACCEPTED', 'FAILED'))
);

CREATE INDEX IF NOT EXISTS ix_cn_mark_image_package_state
ON acquisition.cn_mark_image_package (state, source_rank, source_package_name);

CREATE TABLE IF NOT EXISTS visual.cn_mark_image_observation (
    package_id uuid NOT NULL REFERENCES acquisition.cn_mark_image_package(package_id) ON DELETE RESTRICT,
    source_entry_path text NOT NULL,
    source_subject_key text NOT NULL,
    application_number text,
    raw_asset_id uuid NOT NULL REFERENCES visual.asset(asset_id) ON DELETE RESTRICT,
    canonical_asset_id uuid NOT NULL REFERENCES visual.canonical_asset(canonical_asset_id) ON DELETE RESTRICT,
    observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (package_id, source_entry_path)
);

CREATE INDEX IF NOT EXISTS ix_cn_mark_image_observation_application
ON visual.cn_mark_image_observation (application_number)
WHERE application_number IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_cn_mark_image_observation_raw_asset
ON visual.cn_mark_image_observation (raw_asset_id);

CREATE TABLE IF NOT EXISTS visual.cn_trademark_visual_version (
    application_number text NOT NULL,
    raw_asset_id uuid NOT NULL REFERENCES visual.asset(asset_id) ON DELETE RESTRICT,
    canonical_asset_id uuid NOT NULL REFERENCES visual.canonical_asset(canonical_asset_id) ON DELETE RESTRICT,
    first_package_id uuid NOT NULL REFERENCES acquisition.cn_mark_image_package(package_id) ON DELETE RESTRICT,
    last_package_id uuid NOT NULL REFERENCES acquisition.cn_mark_image_package(package_id) ON DELETE RESTRICT,
    first_source_rank bigint NOT NULL CHECK (first_source_rank >= 0),
    last_source_rank bigint NOT NULL CHECK (last_source_rank >= 0),
    first_observed_at timestamptz NOT NULL DEFAULT now(),
    last_observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (application_number, raw_asset_id)
);

CREATE INDEX IF NOT EXISTS ix_cn_trademark_visual_version_asset
ON visual.cn_trademark_visual_version (canonical_asset_id);

CREATE TABLE IF NOT EXISTS visual.cn_trademark_visual_current (
    application_number text PRIMARY KEY,
    raw_asset_id uuid NOT NULL REFERENCES visual.asset(asset_id) ON DELETE RESTRICT,
    canonical_asset_id uuid NOT NULL REFERENCES visual.canonical_asset(canonical_asset_id) ON DELETE RESTRICT,
    source_package_id uuid NOT NULL REFERENCES acquisition.cn_mark_image_package(package_id) ON DELETE RESTRICT,
    source_rank bigint NOT NULL CHECK (source_rank >= 0),
    first_observed_at timestamptz NOT NULL DEFAULT now(),
    last_observed_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_cn_trademark_visual_current_canonical
ON visual.cn_trademark_visual_current (canonical_asset_id);
