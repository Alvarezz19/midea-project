from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.services.json_project import get_tabs, load_project, summarize_project
from app.services.knowledge import search_knowledge
from app.services.llm_gateway import LLMGatewayError, chat_json
from app.services.project_semantic_index import search_current_project_nodes, summarize_current_project_for_planner
from app.services.retrieval import RetrievalError, load_block_context, load_node_neighborhood, search_blocks, search_nodes, search_tabs


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
   如果用户只说“启用/开启/打开动态输入/动态阈值输入”，只能输出 enable_dynamic_input；不要擅自新增设定节点、软件输入、常量或 connect。
4. set_io_point：需要 node_selector 和 params；只允许修改已有 IO/通讯点位白名单字段，例如 hwExpander、hwChannelIndex、modbusPort、modbusAddress、functionCode、modbusRegAddr、bacnetObjectType、bacnetObjectInstance、bacnetIpDeviceInstance、bacnetIpObjectType、bacnetIpObjectInstance、topic、objectName。
5. rename_node：需要 node_selector 和 new_name。
6. add_tab：需要 label；用于新增页面/tab 节点，可选 info 和 disabled。只新增页面，不自动新增功能节点或连线。除非用户明确要求允许重复页面名，否则不要设置 allow_duplicate_label。
7. add_comment：需要 tab_selector 和 text。
8. add_node_from_schema：需要 tab_selector，且需要 module_type 或 schema_selector；可提供 params、x、y。
9. copy_block：需要 block_id 和 target_tab_selector；可提供 x_offset、y_offset、name_prefix；只有用户基于边界预览明确给出 boundary_connections 时，才可按 entry/exit 显式接线。默认只复制功能块内部节点和内部连线，必须丢弃所有外部入口/出口连线，不自动接入外部线。
10. connect：需要 source_node_selector、target_node_selector、source_output、target_input。wires 表示目标输入端的上游源。
11. disconnect：需要 target_node_selector；可选 source_node_selector、source_output、target_input。
    用户只说“断开第 N 个输入”时，target_input 使用 0 基索引：第 1 个输入为 0，第 2 个输入为 1；不要猜测 source_node_selector。

选择器规则：
1. 已知节点优先使用 {"id": "..."}。
2. 新增节点后要连线时，connect 可用新增节点的稳定名称和 type 作为 source_node_selector，例如 {"type": "constInput", "name": "CO2设定值"}。
3. 禁止只用 {"type": "compare"} 这类明显不唯一的选择器。
4. 无法唯一定位节点、页面、端口或参数时，status 必须是 needs_clarification，并提出具体追问。
5. 页面标签是 type="tab" 节点的 label 字段；用户要求把页面标签中的 A 改成 B 时，应先在 tabs 上唯一定位包含 A 的页面，再输出 update_param，node_selector 使用该 tab id，params 只修改 {"label": "替换后的完整页面标签"}。不要把页面标签写入 name 字段，也不要新增备注或新增节点。
6. 用户看不见内部节点 ID。节点 ID 只能来自系统提供的 target_resolution、current_project_nodes、node_neighborhoods 或 block_contexts；禁止要求用户提供节点 ID。
7. 如果 target_resolution.selected 已唯一定位，优先使用它的 selector。若 target_resolution.candidates 不唯一，不要自行猜测，必须用候选的页面、名称、类型、关键参数、上下游摘要向用户追问。

