# Deadline 前智能体目标实现方案

更新时间：2026-05-06

适用范围：本文用于指导 deadline 前的最后一轮智能体能力改造。目标不是重写已有主流程，而是在当前“模板检索 + 版本创建 + 结构化补丁 + dry-run + 校验 + 前端工作台”的基础上，最快补齐“多轮理解、语义定位、主动检索、减少追问、稳定生成 JSON 补丁”的能力。

## 1. 背景和问题定义

当前从“用户第一次输入需求”到创建工程版本的流程基本合理：

```text
用户第一次输入需求
  -> classify_project_type
  -> collect_requirements
  -> retrieve_template_candidates
  -> select_or_wait_template
  -> create_project_version
```

这一段已经可以完成项目类型识别、需求槽位合并、模板候选、模板确认和创建不可变工程版本。当前真正影响用户体验的是创建工程后的多轮修改阶段。

用户真实表达通常是：

```text
把水泵控制里的比较判断改名为 演示节点
把刚才那个节点的阈值改为 3
在旁通阀控制里新增压差设定并接入比较判断
不要改水泵，改旁通阀那边
把 CO2 设定接到新风阀控制里
```

而不是：

```text
把节点 55554a7 改名为 演示节点
断开节点 3a4c97e 的输入 0
```

从用户视角看，节点 ID 是系统内部实现细节。系统要求用户提供节点 ID，本质上说明后端没有完成“业务意图 -> 当前工程语义定位 -> 内部节点选择器”的工作。

当前主要问题可以归纳为四类：

1. **语义定位弱**：planner 缺少当前版本的可检索工程语义地图，只能依赖用户给节点 ID 或模板索引返回的候选。
2. **多轮记忆弱**：会话中保存了完整 `messages`，但 planner 实际只消费最后一条用户输入，无法稳定理解“刚才那个”“继续把它改成”“不要这个，改那个”。
3. **主动检索弱**：LLM planner 的 `required_context` 目前更像输出说明，系统没有把它变成二次检索和上下文补全循环。
4. **追问策略不符合用户视角**：追问仍偏开发者，例如要求节点 ID、端口和点位字段，而不是给用户可理解的候选对象或业务决策问题。

因此，deadline 前最重要的改造不是扩展更多 patch op，也不是把完整工程 JSON 塞进 prompt，而是补一层：

```text
对话记忆 + 当前工程语义索引 + 语义定位器 + 按需检索循环
```

这会让系统更像一个简易版 Claude Code/Codex：它先理解用户在说什么，再自己检索和定位相关工程片段，最后生成可执行补丁并用 dry-run 验证。

## 2. 当前代码基础判断

### 2.1 已经具备的能力

当前项目已经有较完整的底座：

- `app/graph/workflow.py` 已使用 LangGraph 编排主流程，并通过 `thread_id` 运行会话。
- `app/graph/nodes.py` 已包含模板确认和中高风险补丁确认 interrupt。
- `app/services/requirement_model.py` 已有 `RequirementSlots`，支持多轮覆盖、否定和风险语义。
- `app/services/llm_planner.py` 已支持结构化 LLM planner，输出受 Pydantic schema 约束的 patch plan。
- `app/services/planner_execution.py` 已支持 schema/dry-run/validation 失败反馈重试。
- `app/services/retrieval.py` 已有模板、页面、节点、功能块检索和 `load_node_neighborhood()`。
- `app/services/patch_engine.py` 已支持 `rename_node`、`replace_constant`、`add_tab`、`add_node_from_schema`、`connect`、`disconnect`、`enable_dynamic_input`、`copy_block`、`set_io_point`。
- `app/services/validator.py` 已有结构、schema、动态端口、IO/通讯、保护逻辑和变更风险校验。
- 前端已经有工作台、版本历史、局部图、diff、确认和导出门禁。

这些能力说明项目不需要推翻重做。需要的是把这些能力组织成“智能检索和规划链路”。

### 2.2 当前 planner 上下文缺口

LLM Planner 当前主要看到：

- 最后一条用户输入。
- 当前项目类型。
- 当前模板 ID。
- 当前工程摘要。
- 当前工程页面列表。
- 知识库检索片段。
- 相关页面检索结果。
- 相关节点检索结果。
- 相关功能块的局部 JSON 上下文。
- 如果重试，再追加 schema/dry-run 失败反馈。

它看不到或看不完整：

- 完整多轮对话历史的压缩摘要。
- 最近一次修改影响了哪些节点。
- 用户说“刚才那个”“这个比较判断”“水泵那边”时对应的候选对象。
- 当前工程版本的语义化节点索引。
- 当前工程版本中节点改名、复制、添加后形成的新语义上下文。
- 业务意图到节点/功能块/页面的可解释定位结果。

