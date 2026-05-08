---
id: example.risk.v1
title: 示例风险规则卡
card_type: risk_card
project_type: common
equipment: [示例设备]
function_type: example_risk
question_patterns: [这个保护能不能删]
applies_when: [涉及保护或联锁]
avoid_when: [已有等效保护但未复核]
risk_tags: [protection_logic]
answer_type: risk_review
evidence_requirement: [当前工程保护链, 关键执行器, 风险规则]
patchability: blocked
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: []
---

## 结论

写清楚禁止边界。

## 适用前提

列出触发本卡的工程条件。

## 推荐范围

列出保守建议或替代做法。

## 控制链要求

说明保护信号必须影响哪些对象。

## 风险和禁止项

列出高风险或阻塞项。

## 需要追问的信息

列出人工复核所需资料。

## 可转补丁条件

说明是否允许转补丁。

## 调试验收检查

列出保护动作验收点。
