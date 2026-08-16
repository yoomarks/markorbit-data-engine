CREATE TABLE IF NOT EXISTS control.cn_publish_checkpoint
(
    package_id UUID PRIMARY KEY
        REFERENCES control.source_package(package_id) ON DELETE CASCADE,
    checkpoint_version TEXT NOT NULL,
    stage_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS control.cn_publish_subtask
(
    package_id UUID NOT NULL
        REFERENCES control.source_package(package_id) ON DELETE CASCADE,
    checkpoint_version TEXT NOT NULL,
    task_key CHAR(64) NOT NULL,
    task_group TEXT NOT NULL,
    task_index INTEGER NOT NULL,
    task_total INTEGER NOT NULL,
    stage_table TEXT NOT NULL,
    range_lower TEXT,
    range_upper TEXT,
    sql_hash CHAR(64) NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    attempts INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (package_id, checkpoint_version, task_key)
);

CREATE INDEX IF NOT EXISTS idx_cn_publish_subtask_status
ON control.cn_publish_subtask(package_id, checkpoint_version, status);
