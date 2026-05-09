---
id: ahu.supply_air_temperature.v1
title: AHU 送风温度控制建议
card_type: parameter_card
project_type: ahu
equipment: [AHU, 送风机, 冷热水阀, 直膨机]
function_type: supply_air_temperature_control
question_patterns: [送风温度设定多少合适, 送风温度怎么改, 送风温度控制影响哪里]
applies_when: [有送风温度反馈, 有温度设定值, 有冷热源执行器]
avoid_when: [缺少温度执行器, 用户要求直接修改传感器输入]
risk_tags: [comfort_control, actuator_logic]
answer_type: parameter_recommendation
evidence_requirement: [当前工程设定链, 当前工程执行器链路, 已审阅领域知识或人工确认]
patchability: draftable
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/AHU控制策略.md#送风温度控制]
---

## 结论

送风温度建议应先定位当前工程的设定值链和执行器链，不能脱离冷热源、直膨机或水阀能力直接给高置信数值。本卡来自现有知识整理，生产使用前需要工程师复核。

## 适用前提

工程中应存在送风温度反馈、温度设定值，以及能调节冷热量的执行器。

## 推荐范围

没有工程师确认的参数范围时，只能回答“按当前模板设定或人工设计值调整”，不能把历史样例包装为规范。

## 控制链要求

设定值应进入比较、PID 或模式判断，再经过限幅、联锁和执行器输出，不应直接覆盖传感器输入点。

## 风险和禁止项

禁止把送风温度传感器当成设定值修改；禁止绕过风机运行、直膨机保护或冷热水阀联锁。

## 需要追问的信息

需要确认设计工况、冷热源类型、温度控制对象、允许波动范围和现场调试目标。

## 可转补丁条件

只有当当前工程中唯一定位到送风温度设定节点时，才可生成修改设定值的自然语言补丁意图。

## 调试验收检查

验收时检查设定值生效、反馈趋势、执行器输出限幅、模式切换和停机后的安全输出。
