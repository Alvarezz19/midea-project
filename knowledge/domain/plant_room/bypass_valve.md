---
id: plant_room.bypass_valve.v1
title: 旁通阀控制建议
card_type: parameter_card
project_type: plant_room
equipment: [旁通阀, 水泵, 压差传感器]
function_type: bypass_valve_control
question_patterns: [旁通阀怎么控制, 旁通压差设多少, 旁通阀 PID 怎么调]
applies_when: [存在旁通阀输出, 存在压差反馈, 存在压差设定或 PID]
avoid_when: [没有压差反馈, 用户要求直接改传感器输入]
risk_tags: [comfort_control, actuator_logic]
answer_type: parameter_recommendation
evidence_requirement: [当前工程压差链, 旁通阀输出, PID 和限幅节点]
patchability: draftable
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/机房群控控制策略.md#旁通阀控制]
---

## 结论

旁通阀通常用于维持压差或流量，建议必须基于当前工程压差设定、PID、限幅和输出链。

## 适用前提

工程中应存在压差反馈、旁通阀输出和可定位的设定值或 PID 参数。

## 推荐范围

没有工程师确认的压差范围时，只能建议复核当前模板设定和现场调试值。

## 控制链要求

压差反馈和设定应进入比较或 PID，再经过限幅和模式允许后输出到旁通阀。

## 风险和禁止项

禁止直接修改压差传感器；禁止删除限幅或将旁通阀强制为常量开度。

## 需要追问的信息

需要确认设计压差、末端最不利环路、泵变频策略、阀门类型和输出范围。

## 可转补丁条件

唯一定位到设定、PID 或限幅节点时，可生成参数修改意图。

## 调试验收检查

检查压差趋势、旁通阀开度、泵频率影响、限幅、手自动和报警。