当前 `search_nodes()` 查的是离线模板索引，不是当前项目版本。创建版本后，如果用户已修改过节点名称、新增节点或复制功能块，模板索引会逐渐失真。

## 3. 设计原则

### 3.1 不改变的边界

以下设计继续保留：

- 仍基于模板创建工程版本，不从零生成完整 JSON。
- LLM 只输出结构化意图和补丁计划，不直接写完整工程 JSON。
- 所有 JSON 修改必须由 patch engine 执行。
- 所有中高风险操作必须 dry-run 并等待用户确认。
- 导出前必须做结构校验；能找到需求上下文时还要做需求覆盖门禁。
- Graph state 只保存轻量摘要和引用，不长期保存完整工程 JSON。

### 3.2 本轮新增的关键能力

本轮新增能力聚焦四个点：

1. **当前工程语义索引**：每次补丁规划时，基于 `current_project_path` 构建或读取当前版本节点语义摘要。
2. **会话工作记忆**：保存最近用户意图、最近影响节点、最近操作摘要、最近候选对象，支持“刚才那个”这类引用。
3. **语义定位器**：在 planner 之前，把用户自然语言定位成页面、功能块、节点候选和置信度。
4. **按需检索式 planner**：先定位和加载上下文，再让 LLM 输出补丁；如上下文不足，先检索再规划，而不是直接追问用户。

### 3.3 LangGraph 使用原则

涉及 LangGraph 的部分遵循官方文档的关键点：

- Persistence 文档说明，使用 checkpointer 编译 graph 后，LangGraph 会按 `thread_id` 保存 graph state checkpoint；`thread_id` 是恢复状态和 interrupt 的关键配置。
- Interrupts 文档说明，`interrupt()` 可暂停图执行并保存状态，之后用同一个 `thread_id` 和 `Command(resume=...)` 恢复。
- Memory 文档强调对话型应用需要在 thread 内保留上下文，同时应通过摘要和消息管理控制上下文预算。
- Graph API 文档中的状态、节点、边和编译模型适合继续保持当前 `StateGraph` 结构。

参考链接：

- https://docs.langchain.com/oss/python/langgraph/persistence
- https://docs.langchain.com/oss/python/langgraph/interrupts
- https://docs.langchain.com/oss/python/langgraph/add-memory
- https://docs.langchain.com/oss/python/langgraph/use-graph-api

本项目的落地含义是：

- 继续使用当前 `thread_id` + checkpointer。
- 新增的定位、检索和 planner 节点应是可重复执行的纯计算或只读检索，避免在 interrupt 前做不可重复副作用。
- 创建版本、应用补丁、写版本元数据等副作用仍放在确认后的节点。
- 对话记忆既保存在 `messages`，也要压缩成结构化字段，供 planner 稳定消费。

## 4. 目标架构

### 4.1 创建版本后的新主链路

当前创建版本后的流程建议改成：

```text
用户继续输入修改需求
  -> merge_conversation_memory
  -> build_current_project_semantic_index
  -> locate_semantic_targets
  -> retrieve_context_for_targets
  -> plan_patch
  -> dry_run_patch
  -> risk_assessment
  -> interrupt_if_needed
  -> apply_patch_create_child_version
  -> validate_current_project
  -> summarize_and_update_memory
```

其中最核心的是 `locate_semantic_targets` 和 `retrieve_context_for_targets`。

### 4.2 数据流

```text
用户输入
  + conversation_summary
  + recent_change_memory
  + requirement_slots
  + current_project_semantic_index
        |
        v
semantic_locator
        |
        v
target_candidates
  + node_neighborhoods
  + block_contexts
  + knowledge_contexts
  + schema_hints
        |
        v
LLM planner / rule planner
        |
        v
structured_patch
        |
        v
dry-run + validation + feedback retry
```

### 4.3 用户视角的行为变化

改造后，系统不再要求用户提供节点 ID。系统应优先自己定位：

| 用户输入 | 系统内部行为 |
| --- | --- |
| 把水泵控制里的比较判断改名为演示节点 | 定位水泵控制页面唯一 compare 节点，生成 `rename_node` |
| 把刚才那个节点阈值改为 3 | 使用 `last_affected_node_ids` 定位最近节点，检查字段，生成 `replace_constant/update_param` |
| 不要改水泵，改旁通阀那边 | 更新语义约束，重新定位旁通阀控制功能块 |
| 新增 CO2 设定接到新风阀控制 | 检索 AHU 控制页、CO2/新风阀相关节点和知识配方，生成新增节点 + 动态输入 + 连线计划 |
| 修改 IO 通道 | 定位目标点位候选；若缺少点位对象和值，追问业务候选，不问内部字段名 |

## 5. 新增模块设计

### 5.1 当前工程语义索引

建议新增文件：

```text
app/services/project_semantic_index.py
```

