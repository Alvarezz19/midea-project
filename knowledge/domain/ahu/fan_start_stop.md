---
id: ahu.fan_start_stop.v1
title: AHU 送风机启停建议
card_type: strategy_card
project_type: ahu
equipment: [AHU, 送风机]
function_type: fan_start_stop
question_patterns: [送风机怎么启停, 风机启动条件, 风机停机时哪些输出关闭]
applies_when: [存在送风机命令, 存在运行反馈或故障点]
avoid_when: [缺少风机反馈, 用户要求绕过故障联锁]
risk_tags: [actuator_logic, protection_logic]
answer_type: strategy_review
evidence_requirement: [当前工程风机命令, 运行反馈, 故障和保护链]
patchability: needs_clarification
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/AHU控制策略.md#风机启停]
---

## 结论

送风机通常是 AHU 主要使能条件，温度、CO2、湿度和直膨机输出应受风机运行状态约束。

## 适用前提

工程中需要有风机命令、运行反馈、故障或手自动状态，且能追踪到下游允许条件。

## 推荐范围

可调整启动条件、延时、报警或显示，但应保留故障和保护联锁。

## 控制链要求

风机启停链应包含使能、手自动、故障、保护、反馈和输出命令，停机时相关执行器回到安全状态。

## 风险和禁止项

禁止绕过风机故障；禁止停机后仍允许直膨机、加热、加湿或新风控制继续输出。

## 需要追问的信息

需要确认手自动边界、启停来源、运行反馈、故障点、消防或远程联锁要求。

## 可转补丁条件

明确修改延时、设定、备注或单一使能条件时可转补丁；重构启停顺控需人工确认。

## 调试验收检查

检查启停命令、反馈超时、故障闭锁、停机复位、下游执行器关闭和报警发布。
