---
id: ahu.filter_alarm.v1
title: AHU 过滤网报警建议
card_type: strategy_card
project_type: ahu
equipment: [AHU, 过滤网, 送风机]
function_type: filter_alarm
question_patterns: [要不要过滤网报警, 增加过滤网报警, 过滤网压差怎么接]
applies_when: [有过滤网压差开关或压差传感器, 需要维护提醒]
avoid_when: [没有现场点位, 用户要求报警替代安全保护]
risk_tags: [alarm_logic, io_point_mapping]
answer_type: strategy_review
evidence_requirement: [当前工程过滤网点位, 点表或通讯点, 报警输出链]
patchability: needs_clarification
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/AHU控制策略.md#过滤网报警]
---

## 结论

过滤网报警通常建议保留或增加为维护类报警，但不能替代防冻、风机故障等安全保护。

## 适用前提

现场应有过滤网压差开关、压差传感器或通讯报警点，并明确报警显示或发布目标。

## 推荐范围

报警可作为监测、趋势或通讯输出；是否参与停机需要工程师确认，不能默认加入停机联锁。

## 控制链要求

过滤网输入应进入报警判断、延时或通讯输出，必要时关联风机运行状态避免停机误报。

## 风险和禁止项

禁止凭空新增不存在的硬接点；禁止把过滤网报警误接到关键执行器停机链。

## 需要追问的信息

需要确认点位类型、常开常闭、是否需要延时、报警级别和通讯发布对象。

## 可转补丁条件

有明确点位和目标报警对象时，可转为新增或修改报警判断的补丁意图；缺少点表时只形成候选需求。

## 调试验收检查

检查压差信号、报警延时、复位逻辑、页面显示、通讯输出和风机停机状态下的误报情况。
