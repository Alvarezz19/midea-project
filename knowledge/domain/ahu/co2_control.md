---
id: ahu.co2_control.v1
title: AHU CO2 控制建议
card_type: strategy_card
project_type: ahu
equipment: [AHU, 新风阀, 回风阀, 送风机]
function_type: co2_control
question_patterns: [CO2 阈值多少合适, CO2 控制要不要接新风阀, CO2 高了怎么控制]
applies_when: [有 CO2 传感器, 有新风阀或风机频率可调, 有风机运行状态]
avoid_when: [没有新风执行器, 风机停机时要求单独开新风阀]
risk_tags: [air_quality_logic, actuator_logic]
answer_type: strategy_review
evidence_requirement: [当前工程 CO2 点位, 当前工程新风执行器链, 已审阅参数范围]
patchability: needs_clarification
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/AHU控制策略.md#CO2 控制]
---

## 结论

CO2 控制应作为空气品质逻辑处理，通常通过新风阀开度或风机频率调节，但必须受送风机运行状态约束。

## 适用前提

当前工程需要有 CO2 输入点、可调新风执行器或相关风机调节能力，并能识别 AHU 运行状态。

## 推荐范围

阈值和回差必须由工程师或项目标准确认；历史模板统计只能作为参考证据。

## 控制链要求

CO2 反馈和 CO2 设定值进入比较或 PID 后，应经过最小新风、最大开度、风机运行和模式允许条件，再影响新风阀或风机输出。

## 风险和禁止项

禁止风机未运行时单独打开新风阀；禁止因节能要求删除最小新风或空气品质约束。

## 需要追问的信息

需要确认目标 CO2 范围、新风阀类型、是否有风机变频、最小新风要求和是否允许联动排风。

## 可转补丁条件

若已有 CO2 控制链，可转为设定值、回差、限幅或使能条件的修改意图；若需新增完整控制链，应先人工确认。

## 调试验收检查

检查 CO2 趋势、阀门输出、风机状态联锁、最小新风开度、停机复位和报警展示。