职责：从当前工程版本 JSON 构建预算受控、可搜索、可给 LLM 使用的语义索引。

输入：

```text
project_path
project_type
template_id
```

输出：

```python
{
    "project_path": "...",
    "summary": {...},
    "tabs": [
        {"id": "...", "label": "水泵控制", "node_count": 457}
    ],
    "nodes": [
        {
            "id": "3a4c97e",
            "type": "compare",
            "tab_id": "73b96a8",
            "tab_label": "水泵控制",
            "name": "比较判断",
            "label": "",
            "semantic_text": "水泵控制 比较判断 compare tripPoint ...",
            "key_params": {"tripPoint": 0, "as": "ge"},
            "inputs": 1,
            "outputs": 1,
            "input_sources": ["..."],
            "upstream_labels": ["..."],
            "downstream_labels": ["..."],
            "schema_path": "schemas/logic/比较判断.json",
            "role_hints": ["threshold_compare", "control_logic"]
        }
    ],
    "by_tab": {...},
    "by_type": {...}
}
```

实现策略：

- 第一版运行时从当前 JSON 现扫即可，避免新增持久索引和迁移。
- 若性能不足，再按 `version_path + json_sha256` 做内存缓存。
- `semantic_text` 应合并 tab_label、type、name、label、info、objectName、topic、关键参数、schema 名称、上游/下游摘要。
- 对新增节点和已改名节点必须反映当前版本状态，不能依赖模板索引。
- 索引只作为 planner 输入，不直接作为工程真实数据来源；最终执行仍由 patch engine 在当前 JSON 上解析 selector。

最小函数：

```python
def build_project_semantic_index(project_path: str) -> dict[str, Any]: ...

def search_current_project_nodes(
    query: str,
    *,
    project_path: str,
    tab_label_contains: str | None = None,
    node_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]: ...

def summarize_current_project_for_planner(
    project_path: str,
    *,
    max_nodes: int = 120,
    max_chars: int = 24000,
) -> dict[str, Any]: ...
```

### 5.2 会话工作记忆

建议扩展 `AgentState`：

```python
conversation_summary: dict[str, Any] | None
recent_user_intents: list[dict[str, Any]]
last_affected_node_ids: list[str]
last_touched_entities: list[dict[str, Any]]
last_patch_summary: dict[str, Any] | None
semantic_target_candidates: list[dict[str, Any]]
```

字段含义：

- `conversation_summary`：压缩的多轮对话摘要，包含用户已确认目标、已否定目标、当前工程约束。
- `recent_user_intents`：最近 3 到 5 次用户修改意图。
- `last_affected_node_ids`：最近一次成功补丁影响的节点 ID，来自 patch result diff。
- `last_touched_entities`：最近触达的页面、功能块、节点的用户可读描述。
- `last_patch_summary`：最近补丁操作、风险、变更摘要。
- `semantic_target_candidates`：最近一次定位候选，用于用户选择“第 1 个/第二个”时恢复上下文。

更新时机：

1. 用户消息进入工作流后，追加 `recent_user_intents`。
2. semantic locator 产生候选后，保存 `semantic_target_candidates`。
3. patch dry-run 成功后，保存候选和 dry-run diff 摘要。
4. patch apply 成功后，更新 `last_affected_node_ids`、`last_touched_entities`、`last_patch_summary`。
5. 用户取消高风险补丁时，不更新 `last_affected_node_ids`，但保留取消记录。

注意：

- `messages` 仍保留，用于追溯和前端展示。
- planner prompt 默认不塞完整 messages，只塞最近几轮和 `conversation_summary`。
- 长期上下文以 `RequirementSlots` 和这些工作记忆字段为主。

### 5.3 语义定位器 semantic_locator

建议新增文件：

```text
app/services/semantic_locator.py
```

职责：把用户自然语言转换成系统内部可用的目标候选。

输入：

```python
{
    "message": "把水泵控制里的比较判断改名为 演示节点",
    "project_path": "...",
    "project_type": "plant_room",
    "template_id": "...",
    "conversation_summary": {...},
    "last_affected_node_ids": [...],
    "last_touched_entities": [...],
    "requirement_slots": {...}
}
```

输出：

```python
{
    "status": "resolved | candidates | needs_clarification",
    "intent": "rename_node | update_param | add_logic | connect | disconnect | set_io_point | unknown",
    "target_queries": ["水泵控制 比较判断"],
    "knowledge_queries": ["水泵控制 比较判断 阈值"],
    "candidates": [
        {
            "kind": "node",
            "confidence": 0.91,
            "selector": {"id": "3a4c97e"},
            "display_name": "水泵控制 / 比较判断 / compare / tripPoint=0",
            "reason": "页面和节点类型唯一匹配",
            "node": {...}
        }
    ],
    "selected": {...} | None,
    "questions": []
}
```

定位规则：