组合计划规则：
1. 如果要把新增常量、设定值或传感器信号接入 compare/limit 的动态阈值端口，必须先 add_node_from_schema，再 enable_dynamic_input，最后 connect 到 target_input=1。
2. 如果要把新增设定值接入 PID 参数动态端口，必须先 add_node_from_schema，再 enable_dynamic_input 并指定 input_option，最后 connect 到新增动态端口。
3. 不要用 connect 隐式创建端口；端口数量变化必须显式表达为 enable_dynamic_input。
4. copy_block 默认只能表达“复制局部功能块并暂不接外部线”；如果用户要求复制后接入现有 IO、保护、设备或通讯链路，必须先基于边界预览追问入口/出口和确认方式；只有节点、端口和方向都明确时，才可输出 boundary_connections。
5. set_io_point 只表达明确节点上的点位字段变更；如果没有明确节点、字段和值，必须追问。
6. 如果 related_blocks 中有多个功能块候选，且用户没有用候选标题明确指定哪一个，copy_block 必须返回 needs_clarification；不要自行猜测 block_id。

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
    conversation_context: dict[str, Any] | None = None,
    target_resolution: dict[str, Any] | None = None,
    current_project_context: dict[str, Any] | None = None,
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
        conversation_context=conversation_context,
        target_resolution=target_resolution,
        current_project_context=current_project_context,
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
    data = _postprocess_plan_data(message, data, context)
    data["planner"] = "llm"
    data["llm_meta"] = meta
    data["context"] = context
    data["pending_patch"] = {"operations": data["operations"]} if data.get("status") == "planned" else None
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
    conversation_context: dict[str, Any] | None = None,
    target_resolution: dict[str, Any] | None = None,
    current_project_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    nodes = load_project(project_path)
    target_resolution = _normalize_target_resolution_for_planner(message, target_resolution)
    current_project = current_project_context or summarize_current_project_for_planner(project_path, max_nodes=80, max_chars=18000)
    current_project_nodes = search_current_project_nodes(message, project_path=project_path, limit=12)
    anchor_node_ids = _anchor_node_ids(target_resolution, current_project_nodes)
    node_neighborhoods = _load_node_neighborhoods(project_path, anchor_node_ids)
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
    knowledge = _search_planner_knowledge(message, target_resolution=target_resolution, project_type=project_type, limit=5)

    return {
        "project_type": project_type,
        "template_id": template_id,
        "project_summary": summarize_project(nodes),
        "tabs": [{"id": tab_id, "label": label} for tab_id, label in get_tabs(nodes).items()],
        "conversation_context": conversation_context or {},
        "target_resolution": target_resolution,
        "current_project": current_project,
        "current_project_nodes": current_project_nodes,
        "node_neighborhoods": node_neighborhoods,
        "knowledge": knowledge,
        "related_tabs": search_tabs(message, template_id=template_id, limit=3) if template_id else [],
        "related_nodes": search_nodes(message, template_id=template_id, limit=10) if template_id else [],
        "related_blocks": blocks,
        "block_contexts": block_contexts,
        "context_used": {
            "current_project_node_count": len(current_project_nodes),
            "node_neighborhood_count": len(node_neighborhoods),
            "knowledge_count": len(knowledge),
            "block_context_count": len(block_contexts),
        },
    }


