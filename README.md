# 美的工程 JSON 智能体原型

本项目是面向美的工程 JSON 的智能体原型，用于从既有工程模板中检索合适基底，创建工程版本，并通过结构化补丁对 JSON 做可追踪、可校验的局部改造。

当前定位不是“让大模型直接重写完整工程 JSON”，而是：

1. 用索引和检索选择合适模板。
2. 用 LangGraph 编排会话状态和流程。
3. 用确定性工具读取、修改、校验 JSON。
4. 让 planner 生成低风险结构化补丁。
5. 通过 FastAPI 和前端工作台完成真实交互验证。

## 当前能力

已完成第一阶段可运行原型，并推进到局部子图改造的第一步：

- 扫描 `programs/` 下模板并生成索引。
- 检索机房群控程序和 AHU 程序模板。
- 创建工程版本副本，不覆盖原始模板。
- 每次补丁应用成功后创建新的子版本，不覆盖父版本。
- 支持将当前项目指针回滚到已有版本，保留完整历史版本。
- 支持按版本生成节点级 diff 摘要，用于前端预览历史版本差异。
- 导出前会重新校验工程，存在 error 级问题时拒绝导出。
- 支持生成功能块索引并检索水泵、旁通阀、排风机、直膨机等局部功能块。
- 支持按 `block_id` 加载局部 JSON 上下文，返回节点摘要、内部边、边界边和预算信息。
- 新增 LLM Gateway，默认接入 DeepSeek API，并预留 OpenAI、Azure OpenAI、Anthropic 配置入口。
- 支持使用 LLM 做结构化需求抽取，返回项目类型、设备、控制功能、通讯、保护逻辑、缺失字段和澄清问题。
- 支持显式开启 LLM 结构化补丁规划，并立即进入 dry-run；LLM 只输出受 schema 约束的补丁意图，实际修改仍由 patch engine 执行。
- 新增 LLM planner 评测样例，覆盖低/中风险补丁和目标不唯一追问。
- LLM planner 支持失败反馈重试：schema 校验失败、dry-run 执行失败或校验失败时可把错误反馈给模型重新规划。
- 支持自然语言规划低风险修改：
  - 节点改名。
  - 修改已有节点参数。
  - 添加备注节点。
- 支持结构化补丁操作：
  - `update_param`
  - `rename_node`
  - `add_comment`
  - `add_node_from_schema`
  - `connect`
  - `disconnect`
- 支持 schema 驱动新增节点。
- 支持补丁 dry-run，返回校验报告和节点级 diff 摘要，不保存文件。
- 支持基础工程校验：
  - JSON 根节点类型。
  - 节点 id 和 type。
  - id 重复。
  - tab/subflow 引用。
  - wires 上游源引用。
  - inputs 与 wires 基础一致性。
  - 导出阻塞原因。
- 提供 FastAPI API。
- 提供 FastAPI 托管的前端工作台。
- 应用启动时复用已编译的 LangGraph 工作流。
- 会话状态已通过文件型 session store 持久化到本地目录，后续可替换为 PostgreSQL。
- 已有 pytest 验收覆盖服务层、工作流和 API。

## 尚未完成

以下能力仍在后续计划中：

- `copy_block`
- `enable_dynamic_input`
- planner 自动组合“新增节点 + 连线”的多步补丁。
- 全工程 schema 参数校验。
- 局部流程图展示。
- 更完整的 JSON diff 可视化。
- 更完整的前端交互体验。
- 向量库/混合检索。
- PostgreSQL checkpointer、权限、审计、回滚等生产化能力。

## 目录结构

```text
app/
  main.py                 FastAPI 入口和 API
  graph/                  LangGraph 工作流
  services/               确定性工具层
  static/                 前端工作台静态文件

scripts/
  build_indexes.py        离线索引构建脚本

indexes/
  templates/              模板级索引
  tabs/                   页面级索引
  blocks/                 功能块级索引
  nodes/                  节点级索引

knowledge/                控制策略和工程规范知识文档
programs/                 原始工程模板
schemas/                  模块 schema 描述
projects/                 运行时工程版本输出
tests/                    自动化测试
evals/                    LLM planner 等评测样例
```

## 环境要求

项目在 Windows + Conda 环境下开发。新的终端会话中，先激活环境：

```powershell
conda activate midea
```

主要 Python 依赖包括：

- FastAPI
- Uvicorn
- LangGraph
- Pydantic
- Pytest

依赖范围已记录在 `pyproject.toml`。如果当前环境缺少依赖，可按实际环境安装对应包：

```powershell
conda activate midea
python -m pip install -e ".[dev]"
```

## 构建索引

模板索引用于模板推荐、tab 检索和节点检索。修改 `programs/` 中模板后应重新生成索引。