1. 如果用户提到“刚才、上次、这个、它”，优先使用 `last_affected_node_ids` 和 `last_touched_entities`。
2. 如果用户提到页面名，如“水泵控制、旁通阀控制、控制页面、排风机页”，先约束 tab。
3. 如果用户提到节点类型，如“比较判断、常量、PID、软件输入、物理输入、Modbus 输出”，约束 type 或 schema。
4. 如果用户提到业务对象，如“CO2、新风阀、旁通阀压差、水泵台数”，用当前工程语义索引搜索。
5. 如果候选唯一且置信度高，返回 `resolved`。
6. 如果候选少于等于 5 个但不唯一，返回 `candidates`，让前端展示用户可读候选。
7. 如果缺少业务决策，例如“接到哪个输入端”“点位值是什么”，返回 `needs_clarification`。

不要追问：

```text
请提供节点 id。
请提供 target_input。
请提供字段名 hwChannelIndex。
```

改成：

```text
我找到了 3 个比较判断，请确认要改哪一个：
1. 水泵控制 / 比较判断 / 当前阈值 2 / 上游是水泵运行台数
2. 旁通阀控制 / 比较判断 / 当前阈值 45 / 上游是压差信号
3. 联动控制 / 比较判断 / 当前阈值 1 / 上游是运行反馈
```

或：

```text
要把 CO2 设定接入新风阀控制，我需要确认接入位置：
1. 接入 CO2 比较判断的动态阈值输入
2. 接入新风阀开度 PID 的设定输入
3. 只新增设定节点，暂不接线
```

第一版定位器可以主要用规则 + 当前工程搜索，不强依赖 LLM。只有复杂场景再让 LLM 生成 `target_queries`。

### 5.4 按需检索式 planner

当前 `plan_patch_with_llm()` 是一次性构造 prompt 并让 LLM 输出完整 plan。建议改成两段式，但为了 deadline，不必重构成多节点 LangGraph，可以先在服务层封装一个入口：

```python
def plan_patch_with_context_retrieval(
    message: str,
    *,
    project_path: str,
    template_id: str | None,
    project_type: str | None,
    conversation_context: dict[str, Any],
    provider: str | None,
    llm_max_attempts: int,
) -> dict[str, Any]:
    ...
```

内部流程：

```text
1. semantic_locator 定位候选。
2. 对 resolved/candidates 加载节点邻域。
3. 按用户意图检索功能块和知识库。
4. 组合 planner context。
5. 调用 LLM planner 输出 patch。
6. dry-run。
7. 失败反馈重试。
```

上下文结构建议：

```json
{
  "conversation_context": {
    "summary": "...",
    "recent_user_intents": [],
    "last_affected_node_ids": [],
    "last_touched_entities": []
  },
  "current_project": {
    "summary": {},
    "tabs": [],
    "semantic_node_candidates": []
  },
  "target_resolution": {
    "status": "resolved",
    "selected": {},
    "candidates": []
  },
  "node_neighborhoods": [],
  "related_blocks": [],
  "block_contexts": [],
  "knowledge": [],
  "schemas": [],
  "dry_run_feedback": []
}
```

planner prompt 要明确：

- 用户看不见节点 ID；节点 ID 是系统内部定位结果。
- 如果 `target_resolution.selected` 已唯一定位，优先使用其 selector。
- 如果候选不唯一，不要胡乱选择，返回 `needs_clarification`，但问题必须使用候选的用户可读描述。
- 不要要求用户补节点 ID。
- 对 medium/high 风险操作必须标明风险原因。

### 5.5 知识库补丁配方

当前 `knowledge/` 只有 AHU 控制策略、机房群控控制策略、工程规范三份文档。deadline 前不建议建设完整知识平台，但建议新增一份：

```text
knowledge/补丁配方.md
```

内容按“用户意图 -> 推荐检索词 -> 常见 patch 操作组合 -> 风险”组织。

建议至少写这些配方：

1. AHU CO2 浓度设定接入新风阀控制。
2. AHU 送风温度 PID 动态设定。
3. AHU 过滤网报警。
4. AHU 防冻保护保留和禁止旁路。
5. 机房水泵运行台数阈值修改。
6. 机房旁通阀压差设定修改。
7. 机房旁通阀压差设定新增并接入比较判断。
8. PID 输出上限/下限动态输入。
9. 比较判断动态阈值输入。
10. IO/通讯点位修改风险。
11. copy_block 边界接线确认。
12. 删除/断开保护链路的禁止和确认策略。

示例片段：

