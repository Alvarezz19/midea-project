from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.services.json_project import get_tabs, load_project, summarize_project
from app.services.knowledge import search_knowledge
from app.services.llm_gateway import LLMGatewayError, chat_json
from app.services.retrieval import RetrievalError, load_block_context, search_blocks, search_nodes, search_tabs


RiskLevel = Literal["low", "medium", "high"]
PlannerStatus = Literal["planned", "needs_clarification"]
Intent = Literal["modify_existing_logic", "add_logic", "annotate", "unknown"]
PatchOp = Literal[
    "update_param",
    "replace_constant",
    "enable_dynamic_input",
    "set_io_point",
    "rename_node",
    "add_tab",
    "add_comment",
    "add_node_from_schema",
    "copy_block",
    "connect",
    "disconnect",
]


class LLMPlannerError(ValueError):
    """LLM 补丁规划失败。"""


class RequiredContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tab", "node", "block", "knowledge"] = "block"
    query: str = ""
    reason: str = ""


class PlannedOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: PatchOp
    node_selector: dict[str, Any] | None = None
    label: str | None = None
    new_name: str | None = None
    params: dict[str, Any] | None = None
    field: str | None = None
    value: Any = None
    input_option: str | None = None
    input_options: list[str] | None = None
    tab_selector: dict[str, Any] | None = None
    text: str | None = None
    info: str | None = None
    schema_selector: dict[str, Any] | None = None
    module_type: str | None = None
    block_id: str | None = None
    source_block_id: str | None = None
    target_tab_selector: dict[str, Any] | None = None
    x_offset: int | None = None
    y_offset: int | None = None
    offset: dict[str, int] | None = None
    name_prefix: str | None = None
    boundary_connections: list[dict[str, Any]] | None = None
    source_node_selector: dict[str, Any] | None = None
    target_node_selector: dict[str, Any] | None = None
    source_output: int | None = None
    target_input: int | None = None
    x: int | None = None
    y: int | None = None
    position: dict[str, int] | None = None
    disabled: bool | None = None
    allow_duplicate_label: bool | None = None

    @model_validator(mode="after")
    def validate_operation_contract(self) -> PlannedOperation:
        if self.op == "update_param":
            _require_dict(self.node_selector, "update_param.node_selector")
            _require_dict(self.params, "update_param.params")
        elif self.op == "replace_constant":
            _require_dict(self.node_selector, "replace_constant.node_selector")
            if self.value is None:
                raise ValueError("replace_constant.value 不能为空。")
            if self.field is not None and not _non_empty_string(self.field):
                raise ValueError("replace_constant.field 必须是非空字符串。")
        elif self.op == "enable_dynamic_input":
            _require_dict(self.node_selector, "enable_dynamic_input.node_selector")
            if self.input_options is not None:
                if not self.input_options or any(not _non_empty_string(option) for option in self.input_options):
                    raise ValueError("enable_dynamic_input.input_options 必须是非空字符串数组。")
            if self.input_option is not None and not _non_empty_string(self.input_option):
                raise ValueError("enable_dynamic_input.input_option 必须是非空字符串。")
        elif self.op == "set_io_point":
            _require_dict(self.node_selector, "set_io_point.node_selector")
            _require_dict(self.params, "set_io_point.params")
        elif self.op == "rename_node":
            _require_dict(self.node_selector, "rename_node.node_selector")
            if not _non_empty_string(self.new_name):
                raise ValueError("rename_node.new_name 不能为空。")
        elif self.op == "add_tab":
            if not _non_empty_string(self.label):
                raise ValueError("add_tab.label 不能为空。")
            if self.disabled is not None and not isinstance(self.disabled, bool):
                raise ValueError("add_tab.disabled 必须是布尔值。")
            if self.allow_duplicate_label is not None and not isinstance(self.allow_duplicate_label, bool):
                raise ValueError("add_tab.allow_duplicate_label 必须是布尔值。")
        elif self.op == "add_comment":
            _require_dict(self.tab_selector, "add_comment.tab_selector")
            if not _non_empty_string(self.text):
                raise ValueError("add_comment.text 不能为空。")
        elif self.op == "add_node_from_schema":
            if not isinstance(self.schema_selector, dict) and not _non_empty_string(self.module_type):
                raise ValueError("add_node_from_schema 需要 schema_selector 或 module_type。")
            _require_dict(self.tab_selector, "add_node_from_schema.tab_selector")
        elif self.op == "copy_block":
            if not _non_empty_string(self.block_id) and not _non_empty_string(self.source_block_id):
                raise ValueError("copy_block 需要 block_id。")
            _require_dict(self.target_tab_selector, "copy_block.target_tab_selector")
            if self.x_offset is not None:
                _require_int(self.x_offset, "copy_block.x_offset")
            if self.y_offset is not None:
                _require_int(self.y_offset, "copy_block.y_offset")
            if self.offset is not None:
                if not isinstance(self.offset, dict):
                    raise ValueError("copy_block.offset 必须是对象。")
                if "x" in self.offset:
                    _require_int(self.offset.get("x"), "copy_block.offset.x")
                if "y" in self.offset:
                    _require_int(self.offset.get("y"), "copy_block.offset.y")
            if self.name_prefix is not None and not isinstance(self.name_prefix, str):
                raise ValueError("copy_block.name_prefix 必须是字符串。")
            if self.boundary_connections is not None:
                if not isinstance(self.boundary_connections, list):
                    raise ValueError("copy_block.boundary_connections 必须是数组。")
                for index, connection in enumerate(self.boundary_connections):
                    if not isinstance(connection, dict):
                        raise ValueError(f"copy_block.boundary_connections[{index}] 必须是对象。")
                    role = connection.get("role")
                    if role == "entry":
                        _require_dict(connection.get("source_node_selector"), f"copy_block.boundary_connections[{index}].source_node_selector")
                        _require_non_negative_int(connection.get("source_output"), f"copy_block.boundary_connections[{index}].source_output")
                        if not _non_empty_string(connection.get("target_copied_from_id")):
                            raise ValueError(f"copy_block.boundary_connections[{index}].target_copied_from_id 不能为空。")
                        _require_non_negative_int(connection.get("target_input"), f"copy_block.boundary_connections[{index}].target_input")
                    elif role == "exit":
                        if not _non_empty_string(connection.get("source_copied_from_id")):
                            raise ValueError(f"copy_block.boundary_connections[{index}].source_copied_from_id 不能为空。")
                        _require_non_negative_int(connection.get("source_output"), f"copy_block.boundary_connections[{index}].source_output")
                        _require_dict(connection.get("target_node_selector"), f"copy_block.boundary_connections[{index}].target_node_selector")
                        _require_non_negative_int(connection.get("target_input"), f"copy_block.boundary_connections[{index}].target_input")
                    else:
                        raise ValueError(f"copy_block.boundary_connections[{index}].role 必须是 entry 或 exit。")
        elif self.op == "connect":
            _require_dict(self.source_node_selector, "connect.source_node_selector")
            _require_dict(self.target_node_selector, "connect.target_node_selector")
            _require_non_negative_int(self.source_output, "connect.source_output")
            _require_non_negative_int(self.target_input, "connect.target_input")
        elif self.op == "disconnect":
            _require_dict(self.target_node_selector, "disconnect.target_node_selector")
            if self.source_output is not None:
                _require_non_negative_int(self.source_output, "disconnect.source_output")
            if self.target_input is not None:
                _require_non_negative_int(self.target_input, "disconnect.target_input")
        return self


class StructuredPatchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: PlannerStatus = "needs_clarification"
    intent: Intent = "unknown"
    summary: str = ""
    risk_level: RiskLevel = "low"
    risk_reasons: list[str] = Field(default_factory=list)
    required_context: list[RequiredContext] = Field(default_factory=list)
    operations: list[PlannedOperation] = Field(default_factory=list)
    validation_expectations: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_plan_contract(self) -> StructuredPatchPlan:
        if self.status == "planned" and not self.operations:
            raise ValueError("planned 状态必须包含至少一个操作。")
        if self.status == "needs_clarification" and not self.questions:
            raise ValueError("needs_clarification 状态必须包含追问问题。")
        return self


SYSTEM_PROMPT = """你是楼宇自控工程 JSON 智能体的结构化补丁规划器。
你只能输出 JSON 对象，不要输出 markdown。你不能输出完整工程 JSON，不能要求直接写文件。
你的任务是把用户修改意图转成结构化补丁计划；后续会由确定性 patch engine dry-run、校验并保存。

输出 JSON 必须符合：
{
  "status": "planned | needs_clarification",
  "intent": "modify_existing_logic | add_logic | annotate | unknown",
  "summary": "一句话中文摘要",
  "risk_level": "low | medium | high",
  "risk_reasons": ["风险原因"],
  "required_context": [{"type": "block", "query": "水泵控制", "reason": "用于定位"}],
    "operations": [
    {
      "op": "replace_constant",
      "node_selector": {"id": "3a4c97e"},
      "field": "tripPoint",
      "value": 3
    }
  ],
  "validation_expectations": ["目标节点唯一", "dry-run 校验通过"],
  "questions": []
}

允许的 op 只有：
1. update_param：需要 node_selector 和 params，只能修改已存在参数。
2. replace_constant：需要 node_selector 和 value；可选 field。只用于替换常量、软件输入设定值、比较阈值或数学模块固定值，不用于结构字段。
3. enable_dynamic_input：需要 node_selector；PID、线性变换等需要 input_option 或 input_options；只打开动态输入端口并维护 inputs、inputsOption/inputAuxEnable、wires，不自动连线。
4. set_io_point：需要 node_selector 和 params；只允许修改已有 IO/通讯点位白名单字段，例如 hwExpander、hwChannelIndex、modbusPort、modbusAddress、functionCode、modbusRegAddr、bacnetObjectType、bacnetObjectInstance、bacnetIpDeviceInstance、bacnetIpObjectType、bacnetIpObjectInstance、topic、objectName。
5. rename_node：需要 node_selector 和 new_name。
6. add_tab：需要 label；用于新增页面/tab 节点，可选 info 和 disabled。只新增页面，不自动新增功能节点或连线。除非用户明确要求允许重复页面名，否则不要设置 allow_duplicate_label。
7. add_comment：需要 tab_selector 和 text。
8. add_node_from_schema：需要 tab_selector，且需要 module_type 或 schema_selector；可提供 params、x、y。
9. copy_block：需要 block_id 和 target_tab_selector；可提供 x_offset、y_offset、name_prefix；只有用户基于边界预览明确给出 boundary_connections 时，才可按 entry/exit 显式接线。默认只复制功能块内部节点和内部连线，必须丢弃所有外部入口/出口连线，不自动接入外部线。
10. connect：需要 source_node_selector、target_node_selector、source_output、target_input。wires 表示目标输入端的上游源。
11. disconnect：需要 target_node_selector；可选 source_node_selector、source_output、target_input。

选择器规则：
1. 已知节点优先使用 {"id": "..."}。
2. 新增节点后要连线时，connect 可用新增节点的稳定名称和 type 作为 source_node_selector，例如 {"type": "constInput", "name": "CO2设定值"}。
3. 禁止只用 {"type": "compare"} 这类明显不唯一的选择器。
4. 无法唯一定位节点、页面、端口或参数时，status 必须是 needs_clarification，并提出具体追问。
5. 页面标签是 type="tab" 节点的 label 字段；用户要求把页面标签中的 A 改成 B 时，应先在 tabs 上唯一定位包含 A 的页面，再输出 update_param，node_selector 使用该 tab id，params 只修改 {"label": "替换后的完整页面标签"}。不要把页面标签写入 name 字段，也不要新增备注或新增节点。

组合计划规则：
1. 如果要把新增常量、设定值或传感器信号接入 compare/limit 的动态阈值端口，必须先 add_node_from_schema，再 enable_dynamic_input，最后 connect 到 target_input=1。
2. 如果要把新增设定值接入 PID 参数动态端口，必须先 add_node_from_schema，再 enable_dynamic_input 并指定 input_option，最后 connect 到新增动态端口。
3. 不要用 connect 隐式创建端口；端口数量变化必须显式表达为 enable_dynamic_input。
4. copy_block 默认只能表达“复制局部功能块并暂不接外部线”；如果用户要求复制后接入现有 IO、保护、设备或通讯链路，必须先基于边界预览追问入口/出口和确认方式；只有节点、端口和方向都明确时，才可输出 boundary_connections。
5. set_io_point 只表达明确节点上的点位字段变更；如果没有明确节点、字段和值，必须追问。

风险规则：
1. rename_node、update_param、replace_constant、add_comment 通常是 low。
2. add_tab 只新增空页面时通常是 low；如果同时复制功能块、新增节点或接线，按后续操作升级风险。
3. enable_dynamic_input、add_node_from_schema、connect、disconnect 至少是 medium。
4. copy_block 至少是 high。
5. set_io_point 至少是 high，必须人工确认。
6. 删除、断线、修改设备数量、影响保护逻辑必须是 high；遇到没有对应 op 的需求应追问或说明需要人工确认。
"""


