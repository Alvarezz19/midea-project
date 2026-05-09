---
id: plant_room.rotation_strategy.v1
title: 设备轮值策略建议
card_type: strategy_card
project_type: plant_room
equipment: [主机, 水泵, 冷却塔]
function_type: rotation_strategy
question_patterns: [轮值策略怎么改, 优先级怎么调整, 故障设备要不要参与轮询]
applies_when: [存在运行时间或优先级, 存在多台同类设备, 存在故障或可用状态]
avoid_when: [单台设备, 未确认备用策略]
risk_tags: [rotation_logic, device_count_topology]
answer_type: strategy_review
evidence_requirement: [当前工程运行时间链, 优先级或排序节点, 故障可用状态]
patchability: needs_clarification
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/机房群控控制策略.md#轮值策略, knowledge/机房群控控制策略.md#故障切换]
---

## 结论

轮值策略用于均衡设备运行，修改优先级时应保留故障屏蔽、可用状态和运行时间累计。

## 适用前提

工程中应存在多台设备、运行时间、优先级、故障状态或排序选择逻辑。

## 推荐范围

可调整优先级、排序依据或切换周期，但不能让故障设备参与轮值。

## 控制链要求

轮值结果应与设备可用状态、运行反馈、故障、手自动和需求台数共同决定输出。

## 风险和禁止项

禁止删除运行时间累计；禁止绕过故障屏蔽；禁止设备数量变化时不更新位映射。

## 需要追问的信息

需要确认设备数量、备用策略、优先级原则、切换周期和手自动边界。

## 可转补丁条件

明确优先级或周期参数时可转补丁；轮值算法替换需人工确认。

## 调试验收检查

检查排序结果、故障屏蔽、备用切换、运行时间累计、通讯展示和手自动切换。