```markdown
## 旁通阀压差设定接入

适用：机房群控，旁通阀控制页。

用户常见说法：
- 新增旁通阀压差设定
- 把压差设定接入旁通阀比较判断
- 旁通阀压差改成动态设定

推荐定位：
- tab: 旁通阀控制
- node_type: compare, pid, constInput, swInput
- query: 旁通阀 压差 比较判断 设定

推荐操作组合：
1. add_node_from_schema(constInput 或 swInput)
2. enable_dynamic_input(compare)
3. connect(设定节点 -> compare target_input=1)

风险：
- 至少 medium。
- 如果影响保护、联锁或 IO 点位，升为 high。
```

## 6. 工作流改造方案

### 6.1 AgentState 扩展

建议在 `app/graph/state.py` 增加：

```python
conversation_summary: dict[str, Any] | None
recent_user_intents: list[dict[str, Any]]
last_affected_node_ids: list[str]
last_touched_entities: list[dict[str, Any]]
last_patch_summary: dict[str, Any] | None
semantic_target_candidates: list[dict[str, Any]]
```

初始化为空：

```python
"conversation_summary": None,
"recent_user_intents": [],
"last_affected_node_ids": [],
"last_touched_entities": [],
"last_patch_summary": None,
"semantic_target_candidates": [],
```

### 6.2 nodes.py 改造点

#### plan_patch_node

当前逻辑：

```text
规则 planner
  -> 规则 planned 就应用
  -> 复杂意图 fallback LLM
```

建议改为：

```text
构建 conversation_context
  -> semantic_locator 定位目标
  -> 明确低风险请求可先规则 planner
  -> 规则失败或结构意图或语义定位已给出候选时走 context-aware LLM planner
  -> dry-run feedback
```

伪代码：

```python
def plan_patch_node(state):
    project_path = state.get("current_project_path")
    message = _last_user_content(state)
    conversation_context = _planner_conversation_context(state)

    location = locate_semantic_targets(
        message,
        project_path=project_path,
        project_type=state.get("project_type"),
        template_id=state.get("selected_template_id"),
        conversation_context=conversation_context,
    )

    if location["status"] == "candidates" and _needs_user_selection(message, location):
        return _clarify_with_user_readable_candidates(location)

    rule_result = plan_patch_request(..., semantic_location=location)

    if rule_result["status"] == "planned":
        return planned

    if _should_use_context_aware_llm(message, rule_result, state, location):
        return _plan_patch_with_context_aware_llm(...)

    return clarify
```

第一版为了少改函数签名，也可以不改 `plan_patch_request()`，只在 LLM fallback 前先做 semantic locator，并把定位结果传给 LLM planner。

#### apply_pending_patch_node

补丁成功后更新工作记忆：

```python
diff = patch_result.get("diff") or {}
affected = diff.get("affected_node_ids") or []
last_touched_entities = summarize_affected_nodes(current_nodes, affected)
```

更新字段：

```python
"last_affected_node_ids": affected,
"last_touched_entities": last_touched_entities,
"last_patch_summary": {
    "message": _last_user_content(state),
    "operations": ...,
    "diff_summary": diff.get("summary"),
    "risk_level": ...
}
```

#### summarize_result_node

用户可见摘要增加一句定位说明：

```text
已将“水泵控制 / 比较判断 / 当前阈值 2”改名为“演示节点”。
```

不要只说：

```text
补丁已应用，共 1 项变更。
```

### 6.3 llm_planner.py 改造点

#### plan_patch_with_llm 入参扩展

增加可选参数：

```python
conversation_context: dict[str, Any] | None = None
target_resolution: dict[str, Any] | None = None
current_project_context: dict[str, Any] | None = None
```

保留现有接口兼容测试。

#### _build_planner_context

当前查模板索引：

```python
related_nodes = search_nodes(message, template_id=template_id)
related_blocks = search_blocks(message, template_id=template_id)
```

建议新增：

```python
current_project_nodes = search_current_project_nodes(message, project_path=project_path)
target_neighborhoods = load_node_neighborhood(project_path, anchor_node_ids)
```

上下文优先级：

1. `target_resolution.selected`
2. `target_resolution.candidates`
3. 当前工程节点检索。
4. 当前工程节点邻域。
5. 功能块索引和 block context。
6. 知识库。
7. 模板索引。

#### prompt 修改重点

在 `SYSTEM_PROMPT` 增加：

```text
用户无法看到内部节点 ID。节点 ID 只能来自系统提供的 target_resolution、current_project_nodes、node_neighborhoods 或 block_contexts。
禁止要求用户提供节点 ID。
如果候选不唯一，questions 必须引用候选的页面、节点名称、类型、关键参数等用户可读信息。
如果 target_resolution.selected 唯一，应优先使用其 selector。
```

在 user prompt 增加：

```text
多轮对话摘要
最近修改影响节点
语义定位结果
当前工程候选节点
节点邻域
```

### 6.4 planner.py 改造点

规则 planner 可以继续保留，但要做两类改造：

1. 追问文案不再要求节点 ID。
2. 对明确页面 + 类型唯一的情况，允许自动定位。

