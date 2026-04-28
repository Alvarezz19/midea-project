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
- 节点级索引已显式关联模块 schema，包含 `schema_path`、`schema_category`、`schema_name`、`schema_module_type` 和 `schema_parameter_fields`，便于检索、规划和后续数据库化同步。
- 支持按 `block_id` 加载局部 JSON 上下文，返回节点摘要、内部边、边界边和预算信息。
- 工作流已接入确定性结构化需求摘要，能在模板选择前识别项目类型、设备、控制功能、通讯/IO、保护逻辑和待澄清问题。
- 模板候选会展示匹配项、缺失项、预计改造成本和风险点，便于工程师确认模板基底。
- 模板选择评测已扩充到 AHU 和机房群控各 10 个样例，并在测试中统计 Top-1/Top-3 命中。
- 新增 LLM Gateway，默认接入 DeepSeek API，并预留 OpenAI、Azure OpenAI、Anthropic 配置入口。
- 支持使用 LLM 做结构化需求抽取，返回项目类型、设备、控制功能、通讯、保护逻辑、缺失字段和澄清问题。
- 支持显式开启 LLM 结构化补丁规划，并立即进入 dry-run；LLM 只输出受 schema 约束的补丁意图，实际修改仍由 patch engine 执行。
- 新增 LLM planner 评测样例，覆盖低/中风险补丁、组合补丁和目标不唯一追问。
- LLM planner 支持失败反馈重试：schema 校验失败、dry-run 执行失败或校验失败时可把错误反馈给模型重新规划。
- LangGraph 自然语言补丁规划节点可显式开启 LLM planner，并复用同一套重试、dry-run、校验和错误反馈机制；只有 low 风险计划自动应用，中高风险计划会先停在确认状态，确认后才创建新版本。
- 手动结构化补丁中的 `add_node_from_schema`、`connect`、`disconnect`、`set_io_point`、疑似 IO/通讯字段修改等中高风险操作会先 dry-run 并等待确认，不会直接落盘。
- `enable_dynamic_input` 会被识别为中风险结构变更，确认前只 dry-run，不创建新版本。
- `copy_block` 第一版会被识别为高风险结构变更，只复制功能块内部节点和内部连线，重写节点 id，丢弃外部入口/出口连线，并禁用复制节点上的本地 BACnet 暴露对象，确认前只 dry-run。
- `set_io_point` 会被识别为高风险点位变更，只允许修改已有 IO/通讯/BACnet/MQTT 白名单字段，并复用 dry-run 校验发现点位冲突。
- 支持自然语言规划低风险修改：
  - 节点改名。
  - 替换常量、软件输入设定值或静态阈值。
  - 修改已有节点参数。
  - 添加备注节点。
- 支持结构化启用动态输入端口，并在确认后同步维护 `inputs`、`inputsOption`/`inputAuxEnable` 和 `wires`。
- 支持结构化补丁操作：
  - `update_param`
  - `replace_constant`
  - `enable_dynamic_input`
  - `set_io_point`
  - `rename_node`
  - `add_comment`
  - `add_node_from_schema`
  - `copy_block`
  - `connect`
  - `disconnect`
- 支持 schema 驱动新增节点。
- 支持补丁 dry-run，返回校验报告、变更风险校验和节点级 diff 摘要，不保存文件。
- 支持基础工程校验：
  - JSON 根节点类型。
  - 节点 id 和 type。
  - id 重复。
  - tab/subflow 引用。
  - wires 上游源引用。
  - inputs 与 wires 基础一致性。
  - schema 参数合法性，包括字段类型、枚举、范围、必填参数和未知字段。
  - 动态端口一致性，包括 `inputAuxEnable`、`inputsOption`、`inputD`、`outputD`、`inputsCount` 和 `wires`。
  - IO/通讯冲突，包括硬件 IO 通道、Modbus 点位、本地 BACnet 对象、BACnet/IP 远端点位和 MQTT `objectName`。
  - 机器可读保护逻辑规则库，包括关键执行器输出缺少上游联锁、常量直连旁路、读写方向明显错误、保护/故障/反馈/报警信号被停用、设备相关逻辑缺少故障/运行/反馈线索、设备级保护规则，以及防冻等保护信号的上游链路检查。
  - 校验报告包含 `summary`、问题 `suggestion` 和导出阻塞原因。
  - 补丁 dry-run 会合并 L6 变更风险校验，拦截保护节点删除、关键执行器删除和保护链路断线。
