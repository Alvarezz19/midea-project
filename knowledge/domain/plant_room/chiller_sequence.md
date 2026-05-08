---
id: plant_room.chiller_sequence.v1
title: 机房群控主机启停和加减载建议
card_type: strategy_card
project_type: plant_room
equipment: [主机, 水泵, 蝶阀]
function_type: chiller_sequence
question_patterns: [主机怎么启停, 主机加减载怎么判断, 主机数量怎么改]
applies_when: [存在主机命令, 存在运行台数或负荷需求, 存在水泵和蝶阀联动]
avoid_when: [未确认机组类型, 用户只要求单独修改主机节点]
risk_tags: [device_sequence, device_count_topology, protection_logic]
answer_type: strategy_review
evidence_requirement: [当前工程主机顺控链, 水泵和蝶阀联动链, 设备数量配置]
patchability: requires_manual_design
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/机房群控控制策略.md#主机启停, knowledge/机房群控控制策略.md#主机加减载]
---

## 结论

主机启停和加减载是系统级顺控，不能只修改主机输出节点；必须同时检查水泵、蝶阀、故障和运行台数逻辑。

## 适用前提

当前工程应能定位主机命令、运行反馈、故障、设备数量、负荷需求和联动设备。

## 推荐范围

可审查加减载阈值、延时、运行台数和优先级，但涉及设备数量和拓扑时应人工设计。

## 控制链要求

主机启动通常应经过水泵、蝶阀、水流或反馈确认，再进入主机输出；停机也应有延时和联动顺序。

## 风险和禁止项

禁止单独增加主机数量而不同步位映射、实例、点表、通讯和联动链。

## 需要追问的信息

需要确认系统类型、主机数量、泵阀连接形式、备用策略、负荷依据和现场点表。

## 可转补丁条件

明确阈值或延时修改可转候选补丁；设备数量、拓扑或顺控重构必须人工确认。

## 调试验收检查

检查启停顺序、反馈超时、故障切换、加减载延时、最小运行时间和通讯输出。