例如当前：

```text
请说明要修改的节点、页面、参数和值。例如：把节点 3a4c97e 改名为 水泵比较判断。
```

应改为：

```text
请说明目标所在页面、节点名称或业务对象。例如：把水泵控制里的比较判断改名为水泵比较判断。
```

对 ambiguity 返回 `matched_nodes` 时，前端可以展示候选：

```python
"matched_nodes": [
  {"id": "...", "tab_label": "水泵控制", "type": "compare", "name": "比较判断", "key_params": {...}}
]
```

### 6.5 前端改造点

前端不需要大改 UI，只需改显示策略：

1. 如果后端返回 `semantic_target_candidates` 或 `matched_nodes`，展示为“请选择目标对象”。
2. 候选项显示页面、类型、名称、关键参数、上游/下游摘要，不显示或弱化 ID。
3. 用户点击候选后，下一条 message 可以带一个隐藏字段或用自然语言“选择第 1 个”。deadline 下也可以先让用户回复“第 1 个”，后端用 `semantic_target_candidates` 解析。
4. 修改成功后在消息区展示“本次定位目标”和“影响节点”。
5. 局部图自动定位 `last_affected_node_ids`。

## 7. API 和数据契约

### 7.1 public state 新增字段

`_public_state()` 建议公开：

```json
{
  "conversation_summary": {},
  "last_affected_node_ids": [],
  "last_touched_entities": [],
  "last_patch_summary": {},
  "semantic_target_candidates": []
}
```

其中 `semantic_target_candidates` 不必包含完整节点 JSON，只保留：

```json
{
  "candidate_id": "candidate_1",
  "kind": "node",
  "display_name": "水泵控制 / 比较判断 / compare",
  "description": "当前阈值 2，上游为水泵运行台数",
  "confidence": 0.86,
  "selector": {"id": "3a4c97e"}
}
```

### 7.2 MessageRequest 可选扩展

deadline 下可以不改 API，只支持用户回复“第 1 个”。更完整的方式是加：

```python
selected_candidate_id: str | None = None
```

如果加这个字段，前端点击候选时不必生成自然语言。

### 7.3 planner result 扩展

`planner_result` 建议包含：

```json
{
  "target_resolution": {
    "status": "resolved",
    "selected": {...},
    "candidates": []
  },
  "context_used": {
    "node_neighborhood_count": 1,
    "knowledge_count": 3,
    "block_context_count": 1
  }
}
```

这样 trace 和前端都能解释系统为什么这样改。

## 8. 知识库和检索策略

### 8.1 当前不建议上向量库

deadline 前不建议引入 pgvector、Chroma 或重排模型，原因：

- 当前模板只有 5 个，新增向量库的工程成本高于收益。
- 真正缺的是当前工程版本索引和定位链路，而不是静态模板相似度。
- 关键词 + 规则 + 当前工程语义索引 + LLM 二次判断已经足够覆盖演示和验收场景。

### 8.2 检索优先级

补丁规划时按以下优先级检索：

1. 最近影响节点和最近候选。
2. 当前工程语义索引。
3. 当前工程节点邻域。
4. 功能块索引和 block context。
5. 知识库补丁配方。
6. 模板节点/页面索引。

模板索引主要用于：

- 初始模板选择。
- 当前工程中找不到目标时，对照原模板补充候选。
- `copy_block` 从模板来源复制功能块。

当前工程索引主要用于：

- 用户后续修改。
- 已新增/改名节点的定位。
- 最近上下文引用。

### 8.3 上下文预算

LLM planner prompt 推荐预算：

| 内容 | 预算 |
| --- | --- |
| 系统约束和 patch schema | 20% |
| 用户当前输入和多轮摘要 | 15% |
| 语义定位结果 | 15% |
| 当前工程候选节点和邻域 | 25% |
| 功能块/知识库片段 | 15% |
| dry-run 失败反馈 | 10% |

不要把完整工程 JSON 放入 prompt。只有当候选节点邻域不足以规划时，才加载更多局部上下文。

## 9. 追问策略

### 9.1 追问分级

| 情况 | 系统行为 |
| --- | --- |
| 唯一定位，低风险 | 直接 dry-run 并自动应用或按现有策略处理 |
| 唯一定位，中高风险 | dry-run 后进入确认 |
| 2 到 5 个候选 | 展示用户可读候选，让用户选择 |
| 候选过多 | 追问页面、设备、业务对象 |
| 缺少业务决策 | 追问业务选项 |
| 缺少内部字段 | 系统自己根据 schema/知识库推断；不能推断时用中文解释字段含义 |

### 9.2 禁止的追问

不应再出现：

```text
请提供节点 id。
请提供 target_input。
请提供 source_output。
请提供 hwChannelIndex 字段。
```

