from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.services.llm_gateway import LLMGatewayError, chat_json


ProjectType = Literal["plant_room", "ahu", "unknown"]
RiskLevel = Literal["low", "medium", "high"]


class ExtractedRequirement(BaseModel):
    project_type: ProjectType = "unknown"
    project_type_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    summary: str = ""
    equipment: list[str] = Field(default_factory=list)
    control_features: list[str] = Field(default_factory=list)
    communication: list[str] = Field(default_factory=list)
    io_points: list[str] = Field(default_factory=list)
    protection_logic: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = "low"
    risk_reasons: list[str] = Field(default_factory=list)
    raw_intent: str = ""


class RequirementExtractionError(ValueError):
    """需求抽取失败。"""


SYSTEM_PROMPT = """你是楼宇自控工程 JSON 智能体的需求抽取器。
你只能输出 json 对象，不要输出 markdown。
目标是从用户自然语言中抽取工程需求，供后续模板选择和结构化补丁规划使用。

输出 JSON 格式必须符合：
{
  "project_type": "plant_room | ahu | unknown",
  "project_type_confidence": 0.0,
  "summary": "一句话中文摘要",
  "equipment": ["设备或执行器"],
  "control_features": ["控制功能"],
  "communication": ["通讯方式或协议"],
  "io_points": ["明确出现的 IO 或点位"],
  "protection_logic": ["保护、联锁、报警、反馈"],
  "missing_fields": ["缺失字段"],
  "clarification_questions": ["需要追问的问题"],
  "risk_level": "low | medium | high",
  "risk_reasons": ["风险原因"],
  "raw_intent": "用户原始意图简述"
}

规则：
1. 机房群控、冷站、主机、水泵、冷却塔、旁通阀通常归为 plant_room。
2. AHU、空调箱、新风机组、送风机、排风机、直膨机、新风阀通常归为 ahu。
3. 不能确定项目类型时使用 unknown，并提出澄清问题。
4. 涉及删除、绕过保护逻辑、修改 IO/通讯地址、修改设备数量时 risk_level 至少为 high。
5. 未明确设备数量、通讯方式、关键保护时，把缺口写入 missing_fields 和 clarification_questions。
"""


def extract_requirement_with_llm(message: str, *, provider: str | None = None) -> dict[str, Any]:
    if not message.strip():
        raise RequirementExtractionError("需求文本不能为空。")

    try:
        result = chat_json(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"请抽取以下需求，输出 json：\n{message}"},
            ],
            provider=provider,
        )
    except LLMGatewayError as exc:
        raise RequirementExtractionError(str(exc)) from exc

    meta = result.pop("_llm_meta", None)
    if isinstance(meta, dict):
        meta.setdefault("prompt_name", "requirement_extractor")
    try:
        extracted = ExtractedRequirement.model_validate(result)
    except ValidationError as exc:
        raise RequirementExtractionError(f"需求抽取结果不符合 schema: {exc}") from exc

    data = extracted.model_dump()
    data["llm_meta"] = meta
    return data
