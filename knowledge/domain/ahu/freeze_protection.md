---
id: ahu.freeze_protection.v1
title: AHU 防冻保护规则
card_type: risk_card
project_type: ahu
equipment: [AHU, 送风机, 新风阀, 热水阀]
function_type: freeze_protection
question_patterns: [防冻保护能不能删, 防冻报警怎么处理, 简化防冻逻辑]
applies_when: [涉及防冻开关, 涉及低温保护, 涉及风机或阀门保护]
avoid_when: [没有等效保护证明, 未经人工确认要求删除保护]
risk_tags: [protection_logic, actuator_logic]
answer_type: risk_review
evidence_requirement: [当前工程防冻点, 当前工程关键执行器链, 保护规则]
patchability: blocked
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/AHU控制策略.md#防冻保护, knowledge/工程规范.md#不可默认删除的逻辑]
---

## 结论

防冻保护属于 AHU 高风险保护逻辑，不能因为用户说“简化”就删除、旁路或弱化。

## 适用前提

只要工程中存在防冻、低温、盘管保护、风机停机或新风阀关闭等线索，就应触发本规则。

## 推荐范围

可讨论报警延时、复位方式、显示方式或人工确认后的替代保护，但默认建议是保留保护链。

## 控制链要求

防冻信号通常应影响风机停机、新风阀关闭、热水阀安全开度或报警输出，链路应可追踪。

## 风险和禁止项

禁止删除防冻输入、断开防冻到关键执行器的链路，禁止用常量直接覆盖保护结果。

## 需要追问的信息

需要确认现场盘管类型、低温开关位置、热水阀安全位、复位方式和已有等效保护。

## 可转补丁条件

删除或旁路防冻保护不可自动转补丁；只允许在人工确认后生成保守的显示、备注或参数调整意图。

## 调试验收检查

验收时模拟防冻触发，检查风机、阀门、报警、复位和趋势记录是否符合保护要求。
