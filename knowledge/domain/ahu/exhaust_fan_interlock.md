---
id: ahu.exhaust_fan_interlock.v1
title: AHU 排风机联动建议
card_type: strategy_card
project_type: ahu
equipment: [排风机, 送风机, 新风阀]
function_type: exhaust_fan_interlock
question_patterns: [排风机要不要联动, 排风机跟新风阀怎么接, 排风机逻辑在哪里]
applies_when: [存在排风机命令或反馈, 存在送风机运行或新风阀开度]
avoid_when: [没有排风设备, 未确认排风联动依据]
risk_tags: [actuator_logic, air_quality_logic]
answer_type: strategy_review
evidence_requirement: [当前工程排风机页面, 送风机状态, 新风阀或压差依据]
patchability: needs_clarification
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/AHU控制策略.md#排风机联动]
---

## 结论

排风机通常与送风机、新风阀开度或室内压差联动，不能在未确认设备和控制依据时凭空新增。

## 适用前提

工程中应存在排风机命令、反馈或独立排风机页面，并能定位联动来源。

## 推荐范围

可依据新风阀开度阈值、送风机运行或压差需求启动排风机，具体阈值需工程师确认。

## 控制链要求

排风机输出应经过运行允许、故障、反馈、延时和联锁条件。

## 风险和禁止项

禁止绕过排风机故障反馈；禁止只改排风输出而不检查送风和新风联动。

## 需要追问的信息

需要确认是否有独立排风机、启动条件、反馈点、故障点、压差要求和消防联动边界。

## 可转补丁条件

已有排风机逻辑时可转为阈值、延时或联动条件调整；新增设备输出需点表确认。

## 调试验收检查

检查送风联动、新风阀阈值、排风反馈、故障报警、停机复位和手自动状态。