除非用户已经进入高级调试模式或显式手写补丁。

### 9.3 推荐追问模板

目标不唯一：

```text
我找到了多个可能的目标，请确认要修改哪一个：
1. 水泵控制 / 比较判断 / 当前阈值 2 / 上游是水泵运行台数
2. 联动控制 / 比较判断 / 当前阈值 1 / 上游是运行反馈
```

缺少接线决策：

```text
要把新增设定接入控制逻辑，需要确认接入位置：
1. 接到比较判断的动态阈值输入
2. 接到 PID 的设定输入
3. 只新增设定节点，暂不接线
```

IO 点位不明确：

```text
请确认要修改的是哪个点位：
1. 硬接输入：过滤网报警
2. 硬接输入：防冻开关
3. Modbus 输出：新风阀开度
```

## 10. 实施排期

### 第 1 天：当前工程语义索引 + 工作记忆

目标：让系统知道当前工程里有什么，以及刚刚改过什么。

工作项：

- 新增 `project_semantic_index.py`。
- 实现 `build_project_semantic_index()` 和 `search_current_project_nodes()`。
- `AgentState` 增加工作记忆字段。
- 补丁成功后更新 `last_affected_node_ids`、`last_touched_entities`、`last_patch_summary`。
- `_public_state()` 暴露相关摘要。

验收：

- 创建工程后，搜索“水泵控制 比较判断”能在当前工程返回节点 `3a4c97e`。
- 改名成功后，搜索新名称能命中当前版本，不依赖模板索引。
- 用户第二轮说“刚才那个节点”时，后端能拿到最近 affected node。

### 第 2 天：semantic_locator

目标：把自然语言目标定位成候选节点/页面/功能块。

工作项：

- 新增 `semantic_locator.py`。
- 支持页面、节点类型、业务关键词、最近节点引用定位。
- 支持候选置信度和用户可读描述。
- `plan_patch_node` 中先调用 locator，再调用 planner。
- 追问文案改成用户可读候选。

验收：

- “把水泵控制里的比较判断改名为 演示节点”不要求节点 ID。
- “把刚才那个节点阈值改为 3”能定位最近节点。
- “把比较判断改名为 xxx”如果多候选，返回候选列表。

### 第 3 天：context-aware LLM planner

目标：让 LLM planner 使用语义定位结果和当前工程上下文。

工作项：

- 扩展 `plan_patch_with_llm()` 入参，兼容现有测试。
- `_build_planner_context()` 加入 current project candidates、target_resolution、node_neighborhoods、conversation_context。
- 修改 prompt，禁止要求节点 ID。
- `planner_execution.py` 保持 dry-run feedback 重试。

验收：

- 新增 CO2 设定接入新风阀控制可生成 add_node + enable_dynamic_input/connect 计划或合理追问。
- 旁通阀压差设定可定位旁通阀控制页和相关比较判断。
- dry-run 失败时，反馈中包含定位候选和失败原因，第二次重试更容易修正 selector。

### 第 4 天：知识库补丁配方 + 复杂样例校准

目标：补业务知识，减少 LLM 猜测。

工作项：

- 新增 `knowledge/补丁配方.md`。
- 增强 `search_knowledge()` 查询词，使用 locator 输出的 `knowledge_queries`。
- 针对 AHU CO2、PID 动态设定、旁通阀压差、水泵阈值、IO 点位修改做样例校准。

验收：

- LLM prompt 中能检索到对应配方。
- 典型中风险组合补丁 dry-run 通过率提升。

### 第 5 天：前端候选选择 + 局部图定位

目标：用户看得懂系统在问什么、改了什么。

工作项：

- 前端显示 semantic candidates。
- 支持点击候选或回复“第 1 个”。
- 修改成功后展示 last touched entities。
- 局部图自动聚焦 `last_affected_node_ids`。

验收：

- 目标不唯一时，前端展示候选列表，不展示裸 node id。
- 补丁成功后，图上能定位被改节点。

### 第 6 天：评测和回归

目标：用测试证明能力，而不是只靠演示。

新增评测用例：

- 无节点 ID 改名。
- 无节点 ID 改阈值。
- 刚才那个节点继续修改。
- 多候选要求选择。
- 选择第 1 个后继续执行。
- CO2 新增设定接线。
- 旁通阀压差设定。
- IO 点位修改高风险确认。

验收命令：

```powershell
conda activate midea
python scripts\build_indexes.py
pytest -q
cd frontend
npm run lint
npm run test
npm run build
```

### 第 7 天：演示固化和 bug fix

只做三件事：

- 固化演示脚本。
- 修复评测和 E2E 暴露的问题。
- 不再新增大能力。

## 11. 测试计划

### 11.1 后端单元测试

新增：

```text
tests/test_project_semantic_index.py
tests/test_semantic_locator.py
```

覆盖：

