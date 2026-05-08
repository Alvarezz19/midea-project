---
id: common.commissioning_checklists.v1
title: 调试验收检查建议
card_type: commissioning_card
project_type: common
equipment: [控制器, 现场设备, 上位机]
function_type: commissioning_checklists
question_patterns: [怎么调试验收, 修改后要检查什么, 导出前要复核什么]
applies_when: [咨询调试验收, 修改 IO 通讯或保护, 修改顺控或参数]
avoid_when: [用户要求跳过现场复核, 缺少点表或调试条件]
risk_tags: [commissioning, protection_logic, communication_mapping]
answer_type: commissioning
evidence_requirement: [当前工程修改范围, 校验报告, 点表和现场调试记录]
patchability: not_applicable
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/工程规范.md#导出检查, knowledge/工程规范.md#校验要求]
---

## 结论

调试验收应围绕修改范围、关键保护、IO 通讯、设备动作和导出校验展开，不能只看 JSON 结构通过。

## 适用前提

适用于所有涉及现场点位、通讯、保护、顺控、参数和导出的咨询。

## 推荐范围

至少检查结构校验、需求覆盖、点表、通讯、强制测试、保护动作、趋势和用户确认记录。

## 控制链要求

验收应覆盖从输入、设定、逻辑、限幅、联锁到输出的完整链路。

## 风险和禁止项

禁止在结构错误、需求缺失、高风险未确认或点表不明时导出交付。

## 需要追问的信息

需要确认现场调试条件、点表版本、上位机显示、设备可强制范围和验收责任人。

## 可转补丁条件

本卡不直接转补丁，可生成调试 checklist 或候选验收要求。

## 调试验收检查

检查导出校验、点位读写、通讯地址、保护动作、反馈超时、趋势记录和版本元数据。
