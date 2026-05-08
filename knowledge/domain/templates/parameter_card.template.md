---
id: example.parameter.v1
title: 示例参数建议卡
card_type: parameter_card
project_type: common
equipment: [示例设备]
function_type: example_parameter
question_patterns: [参数设多少合适]
applies_when: [有对应反馈点, 有对应设定点]
avoid_when: [缺少执行器或现场依据]
risk_tags: [comfort_control]
answer_type: parameter_recommendation
evidence_requirement: [当前工程设定链, 参数来源, 已审阅领域知识]
patchability: draftable
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: []
---

## 结论

写清楚建议值、建议范围或不能给出高置信建议的原因。

## 适用前提

列出适用项目、设备和现场条件。

## 推荐范围

列出推荐值、单位和只可作为参考的边界。

## 控制链要求

说明参数作用于哪个比较、PID、限幅或输出链。

## 风险和禁止项

列出误改传感器、绕过保护、误驱动执行器等风险。

## 需要追问的信息

列出缺失的设备、点表、设计工况或现场要求。

## 可转补丁条件

说明何时只可形成候选需求，何时可进入补丁链路。

## 调试验收检查

列出调试时需要确认的趋势、报警、输出和联锁。