def plan_patch_with_llm(
    message: str,
    *,
    project_path: str,
    template_id: str | None = None,
    project_type: str | None = None,
    provider: str | None = None,
    max_block_contexts: int = 2,
    max_attempts: int = 2,
    feedback_messages: list[str] | None = None,
) -> dict[str, Any]:
    """使用 LLM 生成结构化补丁计划，但不执行工程修改。"""

    if not message.strip():
        raise LLMPlannerError("修改需求不能为空。")
    if max_attempts < 1:
        raise LLMPlannerError("max_attempts 必须大于 0。")

    context = _build_planner_context(
        message,
        project_path=project_path,
        template_id=template_id,
        project_type=project_type,
        max_block_contexts=max_block_contexts,
    )
    feedback_history = list(feedback_messages or [])
    schema_errors: list[str] = []
    for attempt_index in range(max_attempts):
        try:
            raw_result = chat_json(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _build_user_prompt(message, context, feedback_history)},
                ],
                provider=provider,
            )
        except LLMGatewayError as exc:
            raise LLMPlannerError(str(exc)) from exc

        meta = raw_result.pop("_llm_meta", None)
        if isinstance(meta, dict):
            meta.setdefault("prompt_name", "llm_planner")
        try:
            plan = StructuredPatchPlan.model_validate(raw_result)
            break
        except ValidationError as exc:
            error_text = f"第 {attempt_index + 1} 次 LLM 输出未通过 schema 校验: {_truncate(str(exc))}"
            schema_errors.append(error_text)
            feedback_history.append(
                "上一次输出未通过 schema 校验。请只使用允许的 op，补齐必需字段；"
                f"错误摘要：{_truncate(str(exc), limit=900)}"
            )
    else:
        raise LLMPlannerError("LLM 补丁规划结果不符合 schema: " + " | ".join(schema_errors))

    data = plan.model_dump(exclude_none=True)
    data["planner"] = "llm"
    data["llm_meta"] = meta
    data["context"] = context
    data["pending_patch"] = {"operations": data["operations"]} if plan.status == "planned" else None
    data["planner_attempt_count"] = attempt_index + 1
    if feedback_history:
        data["feedback_messages"] = feedback_history
    return data


