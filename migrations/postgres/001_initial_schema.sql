-- PostgreSQL 初始业务 schema。
-- 工程 JSON 文件仍存储在文件系统或对象存储中，数据库保存索引、路径、hash 和审计信息。

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id text UNIQUE,
    display_name text NOT NULL,
    email text UNIQUE,
    role text NOT NULL DEFAULT 'engineer',
    is_active boolean NOT NULL DEFAULT true,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT users_role_check CHECK (role IN ('admin', 'engineer', 'viewer', 'system'))
);

CREATE TABLE IF NOT EXISTS template_catalog (
    template_id text PRIMARY KEY,
    project_type text NOT NULL,
    project_type_label text NOT NULL,
    source_path text NOT NULL UNIQUE,
    file_name text NOT NULL,
    json_sha256 char(64),
    node_count integer NOT NULL DEFAULT 0,
    tab_count integer NOT NULL DEFAULT 0,
    features jsonb NOT NULL DEFAULT '{}'::jsonb,
    summary text NOT NULL DEFAULT '',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    indexed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT template_catalog_project_type_check CHECK (project_type IN ('plant_room', 'ahu', 'unknown')),
    CONSTRAINT template_catalog_counts_check CHECK (node_count >= 0 AND tab_count >= 0)
);

CREATE TABLE IF NOT EXISTS projects (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    project_type text NOT NULL,
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    current_version_id uuid,
    status text NOT NULL DEFAULT 'active',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT projects_project_type_check CHECK (project_type IN ('plant_room', 'ahu')),
    CONSTRAINT projects_status_check CHECK (status IN ('active', 'archived', 'deleted'))
);

CREATE TABLE IF NOT EXISTS patch_records (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    base_version_id uuid,
    requested_by uuid REFERENCES users(id) ON DELETE SET NULL,
    request_message text NOT NULL DEFAULT '',
    patch jsonb NOT NULL,
    risk_level text NOT NULL DEFAULT 'low',
    status text NOT NULL DEFAULT 'planned',
    result jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CONSTRAINT patch_records_risk_level_check CHECK (risk_level IN ('low', 'medium', 'high', 'blocked')),
    CONSTRAINT patch_records_status_check CHECK (status IN ('planned', 'dry_run_failed', 'applied', 'failed', 'rejected'))
);

CREATE TABLE IF NOT EXISTS validation_reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version_id uuid,
    valid boolean NOT NULL,
    exportable boolean NOT NULL DEFAULT false,
    error_count integer NOT NULL DEFAULT 0,
    warning_count integer NOT NULL DEFAULT 0,
    risk_count integer NOT NULL DEFAULT 0,
    report jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT validation_reports_counts_check CHECK (error_count >= 0 AND warning_count >= 0 AND risk_count >= 0)
);

CREATE TABLE IF NOT EXISTS project_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    parent_version_id uuid REFERENCES project_versions(id) ON DELETE RESTRICT,
    source_template_id text REFERENCES template_catalog(template_id) ON DELETE SET NULL,
    patch_record_id uuid REFERENCES patch_records(id) ON DELETE SET NULL,
    validation_report_id uuid REFERENCES validation_reports(id) ON DELETE SET NULL,
    version_label text NOT NULL,
    json_sha256 char(64) NOT NULL,
    file_path text NOT NULL,
    file_size_bytes bigint NOT NULL DEFAULT 0,
    exportable boolean NOT NULL DEFAULT false,
    created_by uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT project_versions_hash_check CHECK (json_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT project_versions_file_size_check CHECK (file_size_bytes >= 0),
    CONSTRAINT project_versions_project_label_unique UNIQUE (project_id, version_label)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'projects_current_version_fk'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT projects_current_version_fk
            FOREIGN KEY (current_version_id)
            REFERENCES project_versions(id)
            ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'patch_records_base_version_fk'
    ) THEN
        ALTER TABLE patch_records
            ADD CONSTRAINT patch_records_base_version_fk
            FOREIGN KEY (base_version_id)
            REFERENCES project_versions(id)
            ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'validation_reports_version_fk'
    ) THEN
        ALTER TABLE validation_reports
            ADD CONSTRAINT validation_reports_version_fk
            FOREIGN KEY (version_id)
            REFERENCES project_versions(id)
            ON DELETE SET NULL;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS sessions (
    thread_id text PRIMARY KEY,
    user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    project_id uuid REFERENCES projects(id) ON DELETE SET NULL,
    current_version_id uuid REFERENCES project_versions(id) ON DELETE SET NULL,
    graph_state jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'created',
    next_action text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz
);

