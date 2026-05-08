---
id: common.protection_rules.v1
title: 保护和联锁不可默认删除规则
card_type: risk_card
project_type: common
equipment: [关键执行器, 保护信号]
function_type: protection_rules
question_patterns: [能不能删除保护, 能不能旁路联锁, 简化故障逻辑]
applies_when: [涉及保护信号, 涉及故障联锁, 涉及关键执行器]
avoid_when: [已有等效保护但未复核, 用户仅要求简化]
risk_tags: [protection_logic, actuator_logic]
answer_type: risk_review
evidence_requirement: [当前工程保护链, 关键执行器, rules/protection_logic.json]
patchability: blocked
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/工程规范.md#不可默认删除的逻辑]
---

## 结论

防冻、故障、反馈、手自动、设备可用、最小启停延时和关键联锁不能默认删除或旁路。

## 适用前提

涉及任何保护、故障、反馈、联锁或关键执行器输出时适用。

## 推荐范围

可以建议复核、重命名、增加备注或补充等效保护；不能给出无确认的删除建议。

## 控制链要求

保护信号应能追踪到报警、闭锁、停机、禁止输出或安全复位等结果。

## 风险和禁止项

禁止删除保护节点、断开保护链、用常量覆盖保护、删除反馈和故障屏蔽。

## 需要追问的信息

需要确认现场保护装置、等效保护、人工确认记录、调试方案和安全责任边界。

## 可转补丁条件

删除或旁路保护不可自动转补丁；只允许转为增加备注、显示或保守参数调整。

## 调试验收检查

检查保护触发、报警、执行器闭锁、复位、趋势记录和导出前校验。