def _build_planner_context(
    message: str,
    *,
    project_path: str,
    template_id: str | None,
    project_type: str | None,
    max_block_contexts: int,
) -> dict[str, Any]:
    nodes = load_project(project_path)
    blocks = (
        search_blocks(message, template_id=template_id, project_type=project_type, limit=max_block_contexts)
        if template_id
        else []
    )
    block_contexts: list[dict[str, Any]] = []
    for block in blocks:
        block_id = block.get("block_id")
        if not isinstance(block_id, str):
            continue
        try:
            block_contexts.append(load_block_context(block_id, max_nodes=24, max_chars=9000))
        except RetrievalError:
            continue

    return {
        "project_type": project_type,
        "template_id": template_id,
        "project_summary": summarize_project(nodes),
        "tabs": [{"id": tab_id, "label": label} for tab_id, label in get_tabs(nodes).items()],
        "knowledge": search_knowledge(message, limit=3),
        "related_tabs": search_tabs(message, template_id=template_id, limit=3) if template_id else [],
        "related_nodes": search_nodes(message, template_id=template_id, limit=10) if template_id else [],
        "related_blocks": blocks,
        "block_contexts": block_contexts,
    }


def _build_user_prompt(message: str, context: dict[str, Any], feedback_messages: list[str] | None = None) -> str:
    prompt = (
        "请基于以下用户需求和局部上下文输出结构化补丁计划。\n"
        "如果上下文不足以唯一定位目标，返回 needs_clarification。\n\n"
        f"用户需求：{message}\n\n"
        "局部上下文 JSON：\n"
        f"{json.dumps(_compact_context_for_prompt(context), ensure_ascii=False, separators=(',', ':'))}"
    )
    if feedback_messages:
        prompt += (
            "\n\n上一次规划失败反馈：\n"
            + "\n".join(f"- {item}" for item in feedback_messages[-5:])
            + "\n请修正后重新输出完整 JSON。"
        )
    return prompt


def _compact_context_for_prompt(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_type": context.get("project_type"),
        "template_id": context.get("template_id"),
        "project_summary": context.get("project_summary"),
        "tabs": context.get("tabs"),
        "knowledge": context.get("knowledge"),
        "related_tabs": context.get("related_tabs"),
        "related_nodes": context.get("related_nodes"),
        "block_contexts": context.get("block_contexts"),
    }


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_dict(value: Any, field: str) -> None:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{field} 必须是非空对象。")


def _require_non_negative_int(value: Any, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} 必须是非负整数。")


def _require_int(value: Any, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} 必须是整数。")


def _truncate(value: str, limit: int = 500) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."