CREATE TABLE IF NOT EXISTS audit_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    session_thread_id text REFERENCES sessions(thread_id) ON DELETE SET NULL,
    project_id uuid REFERENCES projects(id) ON DELETE SET NULL,
    version_id uuid REFERENCES project_versions(id) ON DELETE SET NULL,
    patch_record_id uuid REFERENCES patch_records(id) ON DELETE SET NULL,
    event_type text NOT NULL,
    event_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    request_id text,
    ip_address inet,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tab_index (
    id bigserial PRIMARY KEY,
    template_id text NOT NULL REFERENCES template_catalog(template_id) ON DELETE CASCADE,
    source_path text NOT NULL,
    tab_id text NOT NULL,
    tab_label text NOT NULL,
    node_count integer NOT NULL DEFAULT 0,
    node_type_counts jsonb NOT NULL DEFAULT '{}'::jsonb,
    summary text NOT NULL DEFAULT '',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    indexed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT tab_index_template_tab_unique UNIQUE (template_id, tab_id),
    CONSTRAINT tab_index_node_count_check CHECK (node_count >= 0)
);

CREATE TABLE IF NOT EXISTS node_index (
    id bigserial PRIMARY KEY,
    template_id text NOT NULL REFERENCES template_catalog(template_id) ON DELETE CASCADE,
    source_path text NOT NULL,
    node_id text NOT NULL,
    tab_id text,
    tab_label text,
    type text NOT NULL,
    name text NOT NULL DEFAULT '',
    inputs integer,
    outputs integer,
    input_sources jsonb NOT NULL DEFAULT '[]'::jsonb,
    key_params jsonb NOT NULL DEFAULT '{}'::jsonb,
    text_for_search text NOT NULL DEFAULT '',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    indexed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT node_index_template_node_unique UNIQUE (template_id, node_id),
    CONSTRAINT node_index_port_count_check CHECK (
        (inputs IS NULL OR inputs >= 0)
        AND (outputs IS NULL OR outputs >= 0)
    )
);

CREATE TABLE IF NOT EXISTS block_index (
    block_id text PRIMARY KEY,
    template_id text NOT NULL REFERENCES template_catalog(template_id) ON DELETE CASCADE,
    source_path text NOT NULL,
    tab_id text,
    tab_label text,
    anchor_node_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    node_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    entry_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    exit_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    risk_tags jsonb NOT NULL DEFAULT '[]'::jsonb,
    summary text NOT NULL DEFAULT '',
    raw_fragment jsonb NOT NULL DEFAULT '{}'::jsonb,
    text_for_search text NOT NULL DEFAULT '',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    indexed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id bigserial PRIMARY KEY,
    source_path text NOT NULL,
    chunk_index integer NOT NULL,
    title text NOT NULL DEFAULT '',
    content text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    indexed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT knowledge_chunks_source_chunk_unique UNIQUE (source_path, chunk_index),
    CONSTRAINT knowledge_chunks_chunk_index_check CHECK (chunk_index >= 0)
);

CREATE INDEX IF NOT EXISTS idx_users_external_id ON users(external_id);
CREATE INDEX IF NOT EXISTS idx_projects_owner_status ON projects(owner_user_id, status);
CREATE INDEX IF NOT EXISTS idx_project_versions_project_created ON project_versions(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_project_versions_parent ON project_versions(parent_version_id);
CREATE INDEX IF NOT EXISTS idx_patch_records_project_created ON patch_records(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_validation_reports_project_created ON validation_reports(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_project_created ON audit_events(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_type_created ON audit_events(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_template_catalog_project_type ON template_catalog(project_type);
CREATE INDEX IF NOT EXISTS idx_tab_index_template_label ON tab_index(template_id, tab_label);
CREATE INDEX IF NOT EXISTS idx_node_index_template_type ON node_index(template_id, type);
CREATE INDEX IF NOT EXISTS idx_node_index_template_tab ON node_index(template_id, tab_id);
CREATE INDEX IF NOT EXISTS idx_block_index_template_tab ON block_index(template_id, tab_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_source ON knowledge_chunks(source_path);

CREATE INDEX IF NOT EXISTS idx_template_catalog_features_gin ON template_catalog USING gin(features);
CREATE INDEX IF NOT EXISTS idx_node_index_input_sources_gin ON node_index USING gin(input_sources);
CREATE INDEX IF NOT EXISTS idx_node_index_key_params_gin ON node_index USING gin(key_params);
CREATE INDEX IF NOT EXISTS idx_block_index_node_ids_gin ON block_index USING gin(node_ids);

INSERT INTO schema_migrations (version)
VALUES ('001_initial_schema')
ON CONFLICT (version) DO NOTHING;