- 提供 FastAPI API。
- 提供 FastAPI 托管的前端工作台。
- 应用启动时复用已编译的 LangGraph 工作流。
- 会话状态默认通过文件型 session store 持久化到本地目录，也可切换到 PostgreSQL 运行时存储。
- 支持 PostgreSQL 运行时业务存储：session、project/version 元数据、patch、validation、export、audit 可写入 PostgreSQL，工程 JSON 文件本体仍保存在版本目录。
- 支持 LangGraph PostgreSQL checkpointer 配置，并已完成 Docker PostgreSQL 本地集成验收。
- 模板确认和中高风险补丁确认已接入 LangGraph 原生 interrupt/resume；API 仍保持现有确认入口。
- 已有 pytest 验收覆盖服务层、工作流和 API。

## 尚未完成

以下能力仍在后续计划中：

- planner 自动组合“新增节点 + 连线”的多步补丁。
- 局部流程图展示。
- 更完整的 JSON diff 可视化。
- 更完整的前端交互体验。
- 向量库/混合检索。
- PostgreSQL 权限模型、运维查询和更完整生产化配套。

## 目录结构

```text
app/
  main.py                 FastAPI 入口、API 和静态工作台托管
  core/                   环境变量配置与日志初始化
  graph/                  LangGraph 状态、节点、workflow、checkpointer
  services/               检索、补丁、校验、运行时存储等服务层
  static/                 前端工作台静态文件

scripts/
  build_indexes.py        离线索引构建脚本

indexes/
  templates/              模板级索引
  tabs/                   页面级索引
  blocks/                 功能块级索引
  nodes/                  节点级索引，显式包含 schema_* 关联字段

knowledge/                控制策略和工程规范知识文档
rules/                    机器可读工程规则
migrations/               PostgreSQL 迁移脚本
programs/                 原始工程模板
schemas/                  模块 schema 描述
projects/                 运行时工程版本与会话输出目录
文档/                     项目结构、演进记录、生产化方案等维护文档
tests/                    自动化测试
evals/                    模板选择、LLM planner 等评测样例
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
- 查看结构化需求清单和待澄清问题。
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
POST /api/sessions/{thread_id}/patch-confirmation
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

工作流消息接口如需启用 LLM planner，传入 `use_llm_planner: true`；也可在创建会话时设置为会话默认值：

```json
{
  "message": "在水泵控制里新增一个常量并接到比较判断",
  "use_llm_planner": true,
  "llm_max_attempts": 2
}
```

当会话状态返回 `next_action: "confirm_patch"` 时，说明补丁已 dry-run 通过但需要人工确认。确认或取消：

```json
{
  "action": "approve"
}
```

或：

```json
{
  "action": "cancel"
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

替换常量或设定值：

```json
{
  "op": "replace_constant",
  "node_selector": {"id": "67febfa"},
  "field": "fixedValue",
  "value": "6"
}
```

启用动态输入端口：

```json
{
  "op": "enable_dynamic_input",
  "node_selector": {"id": "f22a5df"}
}
```

PID 等带选项的动态输入需要指定选项：

```json
{
  "op": "enable_dynamic_input",
  "node_selector": {"id": "a2f6cc2"},
  "input_option": "highPidOutLimit"
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

复制功能块：

```json
{
  "op": "copy_block",
  "block_id": "block_3f996bfafceb",
  "target_tab_selector": {"label": "旁通阀控制"},
  "x_offset": 80,
  "y_offset": 80,
  "name_prefix": "复制-"
}
```

注意：`copy_block` 默认只复制功能块内部节点和内部连线，会重写所有复制节点 id，丢弃所有外部入口/出口连线，并禁用复制节点上的本地 BACnet 暴露对象，避免复用原对象号。dry-run 的 `changes[0].boundary_preview` 会返回入口/出口节点映射、外部边界端口和可用的 `boundary_connections` 草案；只有人工确认节点、端口和方向后，才应把草案显式写入补丁：

```json
{
  "op": "copy_block",
  "block_id": "block_e05eca9c6b44",
  "target_tab_selector": {"label": "控制"},
  "x_offset": 50,
  "y_offset": 70,
  "name_prefix": "复制-",
  "boundary_connections": [
    {
      "role": "entry",
      "source_node_selector": {"id": "af00ef2"},
      "source_output": 0,
      "target_copied_from_id": "a66dadc",
      "target_input": 0
    }
  ]
}
```

设置 IO/通讯点位：

```json
{
  "op": "set_io_point",
  "node_selector": {"id": "22a27d1"},
  "params": {
    "hwExpander": "1",
    "hwChannelIndex": "31"
  }
}
```

`set_io_point` 当前支持已有节点上的白名单字段：硬件 IO 的 `hwExpander`、`hwChannelIndex`；Modbus 的 `modbusPort`、`modbusTcpIPaddr`、`modbusTcpPort`、`modbusAddress`、`functionCode`、`modbusRegAddr`；本地 BACnet 暴露对象字段；BACnet/IP 的设备号、对象类型、对象实例和优先级；MQTT 的 `topic`、`objectName`。该操作必须人工确认后应用，dry-run 会复用 IO/通讯冲突校验。

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
96 passed, 4 skipped
```

当前测试文件包括：

- `tests/test_services.py`：服务层、检索、补丁、校验和规则回归。
- `tests/test_workflow_api.py`：LangGraph 工作流和 FastAPI API 端到端回归。
- `tests/test_template_selection_evals.py`：模板选择与澄清评测样例。
- `tests/test_planner_evals.py`：LLM planner 评测样例和真实 DeepSeek 结构化规划验收。
- `tests/test_llm_gateway.py`：LLM Gateway、需求抽取和 planner schema/dry-run 反馈重试。
- `tests/test_checkpointing.py`：LangGraph PostgreSQL checkpointer 配置边界。
- `tests/test_session_store.py`：文件型会话存储边界。
- `tests/test_migrations.py`：PostgreSQL 迁移脚本结构约束。
- `tests/test_postgres_integration.py`：Docker PostgreSQL 真实集成验收，默认跳过。

PostgreSQL 集成验收可使用 Docker 本地实例，覆盖业务 schema、运行时存储和 LangGraph checkpointer：

```powershell
conda activate midea
python -m pip install -e ".[postgres]"
docker run -d --name midea-postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=midea_agent -p 5432:5432 postgres:16-alpine
$env:DATABASE_URL='postgresql://postgres:postgres@localhost:5432/midea_agent?sslmode=disable'
Get-Content -Raw -Encoding UTF8 'migrations/postgres/001_initial_schema.sql' | docker exec -i midea-postgres psql -U postgres -d midea_agent -v ON_ERROR_STOP=1
Get-Content -Raw -Encoding UTF8 'migrations/postgres/002_runtime_public_ids.sql' | docker exec -i midea-postgres psql -U postgres -d midea_agent -v ON_ERROR_STOP=1
$env:RUNTIME_STORE_BACKEND='postgres'
$env:LANGGRAPH_CHECKPOINTER_BACKEND='postgres'
$env:LANGGRAPH_CHECKPOINTER_SETUP='true'
$env:RUN_POSTGRES_INTEGRATION='1'
pytest tests\test_postgres_integration.py -q
```

当前 PostgreSQL 集成验收结果：

```text
4 passed
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
- `projects/sessions/`：`RUNTIME_STORE_BACKEND=file` 时的默认会话状态输出目录。
- `projects/exports/`：预留导出目录。
- `.env`：本地环境变量，不应提交。
- `uvicorn-workbench.log`：本地运行日志，可按需删除。

切换到 `RUNTIME_STORE_BACKEND=postgres` 后，session、project/version 元数据、patch、validation、export、audit 会写入 PostgreSQL，工程 JSON 文件本体仍保存在版本目录。测试中通常使用临时目录，避免污染真实项目版本目录。

## 相关文档

- [智能体系统架构设计.md](智能体系统架构设计.md)
- [schemas/模块特点总结.md](schemas/模块特点总结.md)

## 下一步建议

优先用当前工作台跑真实样例，记录交互问题，再决定下一步：

1. 增强前端工作台：补丁摘要、校验问题列表、JSON diff、模板确认体验。
2. 增强前端对 `copy_block` 边界预览和 `boundary_connections` 草案的可视化确认。
3. 继续按真实项目样例细化通讯读写方向、更多设备保护链路和业务例外白名单。