```powershell
conda activate midea
python scripts\build_indexes.py
```

当前期望输出规模：

```text
模板数量: 5
页面数量: 31
节点数量: 8413
功能块数量: 129
```

## 启动后端和工作台

```powershell
conda activate midea
uvicorn app.main:app --reload
```

启动后打开：

```text
http://127.0.0.1:8000/
```

工作台支持：

- 新建会话。
- 输入项目需求。
- 展示并确认模板候选。
- 创建工程版本。
- 规划补丁。
- 手动应用结构化补丁。
- 补丁成功后切换到新的工程版本。
- 查看校验结果。
- 导出最终 JSON。

## 常用 API

```text
GET  /api/health
POST /api/sessions
POST /api/sessions/{thread_id}/message
POST /api/templates/search
POST /api/blocks/search
POST /api/blocks/context
POST /api/knowledge/search
POST /api/requirements/extract
POST /api/planner/plan
POST /api/planner/dry-run
GET  /api/projects/{project_id}/versions
GET  /api/projects/{project_id}/diff
POST /api/projects/{project_id}/validate
POST /api/projects/{project_id}/rollback
GET  /api/projects/{project_id}/export
```

LLM 结构化补丁规划默认关闭，调用时显式传入 `use_llm: true`。示例：

```json
{
  "project_path": "projects/versions/demo/v1.json",
  "message": "在水泵控制里新增一个常量并接到比较判断",
  "template_id": "plant_room_efb00c114dcb",
  "project_type": "plant_room",
  "use_llm": true
}
```

## 结构化补丁示例

修改参数：

```json
{
  "op": "update_param",
  "node_selector": {"id": "67febfa"},
  "params": {"fixedValue": "5"}
}
```

节点改名：

```json
{
  "op": "rename_node",
  "node_selector": {"id": "3a4c97e"},
  "new_name": "水泵比较判断"
}
```

从 schema 新增节点：

```json
{
  "op": "add_node_from_schema",
  "module_type": "constInput",
  "tab_selector": {"label": "水泵控制"},
  "params": {
    "user_defined_name": "新增常量",
    "fixedValue": 9
  },
  "x": 220,
  "y": 260
}
```

显式连线：

```json
{
  "op": "connect",
  "source_node_selector": {"id": "source_id"},
  "source_output": 0,
  "target_node_selector": {"id": "target_id"},
  "target_input": 1
}
```

断开连线：

```json
{
  "op": "disconnect",
  "source_node_selector": {"id": "source_id"},
  "target_node_selector": {"id": "target_id"},
  "target_input": 1
}
```

注意：当前工程 JSON 中的 `wires` 统一按 `input_sources` 语义理解，表示“当前节点每个输入端连接的上游源”。因此 `connect` 会修改目标节点的 `wires[target_input]`，节点索引中也使用 `input_sources` 字段。

## 运行测试

```powershell
conda activate midea
pytest -q
```

当前验收结果：

```text
47 passed, 2 skipped
```

真实 LLM 验收需要本地 `.env` 配置 `DEEPSEEK_API_KEY`，并显式开启：

```powershell
conda activate midea
$env:RUN_LLM_INTEGRATION='1'
pytest tests\test_llm_gateway.py::test_requirement_extractor_real_deepseek -q -s
```

LLM planner 真实结构化规划验收：

```powershell
conda activate midea
$env:RUN_LLM_INTEGRATION='1'
pytest tests\test_planner_evals.py::test_llm_planner_real_deepseek_returns_structured_dry_runnable_plan -q -s
```

建议完整验收命令：

```powershell
conda activate midea
python scripts\build_indexes.py
pytest -q
```

## 运行时文件

- `projects/versions/`：默认工程版本输出目录。
  - `{version_id}.json`：不可变工程版本。
  - `{version_id}.meta.json`：版本来源、父版本、hash、补丁摘要和校验摘要。
- `projects/sessions/`：默认会话状态输出目录。
- `projects/exports/`：预留导出目录。
- `.env`：本地环境变量，不应提交。
- `uvicorn-workbench.log`：本地运行日志，可按需删除。

测试中通常使用临时目录，避免污染真实项目版本目录。

## 相关文档

- [智能体系统架构设计.md](智能体系统架构设计.md)
- [schemas/模块特点总结.md](schemas/模块特点总结.md)

## 下一步建议

优先用当前工作台跑真实样例，记录交互问题，再决定下一步：

1. 增强前端工作台：补丁摘要、校验问题列表、JSON diff、模板确认体验。
2. 增强后端局部子图能力：`copy_block`、`enable_dynamic_input`。
3. 补齐 LLM planner 高风险确认和更多真实评测样例。
