---
id: common.io_point_rules.v1
title: IO 点位规则
card_type: point_card
project_type: common
equipment: [现场 IO, 控制器]
function_type: io_point_rules
question_patterns: [点位怎么命名, 输入输出能不能改, 现场点表没有怎么办]
applies_when: [涉及硬接输入输出, 涉及点表或通道, 涉及读写方向]
avoid_when: [没有点表, 未确认现场通道]
risk_tags: [io_point_mapping, protection_logic]
answer_type: risk_review
evidence_requirement: [当前工程 IO 节点, 现场点表, 读写方向规则]
patchability: needs_clarification
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/工程规范.md#高风险操作, knowledge/工程规范.md#不可默认删除的逻辑]
---

## 结论

IO 点位属于系统边界，修改通道、读写方向或名称前必须有点表依据。

## 适用前提

涉及 `hwInput`、`hwOutput` 或现场 DI、DO、AI、AO 点位时适用。

## 推荐范围

可根据点表做命名、备注和地址修正；没有点表时只形成候选需求。

## 控制链要求

读信号应进入判断、报警或联锁；写信号应由控制逻辑驱动，不应被命名为反馈或报警。

## 风险和禁止项

禁止把物理输入接成被上游驱动；禁止把输出点命名为故障、反馈、状态、防冻或急停。

## 需要追问的信息

需要点表、通道号、信号类型、常开常闭、量程、单位和写入权限。

## 可转补丁条件

点表明确且目标唯一时可进入 `set_io_point` 类补丁；缺资料时阻止自动修改。

## 调试验收检查

检查通道、方向、量程、单位、常开常闭、强制测试和报警趋势。