- 当前工程索引能识别 tab、节点、key params、上下游。
- 改名后索引返回当前版本名称。
- locator 支持页面 + 类型定位。
- locator 支持“刚才那个”定位。
- locator 多候选时返回用户可读候选。

### 11.2 工作流测试

在 `tests/test_workflow_api.py` 增加：

```text
test_workflow_renames_node_by_semantic_description
test_workflow_updates_recent_node_by_reference
test_workflow_returns_user_readable_candidates_when_target_ambiguous
test_workflow_uses_candidate_selection_for_followup
test_workflow_context_aware_llm_planner_does_not_ask_for_node_id
```

### 11.3 评测样例

新增或扩展：

```text
evals/semantic_location_cases.jsonl
evals/patch_planning_cases.jsonl
evals/business_acceptance_cases.jsonl
```

样例：

```json
{"case_id":"plant_rename_pump_compare_no_id","message":"把水泵控制里的比较判断改名为 演示节点","expected_selector":{"id":"3a4c97e"},"expected_op":"rename_node"}
{"case_id":"plant_followup_recent_node","messages":["把水泵控制里的比较判断改名为 演示节点","把刚才那个节点的阈值改为 3"],"expected_reference":"last_affected_node_ids"}
{"case_id":"plant_ambiguous_compare_candidates","message":"把比较判断改名为 演示节点","expected_status":"candidates"}
```

### 11.4 前端 E2E

覆盖：

- 创建会话。
- 选模板。
- 输入无 ID 修改需求。
- 查看候选或直接应用。
- 确认中风险。
- 查看局部图高亮。
- 导出前校验。

## 12. 风险和控制措施

### 12.1 语义定位误改节点

风险：系统自动选择了错误节点。

控制：

- 只有高置信度唯一候选才自动规划。
- 中风险或影响结构的修改即使定位唯一，也 dry-run 后确认。
- 候选置信度不够时展示候选，让用户选。
- 变更摘要必须显示用户可读目标。

### 12.2 LLM 根据不完整上下文瞎编

风险：LLM 输出不存在节点、字段或端口。

控制：

- Pydantic schema 校验。
- patch engine selector 唯一性校验。
- dry-run 和 validation feedback 重试。
- prompt 明确只能使用系统提供的 selector。
- 不允许 LLM 直接输出完整工程 JSON。

### 12.3 当前工程索引性能问题

风险：每轮扫描 3000 节点导致延迟。

控制：

- 第一版先接受现扫，因为节点规模可控。
- 后续按 `project_path + mtime/json_sha256` 内存缓存。
- prompt 只传 top candidates，不传全量节点。

### 12.4 多轮记忆错误引用

风险：“刚才那个”引用到错误节点。

控制：

- 只在最近一次补丁成功且 affected 节点数量少时自动解析。
- 如果最近影响多个节点，展示候选列表。
- 用户取消补丁不更新 `last_affected_node_ids`。

### 12.5 追问仍过多

风险：系统仍然频繁 asking。

控制：

- 缺内部信息时先检索，不立刻追问。
- 追问只问业务决策或候选选择。
- 追问必须附带系统已找到的信息。

## 13. 最终交付标准

deadline 前完成以下标准即可视为目标达成：

1. 用户不需要知道节点 ID，也能完成典型节点改名、阈值修改、常量替换。
2. 用户说“刚才那个节点”“继续改它”时，系统能利用最近变更记忆定位。
3. 用户模糊描述目标时，系统给出用户可读候选，而不是要求节点 ID。
4. LLM planner 能看到多轮摘要、当前工程语义候选、节点邻域、知识库配方和 dry-run 反馈。
5. 复杂结构改造能自动按需检索知识库和局部工程上下文。
6. 中高风险操作仍保持 dry-run 和人工确认。
7. 每次修改后前端能展示本次定位目标、影响节点和局部图高亮。
8. 测试覆盖无 ID 修改、最近引用、多候选选择、复杂新增设定、IO 高风险确认。

一句话交付口径：

> Deadline 版本不是从零生成任意 JSON 的大模型，而是一个面向 AHU 和机房群控模板工程的受控智能改造助手：它能记住多轮对话，自己检索当前工程，按业务语义定位节点和功能块，生成结构化补丁，通过 dry-run 和校验保证 JSON 安全，并用候选确认和局部图高亮减少用户反复追问。

## 14. 后续非 deadline 能力

以下能力建议延期：

- 向量数据库和重排模型。
- 完整点表导入/导出编辑器。
- 大规模复合功能块自动扩容。
- 完整 KONG 画布编辑器。
- 多用户权限和租户隔离。
- 生产级审计检索和运维后台。
- 自动修复校验问题的闭环 agent。

这些能力有价值，但不如当前工程语义索引、语义定位和工作记忆对 deadline 目标直接。
