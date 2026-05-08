---
id: ahu.fresh_return_air_damper.v1
title: AHU 新回风阀控制建议
card_type: strategy_card
project_type: ahu
equipment: [新风阀, 回风阀, 送风机]
function_type: fresh_return_air_damper_control
question_patterns: [新风阀和回风阀怎么联动, 最小新风怎么保留, 新风阀开度怎么改]
applies_when: [存在新风阀或回风阀输出, 存在风机运行状态]
avoid_when: [无阀门执行器, 未确认最小新风要求]
risk_tags: [air_quality_logic, actuator_logic]
answer_type: strategy_review
evidence_requirement: [当前工程阀门输出链, 风机运行约束, 最小新风依据]
patchability: needs_clarification
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/AHU控制策略.md#新风阀和回风阀]
---

## 结论

新风阀和回风阀通常互补或联动控制，停机时应回到安全开度，运行时应保留最小新风边界。

## 适用前提

工程中应能定位新风阀、回风阀、风机状态和相关开度设定或限幅。

## 推荐范围

节能或 CO2 优化应在最小和最大开度之间调节，不应直接删除最小新风限制。

## 控制链要求

阀门输出应接受风机运行、模式、CO2 或温度需求、限幅和安全复位条件。

## 风险和禁止项

禁止停机状态继续调节新风；禁止因节能要求关闭全部新风而未评估空气品质。

## 需要追问的信息

需要确认阀门类型、安全位、最小新风要求、CO2 联动、排风联动和现场验收标准。

## 可转补丁条件

已有阀门设定或限幅节点时，可转为参数修改；新增阀门联动需要人工确认控制策略。

## 调试验收检查

检查运行和停机开度、最小新风、最大开度、联动排风、CO2 需求和手自动切换。
