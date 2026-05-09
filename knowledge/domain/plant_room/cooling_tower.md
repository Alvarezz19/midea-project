---
id: plant_room.cooling_tower.v1
title: 冷却塔控制建议
card_type: strategy_card
project_type: plant_room
equipment: [冷却塔, 冷却水泵, 蝶阀]
function_type: cooling_tower_control
question_patterns: [冷却塔怎么启停, 冷却塔风机怎么控制, 冷却塔故障怎么处理]
applies_when: [存在冷却塔或塔风机, 存在冷却水温度或主机联动]
avoid_when: [风冷热泵系统无冷却塔, 缺少塔风机点表]
risk_tags: [device_sequence, actuator_logic, protection_logic]
answer_type: strategy_review
evidence_requirement: [当前工程冷却塔链, 冷却水温度点, 故障和反馈点]
patchability: needs_clarification
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/机房群控控制策略.md#冷却塔控制]
---

## 结论

冷却塔控制应结合冷却水温度、塔风机、蝶阀、冷却泵和主机状态，不能只改风机输出。

## 适用前提

当前工程应存在冷却塔相关设备和温度、反馈、故障或通讯点。

## 推荐范围

可调整温度阈值、启停延时、故障屏蔽和轮值优先级。

## 控制链要求

冷却塔输出应经过温度需求、泵运行、阀门状态、故障和反馈确认。

## 风险和禁止项

禁止删除冷却塔故障屏蔽；禁止在无泵运行或阀门未开条件下孤立启动塔风机。

## 需要追问的信息

需要确认塔数量、风机控制方式、蝶阀点位、温度目标、故障反馈和联动顺序。

## 可转补丁条件

已有阈值或延时节点时可转补丁；新增塔控制链需人工确认点表。

## 调试验收检查

检查温度触发、塔泵阀联动、故障闭锁、反馈超时、轮值和通讯输出。
