---
id: ahu.fan_variable_frequency.v1
title: AHU 风机变频控制建议
card_type: strategy_card
project_type: ahu
equipment: [送风机, 变频器, AHU]
function_type: fan_variable_frequency
question_patterns: [风机变频怎么控制, 风机频率设多少, 风机变频接静压还是 CO2]
applies_when: [存在风机频率输出, 存在静压风量或 CO2 控制依据]
avoid_when: [未确认控制依据, 没有变频输出点]
risk_tags: [comfort_control, actuator_logic]
answer_type: strategy_review
evidence_requirement: [当前工程频率输出, 控制依据点, PID 或限幅链]
patchability: needs_clarification
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/AHU控制策略.md#风机变频]
---

## 结论

风机变频不能默认固定频率，应先确认控制依据是静压、风量、CO2 需求还是人工设定。

## 适用前提

当前工程应有变频输出点和至少一个控制依据点，且能定位频率上下限。

## 推荐范围

已有变频链时优先调整设定、上下限、PID 参数或使能条件，不应重建整段逻辑。

## 控制链要求

控制依据进入比较或 PID 后，应经过限幅、风机运行、故障和手自动约束，再输出频率。

## 风险和禁止项

禁止绕过频率上下限；禁止未确认控制依据就把变频输出改为常量。

## 需要追问的信息

需要确认静压或风量目标、CO2 联动需求、频率上下限、变频器通讯方式和手自动边界。

## 可转补丁条件

明确参数节点时可转为设定、限幅或 PID 参数修改；控制依据变更需要人工确认。

## 调试验收检查

检查频率输出、上下限、反馈趋势、风机状态、故障闭锁和手自动切换。