def _normalize_target_resolution_for_planner(message: str, target_resolution: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(target_resolution, dict):
        return target_resolution
    if target_resolution.get("status") != "candidates" or target_resolution.get("intent") != "add_logic":
        return target_resolution
    if not _is_pure_add_node_request(message):
        return target_resolution
    return {
        **target_resolution,
        "status": "not_required",
        "selected": None,
        "candidates": [],
        "questions": [],
        "reason": "新增独立节点请求不需要选择已有目标节点。",
    }


def _is_pure_add_node_request(message: str) -> bool:
    text = message.strip()
    if not any(keyword in text for keyword in ("新增", "添加", "创建", "新建")):
        return False
    if not any(keyword in text for keyword in ("常量", "常数", "软件输入", "设定", "PID", "pid")):
        return False
    return not any(keyword in text for keyword in ("接入", "接到", "连接", "连线", "复制", "功能块"))


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


def _postprocess_plan_data(message: str, data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    if data.get("status") == "needs_clarification":
        duplicate_tab_question = _duplicate_add_tab_question(message, data.get("operations"), context) or _duplicate_add_tab_question_from_message(message, context)
        if duplicate_tab_question:
            result["intent"] = "add_logic"
            result["summary"] = "新增页面名称与现有页面重复，需要用户确认。"
            result["risk_level"] = "low"
            result["operations"] = []
            result["questions"] = [duplicate_tab_question]
            return result
        boundary_question = _copy_block_boundary_question(message, context)
        if boundary_question:
            result["intent"] = "add_logic"
            result["summary"] = "复制功能块的边界接线需要用户确认。"
            result["risk_level"] = "high"
            result["operations"] = []
            result["questions"] = [boundary_question]
        return result

    if data.get("status") != "planned":
        return result
    operations = data.get("operations")
    if not isinstance(operations, list):
        return result

    if _is_simple_dynamic_input_enable_request(message):
        enable_ops = [operation for operation in operations if isinstance(operation, dict) and operation.get("op") == "enable_dynamic_input"]
        if enable_ops and len(enable_ops) != len(operations):
            result["operations"] = enable_ops
            result["summary"] = "仅启用目标节点的动态输入端口，不新增节点或连线。"
            result["risk_level"] = _max_risk_level(str(result.get("risk_level") or "low"), "medium")
            reasons = [str(item) for item in result.get("risk_reasons") or [] if str(item)]
            _append_unique(reasons, "用户只要求启用动态输入，已移除 LLM 擅自添加的新增节点或连线。")
            result["risk_reasons"] = reasons

    if _is_input_only_disconnect_request(message):
        result["operations"] = _normalize_input_only_disconnect_operations(message, result.get("operations"))

    duplicate_tab_question = _duplicate_add_tab_question(message, result.get("operations"), context)
    if duplicate_tab_question:
        result["status"] = "needs_clarification"
        result["intent"] = "add_logic"
        result["summary"] = "新增页面名称与现有页面重复，需要用户确认。"
        result["risk_level"] = "low"
        result["operations"] = []
        result["questions"] = [duplicate_tab_question]

    copy_block_question = _copy_block_ambiguity_question(message, result.get("operations"), context)
    if copy_block_question:
        result["status"] = "needs_clarification"
        result["intent"] = "add_logic"
        result["summary"] = "复制功能块目标不唯一，需要用户确认候选。"
        result["risk_level"] = "high"
        result["operations"] = []
        result["questions"] = [copy_block_question]
    return result


def _duplicate_add_tab_question(message: str, operations: Any, context: dict[str, Any]) -> str | None:
    if not isinstance(operations, list):
        return None
    add_tab_ops = [operation for operation in operations if isinstance(operation, dict) and operation.get("op") == "add_tab"]
    if not add_tab_ops:
        return None
    existing_labels = {str(tab.get("label") or "") for tab in context.get("tabs") or [] if isinstance(tab, dict)}
    for operation in add_tab_ops:
        label = str(operation.get("label") or "").strip()
        normalized_label = _normalize_requested_tab_label(label)
        if normalized_label in existing_labels:
            return f"页面“{normalized_label}”已存在。请确认是使用现有页面继续修改，还是换一个新的页面名称。"
    requested_label = _requested_tab_label_from_message(message)
    if requested_label and requested_label in existing_labels:
        return f"页面“{requested_label}”已存在。请确认是使用现有页面继续修改，还是换一个新的页面名称。"
    return None


def _duplicate_add_tab_question_from_message(message: str, context: dict[str, Any]) -> str | None:
    requested_label = _requested_tab_label_from_message(message)
    if not requested_label:
        return None
    existing_labels = {str(tab.get("label") or "") for tab in context.get("tabs") or [] if isinstance(tab, dict)}
    if requested_label not in existing_labels:
        return None
    return f"页面“{requested_label}”已存在。请确认是使用现有页面继续修改，还是换一个新的页面名称。"


def _normalize_requested_tab_label(label: str) -> str:
    return re.sub(r"^(?:已经存在的|已存在的|现有的|已有的)", "", label.strip()).strip()


def _requested_tab_label_from_message(message: str) -> str | None:
    patterns = [
        r"(?:新增|添加|创建|新建)\s*(?:一个|1个)?\s*(?P<label>[^，。！？!?]+?)\s*(?:页面|页签|tab|Tab)",
        r"(?:新增|添加|创建|新建)\s*(?:一个|1个)?\s*(?:页面|页签|tab|Tab)\s*[:：]?\s*(?P<label>[^，。！？!?]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message)
        if not match:
            continue
        label = _normalize_requested_tab_label(match.group("label").strip().strip("\"'“”‘’`"))
        if label:
            return label
    return None


def _copy_block_boundary_question(message: str, context: dict[str, Any]) -> str | None:
    block_id = _explicit_block_id(message)
    if not block_id:
        return None
    if not _requests_copy_block_external_connections(message):
        return None
    block = _block_from_context(context, block_id)
    title = str(block.get("title") or block_id) if block else block_id
    return (
        f"已确认要复制“{title}”。复制功能块默认只保留内部连线，不能自动接回外部入口或出口。"
        "请基于边界预览明确每一条入口/出口接线的源节点、目标节点、端口和方向，或确认暂不接外部线。"
    )


def _explicit_block_id(message: str) -> str | None:
    match = re.search(r"\bblock_[a-fA-F0-9]{6,}\b", message)
    return match.group(0) if match else None


def _requests_copy_block_external_connections(message: str) -> bool:
    text = message.strip()
    if not any(keyword in text for keyword in ("复制", "克隆", "copy_block", "功能块")):
        return False
    if any(keyword in text for keyword in ("暂不接", "不接外部", "先不要接", "不要接外部")):
        return False
    return any(keyword in text for keyword in ("接回", "接入", "接到", "连接", "连线", "入口", "出口", "外部线"))


def _block_from_context(context: dict[str, Any], block_id: str) -> dict[str, Any] | None:
    for key in ("related_blocks", "block_contexts"):
        for item in context.get(key) or []:
            if isinstance(item, dict) and str(item.get("block_id") or "") == block_id:
                return item
    return None


def _is_simple_dynamic_input_enable_request(message: str) -> bool:
    text = message.strip()
    if not text:
        return False
    if not ("动态" in text and "输入" in text and any(keyword in text for keyword in ("启用", "开启", "打开"))):
        return False
    return not any(keyword in text for keyword in ("新增", "添加", "创建", "新建", "接入", "接到", "连接", "连线"))


def _is_input_only_disconnect_request(message: str) -> bool:
    text = message.strip()
    if not any(keyword in text for keyword in ("断开", "断掉", "取消连接", "移除连接")):
        return False
    if "输入" not in text:
        return False
    return not any(keyword in text for keyword in ("来自", "从", "源节点", "source"))


def _normalize_input_only_disconnect_operations(message: str, operations: Any) -> list[Any]:
    if not isinstance(operations, list):
        return operations
    target_input = _human_input_index(message)
    result: list[Any] = []
    for operation in operations:
        if not isinstance(operation, dict) or operation.get("op") != "disconnect":
            result.append(operation)
            continue
        normalized = dict(operation)
        normalized.pop("source_node_selector", None)
        normalized.pop("source_output", None)
        if target_input is not None:
            normalized["target_input"] = target_input
        result.append(normalized)
    return result


def _human_input_index(message: str) -> int | None:
    match = re.search(r"第\s*(?P<index>\d+)\s*(?:个|路|号)?\s*输入", message)
    if match:
        return max(0, int(match.group("index")) - 1)
    chinese_digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    match = re.search(r"第\s*(?P<index>[一二两三四五六七八九])\s*(?:个|路|号)?\s*输入", message)
    if match:
        return chinese_digits[match.group("index")] - 1
    return None


def _copy_block_ambiguity_question(message: str, operations: Any, context: dict[str, Any]) -> str | None:
    if not isinstance(operations, list) or not any(isinstance(operation, dict) and operation.get("op") == "copy_block" for operation in operations):
        return None
    blocks = [block for block in context.get("related_blocks") or [] if isinstance(block, dict)]
    if len(blocks) <= 1:
        return None

    for operation in operations:
        if not isinstance(operation, dict) or operation.get("op") != "copy_block":
            continue
        selected_block_id = str(operation.get("block_id") or operation.get("source_block_id") or "")
        selected_block = next((block for block in blocks if str(block.get("block_id") or "") == selected_block_id), None)
        selected_title = str(selected_block.get("title") or "") if selected_block else ""
        if selected_block_id and selected_block_id in message:
            continue
        if selected_title and selected_title in message:
            continue
        lines = ["我找到了多个可复制的功能块，请确认要复制哪一个："]
        for index, block in enumerate(blocks[:5], start=1):
            title = str(block.get("title") or "未命名功能块")
            tab_label = str(block.get("tab_label") or "")
            node_count = block.get("node_count")
            count_text = f"，{node_count} 个节点" if isinstance(node_count, int) else ""
            tab_text = f"（页面：{tab_label}{count_text}）" if tab_label or count_text else ""
            lines.append(f"{index}. {title}{tab_text}")
        return "\n".join(lines)
    return None


def _max_risk_level(left: str, right: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return left if order.get(left, 0) >= order.get(right, 0) else right


def _compact_context_for_prompt(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_type": context.get("project_type"),
        "template_id": context.get("template_id"),
        "project_summary": context.get("project_summary"),
        "tabs": context.get("tabs"),
        "conversation_context": _compact_conversation_context(context.get("conversation_context")),
        "target_resolution": _compact_target_resolution(context.get("target_resolution")),
        "current_project": _compact_current_project(context.get("current_project")),
        "current_project_nodes": context.get("current_project_nodes"),
        "node_neighborhoods": context.get("node_neighborhoods"),
        "knowledge": context.get("knowledge"),
        "context_used": context.get("context_used"),
        "related_tabs": context.get("related_tabs"),
        "related_nodes": context.get("related_nodes"),
        "related_blocks": context.get("related_blocks"),
        "block_contexts": context.get("block_contexts"),
    }


def _search_planner_knowledge(
    message: str,
    *,
    target_resolution: dict[str, Any] | None,
    project_type: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    queries = _planner_knowledge_queries(message, target_resolution=target_resolution, project_type=project_type)
    if isinstance(target_resolution, dict):
        selected = target_resolution.get("selected")
        if isinstance(selected, dict):
            display_name = selected.get("display_name")
            if isinstance(display_name, str) and display_name.strip():
                _append_unique(queries, display_name.strip())
    per_query_limit = 2 if len(queries) > 1 else limit
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries:
        for item in search_knowledge(query, limit=per_query_limit):
            chunk_id = str(item.get("chunk_id") or "")
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            results.append({**item, "matched_query": query})
            if len(results) >= limit:
                return results
    return results


def _planner_knowledge_queries(message: str, *, target_resolution: dict[str, Any] | None, project_type: str | None) -> list[str]:
    queries: list[str] = []
    if isinstance(target_resolution, dict):
        for key in ("knowledge_queries", "target_queries"):
            for query in target_resolution.get(key) or []:
                if isinstance(query, str):
                    _append_unique(queries, query.strip())
    for query in _inferred_recipe_queries(message, project_type=project_type):
        _append_unique(queries, query)
    _append_unique(queries, message.strip())
    return queries


def _inferred_recipe_queries(message: str, *, project_type: str | None) -> list[str]:
    queries: list[str] = []
    text = message.strip()
    if not text:
        return queries
    if "CO2" in text or "二氧化碳" in text:
        _append_unique(queries, "AHU CO2 新风阀 比较判断 动态阈值 补丁配方")
    if "送风" in text and ("PID" in text or "pid" in text):
        _append_unique(queries, "AHU 送风温度 PID 动态设定 补丁配方")
    if ("PID" in text or "pid" in text) and any(token in text for token in ("输出上限", "输出下限", "highPidOutLimit", "lowPidOutLimit", "动态")):
        _append_unique(queries, "PID 输出上限 下限 动态输入 补丁配方")
    if "过滤网" in text:
        _append_unique(queries, "AHU 过滤网 报警 点位 补丁配方")
    if "防冻" in text:
        _append_unique(queries, "AHU 防冻保护 禁止旁路 补丁配方")
    if "水泵" in text and any(token in text for token in ("阈值", "台数", "比较判断", "运行台数")):
        _append_unique(queries, "机房 水泵 运行台数 阈值 比较判断 补丁配方")
    if "旁通" in text and "压差" in text:
        if any(token in text for token in ("新增", "接入", "接到", "动态")):
            _append_unique(queries, "机房 旁通阀 压差设定 新增 接入 比较判断 补丁配方")
        _append_unique(queries, "机房 旁通阀 压差设定 修改 补丁配方")
    if "比较判断" in text and any(token in text for token in ("动态", "接入", "接到", "阈值")):
        _append_unique(queries, "比较判断 动态阈值 输入 target_input=1 补丁配方")
    if any(token in text for token in ("IO", "io", "I/O", "点位", "通道", "地址", "Modbus", "BACnet", "对象号")):
        _append_unique(queries, "IO 通讯 点位 修改 风险 set_io_point 补丁配方")
    if any(token in text for token in ("copy_block", "复制", "功能块", "克隆")):
        _append_unique(queries, "copy_block 功能块 边界接线 确认 补丁配方")
    if project_type == "ahu" and any(token in text for token in ("设定", "接入", "动态")):
        _append_unique(queries, "AHU 补丁配方 动态设定 接入")
    if project_type == "plant_room" and any(token in text for token in ("设定", "阈值", "接入", "动态")):
        _append_unique(queries, "机房群控 补丁配方 设定 阈值 接入")
    return queries


def _anchor_node_ids(target_resolution: dict[str, Any] | None, current_project_nodes: list[dict[str, Any]]) -> list[str]:
    node_ids: list[str] = []
    if isinstance(target_resolution, dict):
        selected = target_resolution.get("selected")
        if isinstance(selected, dict):
            selector = selected.get("selector")
            if isinstance(selector, dict) and isinstance(selector.get("id"), str):
                node_ids.append(selector["id"])
        for candidate in target_resolution.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            selector = candidate.get("selector")
            if isinstance(selector, dict) and isinstance(selector.get("id"), str):
                node_ids.append(selector["id"])
    for node in current_project_nodes:
        node_id = node.get("node_id") or node.get("id")
        if isinstance(node_id, str):
            node_ids.append(node_id)
    result: list[str] = []
    for node_id in node_ids:
        if node_id not in result:
            result.append(node_id)
    return result[:5]


def _load_node_neighborhoods(project_path: str, anchor_node_ids: list[str]) -> list[dict[str, Any]]:
    neighborhoods: list[dict[str, Any]] = []
    for node_id in anchor_node_ids[:3]:
        try:
            neighborhoods.append(load_node_neighborhood(project_path, [node_id], depth=1, max_nodes=32))
        except RetrievalError:
            continue
    return neighborhoods


def _compact_conversation_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "summary": value.get("conversation_summary"),
        "recent_user_intents": value.get("recent_user_intents", [])[-5:] if isinstance(value.get("recent_user_intents"), list) else [],
        "last_affected_node_ids": value.get("last_affected_node_ids", []),
        "last_touched_entities": value.get("last_touched_entities", []),
        "last_patch_summary": value.get("last_patch_summary"),
    }


def _compact_target_resolution(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {
        "status": value.get("status"),
        "intent": value.get("intent"),
        "selected": _compact_candidate(value.get("selected")),
        "candidates": [_compact_candidate(candidate) for candidate in (value.get("candidates") or []) if isinstance(candidate, dict)],
        "questions": value.get("questions", []),
        "target_queries": value.get("target_queries", []),
        "knowledge_queries": value.get("knowledge_queries", []),
    }


def _compact_candidate(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {
        "kind": value.get("kind", "node"),
        "confidence": value.get("confidence"),
        "selector": value.get("selector"),
        "display_name": value.get("display_name"),
        "description": value.get("description"),
        "reason": value.get("reason"),
        "tab_label": value.get("tab_label"),
        "type": value.get("type"),
        "name": value.get("name"),
        "key_params": value.get("key_params", {}),
    }


def _compact_current_project(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {
        "summary": value.get("summary"),
        "tabs": value.get("tabs"),
        "nodes": value.get("nodes"),
        "budget": value.get("budget"),
    }


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


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
