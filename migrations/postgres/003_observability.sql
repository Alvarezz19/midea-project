-- 阶段 7 可观测性 schema。
-- 公开 API 使用 trace_...、project_...、v_... 等稳定字符串 ID，避免前端暴露数据库 UUID。

CREATE TABLE IF NOT EXISTS agent_traces (
    trace_id text PRIMARY KEY,
    thread_id text NOT NULL,
    project_id text,
    version_id text,
    root_input text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'running',
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    error text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT agent_traces_status_check CHECK (status IN ('running', 'completed', 'failed', 'cancelled'))
);

CREATE TABLE IF NOT EXISTS workflow_events (
    event_id text PRIMARY KEY,
    trace_id text NOT NULL REFERENCES agent_traces(trace_id) ON DELETE CASCADE,
    thread_id text NOT NULL,
    project_id text,
    version_id text,
    event_type text NOT NULL,
    step text NOT NULL,
    status text NOT NULL,
    message text NOT NULL DEFAULT '',
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS llm_call_records (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id text NOT NULL REFERENCES agent_traces(trace_id) ON DELETE CASCADE,
    provider text NOT NULL,
    model text NOT NULL,
    prompt_name text NOT NULL,
    attempt integer NOT NULL DEFAULT 1,
    latency_ms integer NOT NULL DEFAULT 0,
    input_tokens integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    estimated_cost numeric(12, 6) NOT NULL DEFAULT 0,
    status text NOT NULL DEFAULT 'completed',
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT llm_call_records_attempt_check CHECK (attempt >= 1),
    CONSTRAINT llm_call_records_latency_check CHECK (latency_ms >= 0),
    CONSTRAINT llm_call_records_tokens_check CHECK (input_tokens >= 0 AND output_tokens >= 0),
    CONSTRAINT llm_call_records_status_check CHECK (status IN ('completed', 'failed'))
);

CREATE TABLE IF NOT EXISTS user_feedback (
    feedback_id text PRIMARY KEY,
    trace_id text NOT NULL REFERENCES agent_traces(trace_id) ON DELETE CASCADE,
    project_id text,
    version_id text,
    rating integer NOT NULL,
    category text NOT NULL,
    comment text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT user_feedback_rating_check CHECK (rating BETWEEN 1 AND 5)
);

CREATE INDEX IF NOT EXISTS idx_agent_traces_thread_started ON agent_traces(thread_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_traces_project_started ON agent_traces(project_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_events_thread_created ON workflow_events(thread_id, created_at, event_id);
CREATE INDEX IF NOT EXISTS idx_workflow_events_trace_created ON workflow_events(trace_id, created_at, event_id);
CREATE INDEX IF NOT EXISTS idx_llm_call_records_trace ON llm_call_records(trace_id);
CREATE INDEX IF NOT EXISTS idx_user_feedback_project_created ON user_feedback(project_id, created_at DESC);

INSERT INTO schema_migrations (version)
VALUES ('003_observability')
ON CONFLICT (version) DO NOTHING;
