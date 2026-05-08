---
id: ahu.dx_unit.v1
title: AHU 直膨机控制建议
card_type: strategy_card
project_type: ahu
equipment: [AHU, 直膨机, 送风机]
function_type: dx_unit_control
question_patterns: [直膨机怎么控制, 直膨机故障怎么接, 直膨除湿怎么做]
applies_when: [存在直膨机状态或故障点, 存在制冷制热模式, 存在温湿度控制需求]
avoid_when: [没有直膨设备, 缺少运行反馈和故障反馈]
risk_tags: [actuator_logic, protection_logic]
answer_type: strategy_review
evidence_requirement: [当前工程直膨机页面, 当前工程故障链, 运行反馈和模式点]
patchability: requires_manual_design
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/AHU控制策略.md#直膨机控制]
---

## 结论

直膨机控制涉及启停、模式、故障、运行反馈和保护联锁，通常不应只靠单个设定值修改完成。

## 适用前提

工程中应存在直膨机状态、故障、模式或控制页面，并明确直膨机与风机运行的允许关系。

## 推荐范围

可优先审查“直膨机状态”“直膨机故障”“控制”等页面角色，再判断是参数调整还是人工设计。

## 控制链要求

直膨机输出应受风机运行、模式允许、温湿度需求、故障状态和保护条件约束。

## 风险和禁止项

禁止无风运行直膨机；禁止删除故障反馈、运行反馈或模式联锁；禁止凭空新增现场输出点。

## 需要追问的信息

需要确认直膨机类型、控制接口、运行反馈、故障反馈、模式命令和除湿控制策略。

## 可转补丁条件

仅参数、备注或已有状态映射修改可考虑补丁；新增完整直膨控制链应要求人工设计。

## 调试验收检查

检查启停命令、运行反馈、故障闭锁、模式切换、温湿度需求、停机复位和通讯显示。
