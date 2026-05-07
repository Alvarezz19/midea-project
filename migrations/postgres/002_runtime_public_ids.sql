-- 为运行时 PostgreSQL 存储补充外部稳定 ID。
-- API 继续使用 project_... 和 v_... 字符串；数据库内部主键仍保持 UUID。

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS public_project_id text;

UPDATE projects
SET public_project_id = id::text
WHERE public_project_id IS NULL;

ALTER TABLE projects
    ALTER COLUMN public_project_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_public_project_id
    ON projects(public_project_id);

INSERT INTO schema_migrations (version)
VALUES ('002_runtime_public_ids')
ON CONFLICT (version) DO NOTHING;
