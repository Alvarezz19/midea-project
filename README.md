# 美的工程 JSON 智能体原型

本项目用于对美的工程 JSON 做受控改造：先从既有模板中检索合适基底，再通过 LangGraph 编排需求理解、模板确认、局部语义定位、结构化补丁、dry-run 校验、风险确认、版本落盘和导出门禁。

核心原则是：大模型只生成受 schema 约束的补丁意图，实际 JSON 读写、修改、校验和版本管理都由确定性代码完成。

## 当前重点

- 面向 `机房群控程序` 和 `AHU 程序` 的模板检索与工程版本创建。
- 支持多轮需求槽位、设计摘要、模板确认、需求覆盖校验和导出阻塞。
- 支持自然语言定位当前工程节点，规划低风险修改和中高风险结构化补丁。
- 中高风险操作先 dry-run 并等待人工确认，不直接落盘。
- React 工作台提供需求、模板、计划、局部图、diff、版本、回滚、Trace、事件流和反馈视图。
- 默认使用文件型运行时存储，可切换 PostgreSQL；工程 JSON 文件本体仍保存在版本目录。

更完整的模块职责和能力边界见 [文档/项目结构.md](文档/项目结构.md)。

## 快速开始

项目要求：

- Windows PowerShell
- Conda 或 Miniconda
- Python 3.12 及以上
- Node.js 和 npm
- Docker，仅 PostgreSQL 集成验收需要

创建并激活环境。这里用 `midea-json-agent` 作为示例环境名：

```powershell
conda create -n midea-json-agent python=3.12 -y
conda activate midea-json-agent
```

安装 Python 和前端依赖：

```powershell
python -m pip install -r requirements.txt
cd frontend
npm install
cd ..
```

构建索引和前端工作台：

```powershell
python scripts\build_indexes.py
cd frontend
npm run build
cd ..
```

启动服务：

```powershell
uvicorn app.main:app --reload
```

打开：

```text
http://127.0.0.1:8000/
```

旧静态原型保留在：

```text
http://127.0.0.1:8000/legacy
```

## 常用命令

后端测试：

```powershell
pytest -q
```

前端检查：

```powershell
cd frontend
npm run lint
npm run test
npm run build
npm run e2e
cd ..
```

重新生成索引：

```powershell
python scripts\build_indexes.py
```

如果新的 PowerShell 窗口尚未激活环境，先执行：

```powershell
conda activate midea-json-agent
```

## 目录结构

```text
app/                    FastAPI、LangGraph 工作流、服务层、静态工作台托管
frontend/               React/TypeScript 工作台源码
programs/               原始工程 JSON 模板库，不应被运行时覆盖
indexes/                模板、页面、功能块、节点检索索引
schemas/                模块 schema 和节点生成模板
knowledge/              控制策略、工程规范和补丁配方知识库
rules/                  机器可读工程规则
projects/               运行时版本、会话、事件、trace 和反馈输出
migrations/postgres/    PostgreSQL 迁移脚本
scripts/                离线脚本
tests/                  pytest 自动化测试
evals/                  评测样例
文档/                   结构、演进、演示和生产化方案
```

## 主要 API

```text
GET  /api/health
POST /api/sessions
POST /api/sessions/{thread_id}/message
POST /api/sessions/{thread_id}/patch-confirmation
GET  /api/sessions/{thread_id}/events

POST /api/templates/search
POST /api/blocks/search
POST /api/blocks/context
POST /api/knowledge/search
POST /api/requirements/extract
POST /api/planner/plan
POST /api/planner/dry-run

GET  /api/projects/{project_id}/versions
GET  /api/projects/{project_id}/diff
GET  /api/projects/{project_id}/versions/{version_id}/flow
POST /api/projects/{project_id}/validate
POST /api/projects/{project_id}/rollback
GET  /api/projects/{project_id}/export

GET  /api/traces/{trace_id}
GET  /api/observability/costs
GET  /api/observability/trends
POST /api/feedback
GET  /metrics
```

## 配置

项目会自动读取根目录 `.env`，可从 [.env.example](.env.example) 复制后修改。常用配置：

```text
PROJECT_VERSIONS_DIR=projects/versions
PROJECT_SESSIONS_DIR=projects/sessions
RUNTIME_STORE_BACKEND=file
LANGGRAPH_CHECKPOINTER_BACKEND=memory

LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-v4-flash
```

真实 LLM 测试默认跳过，需要配置 `DEEPSEEK_API_KEY` 并显式设置：

```powershell
$env:RUN_LLM_INTEGRATION='1'
```

PostgreSQL 运行时存储需要安装可选依赖并配置 `DATABASE_URL`：

```powershell
python -m pip install -e ".[postgres]"
```

## 运行时产物

```text
projects/
  sessions/       文件型会话状态
  events/         文件型 SSE/workflow 事件 JSONL
  traces/         文件型 trace 详情 JSON
  feedback/       文件型用户反馈 JSONL
  versions/       不可变工程版本 JSON 和元数据
```

测试通常使用临时目录，避免污染真实运行时版本。

## 相关文档

- [文档/项目结构.md](文档/项目结构.md)
- [使用说明指南.md](使用说明指南.md)
- [文档/演示脚本.md](文档/演示脚本.md)
- [智能体系统架构设计.md](智能体系统架构设计.md)
- [文档/阶段7前后端契约.md](文档/阶段7前后端契约.md)
- [文档/生产级智能体系统改进方案.md](文档/生产级智能体系统改进方案.md)
