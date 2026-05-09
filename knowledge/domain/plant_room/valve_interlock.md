---
id: plant_room.valve_interlock.v1
title: 蝶阀联动建议
card_type: strategy_card
project_type: plant_room
equipment: [蝶阀, 主机, 水泵, 冷却塔]
function_type: valve_interlock
question_patterns: [蝶阀怎么联动, 启动前要不要先开阀, 阀门到位怎么处理]
applies_when: [存在蝶阀命令, 存在设备启停顺控, 存在开到位或关到位反馈]
avoid_when: [缺少阀门反馈点, 用户要求删除阀门到位联锁]
risk_tags: [device_sequence, protection_logic]
answer_type: strategy_review
evidence_requirement: [当前工程阀门命令, 到位反馈, 设备顺控链]
patchability: needs_clarification
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/机房群控控制策略.md#蝶阀联动]
---

## 结论

蝶阀通常是主机、水泵或冷却塔启停顺控的一部分，设备启动前应考虑开阀和到位确认。

## 适用前提

当前工程应能定位阀门命令、开到位、关到位、故障和关联设备。

## 推荐范围

可调整开阀延时、反馈超时或报警显示，不应默认删除到位联锁。

## 控制链要求

阀门开到位应参与设备允许启动，停机后按顺序延时关阀。

## 风险和禁止项

禁止绕过阀门到位直接启设备；禁止只改设备命令而不检查阀门链。

## 需要追问的信息

需要确认阀门类型、到位反馈、故障点、开关时间和设备顺序要求。

## 可转补丁条件

明确延时或报警参数时可转补丁；删除联锁或重排顺序需人工确认。

## 调试验收检查

检查开阀、到位、启设备、停设备、关阀、故障报警和反馈超时。
