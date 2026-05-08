---
id: plant_room.chilled_pump_control.v1
title: 冷冻水泵控制建议
card_type: strategy_card
project_type: plant_room
equipment: [冷冻水泵, 主机, 蝶阀]
function_type: chilled_pump_control
question_patterns: [冷冻水泵怎么控制, 水泵数量怎么改, 冷冻泵轮值怎么做]
applies_when: [存在冷冻水泵命令, 存在主机联动, 存在运行反馈或故障点]
avoid_when: [未确认泵连接形式, 未确认设备数量来源]
risk_tags: [device_sequence, rotation_logic, device_count_topology]
answer_type: strategy_review
evidence_requirement: [当前工程水泵控制链, 配置页设备数量, 轮值或反馈链]
patchability: requires_manual_design
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/机房群控控制策略.md#冷冻水泵控制]
---

## 结论

冷冻水泵通常与主机联动，修改数量或轮值策略时必须同步反馈、故障、可用状态和通讯映射。

## 适用前提

工程中应有水泵命令、运行反馈、故障、主机联动和可能的配置页设备数量。

## 推荐范围

可调整阈值、延时、压差设定或优先级；设备数量变化应作为系统拓扑变更处理。

## 控制链要求

水泵应在主机启停顺序中承担先启、确认反馈和故障切换角色。

## 风险和禁止项

禁止只复制水泵命令节点而不复制反馈、故障、位组合和通讯输出。

## 需要追问的信息

需要确认水泵数量、备用数量、并联或一对一形式、反馈点、故障点和变频方式。

## 可转补丁条件

已有明确参数节点时可转为参数修改；数量和轮值结构调整需人工设计后再执行。

## 调试验收检查

检查启停顺序、反馈超时、故障切换、轮值结果、通讯点和压差控制影响。
