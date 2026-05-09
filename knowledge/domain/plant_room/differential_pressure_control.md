---
id: plant_room.differential_pressure_control.v1
title: 压差控制建议
card_type: parameter_card
project_type: plant_room
equipment: [水泵, 旁通阀, 压差传感器]
function_type: differential_pressure_control
question_patterns: [压差设定怎么优化, 水泵压差控制怎么做, 节能要不要降压差]
applies_when: [存在压差反馈, 存在水泵变频或旁通阀控制]
avoid_when: [未确认末端需求, 缺少压差传感器]
risk_tags: [comfort_control, actuator_logic]
answer_type: parameter_recommendation
evidence_requirement: [当前工程压差点, 水泵或旁通输出链, 设计或调试依据]
patchability: needs_clarification
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/机房群控控制策略.md#压差控制]
---

## 结论

压差优化可以服务节能，但必须基于末端需求、泵变频和旁通阀逻辑，不能只为了节能删除保护或强降设定。

## 适用前提

工程中应有压差反馈、设定值、水泵频率或旁通阀输出。

## 推荐范围

推荐范围需要工程师和现场调试确认；系统可先指出当前工程参数和影响范围。

## 控制链要求

压差设定应影响水泵频率或旁通阀，并受限幅、运行台数和模式约束。

## 风险和禁止项

禁止无依据降低压差导致末端供能不足；禁止删除泵保护和故障切换。

## 需要追问的信息

需要确认设计压差、末端负荷、泵变频方式、旁通阀策略和允许节能目标。

## 可转补丁条件

只在定位到唯一设定或 PID 参数后生成候选修改；策略调整需人工确认。

## 调试验收检查

检查压差稳定性、泵频率、旁通开度、末端供能、报警和模式切换。
