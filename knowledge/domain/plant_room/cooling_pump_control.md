---
id: plant_room.cooling_pump_control.v1
title: 冷却水泵控制建议
card_type: strategy_card
project_type: plant_room
equipment: [冷却水泵, 主机, 冷却塔]
function_type: cooling_pump_control
question_patterns: [冷却水泵怎么控制, 风冷热泵有没有冷却泵, 冷却泵和冷却塔怎么联动]
applies_when: [水冷主机系统, 存在冷却水泵或冷却塔联动]
avoid_when: [风冷热泵系统未配置冷却水泵, 未确认机组类型]
risk_tags: [device_sequence, device_count_topology]
answer_type: strategy_review
evidence_requirement: [当前工程系统类型, 冷却泵点位, 冷却塔联动链]
patchability: needs_clarification
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/机房群控控制策略.md#冷却水泵控制]
---

## 结论

冷却水泵只适用于有冷却水系统的项目；风冷热泵模板中不能默认新增冷却水泵。

## 适用前提

需要先确认机组类型是水冷主机还是风冷热泵，并定位冷却泵、冷却塔和主机联动。

## 推荐范围

水冷系统可围绕启停顺序、反馈确认、故障切换和塔泵联动给建议。

## 控制链要求

冷却泵应与主机、冷却塔、蝶阀或水流保护形成顺控和保护链。

## 风险和禁止项

禁止未确认系统类型就添加冷却泵；禁止忽略冷却水流、反馈和故障保护。

## 需要追问的信息

需要确认机组类型、冷却泵数量、冷却塔数量、泵阀形式、反馈和故障点。

## 可转补丁条件

已有冷却泵参数时可调整；新增冷却泵链路需点表和人工设计。

## 调试验收检查

检查塔泵主机顺序、反馈超时、水流保护、故障切换和通讯状态。
