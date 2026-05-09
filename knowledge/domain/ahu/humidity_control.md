---
id: ahu.humidity_control.v1
title: AHU 湿度控制建议
card_type: strategy_card
project_type: ahu
equipment: [AHU, 加湿器, 直膨机, 冷热水阀]
function_type: humidity_control
question_patterns: [湿度控制怎么做, 要不要除湿控制, 加湿输出怎么接]
applies_when: [存在湿度传感器, 明确加湿或除湿执行器]
avoid_when: [没有加湿除湿设备, 只提出湿度但未说明执行器]
risk_tags: [comfort_control, actuator_logic]
answer_type: strategy_review
evidence_requirement: [当前工程湿度点, 执行器点表, 风机运行约束]
patchability: requires_manual_design
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/AHU控制策略.md#湿度控制]
---

## 结论

湿度控制必须先确认执行器类型；只有湿度传感器不足以自动生成加湿、除湿或再热控制。

## 适用前提

工程中需要有湿度反馈，并明确加湿器、直膨除湿、冷水阀除湿或再热等执行对象。

## 推荐范围

可先形成候选需求，要求工程师确认控制方式、设定范围、死区和联锁。

## 控制链要求

湿度反馈应进入设定比较或 PID，再受风机运行、模式、温度边界和设备保护约束。

## 风险和禁止项

禁止凭空新增加湿或除湿输出；禁止在无风状态下允许加湿或直膨除湿。

## 需要追问的信息

需要确认加湿器、除湿设备、再热能力、湿度设定、控制死区和现场露点风险。

## 可转补丁条件

已有湿度控制链可调整设定或延时；新增完整控制链需要人工设计。

## 调试验收检查

检查湿度趋势、执行器输出、温度影响、风机联锁、模式切换和报警保护。
