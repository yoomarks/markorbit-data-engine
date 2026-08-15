CREATE TABLE IF NOT EXISTS control.cn_package_stage_checkpoint (
    package_id uuid PRIMARY KEY REFERENCES control.source_package(package_id) ON DELETE CASCADE,
    checkpoint_version text NOT NULL,
    source_sha256 char(64) NOT NULL,
    snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    staged_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_cn_package_stage_checkpoint_updated
ON control.cn_package_stage_checkpoint(updated_at DESC);
