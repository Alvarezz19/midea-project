---
id: common.communication_rules.v1
title: 通讯点规则
card_type: point_card
project_type: common
equipment: [Modbus, BACnet, MQTT]
function_type: communication_rules
question_patterns: [Modbus 地址怎么分配, BACnet 对象号能不能复用, 通讯点读写方向怎么判断]
applies_when: [涉及通讯输入输出, 涉及地址或对象号, 涉及比例或位解析]
avoid_when: [没有通讯点表, 未确认对象号唯一性]
risk_tags: [communication_mapping, io_point_mapping]
answer_type: risk_review
evidence_requirement: [当前工程通讯点, 通讯点表, 地址或对象号规则]
patchability: needs_clarification
review_status: generated
owner: controls_engineering
updated_at: 2026-05-08
source_refs: [knowledge/工程规范.md#高风险操作, knowledge/工程规范.md#不可默认删除的逻辑]
---

## 结论

通讯地址、对象号和读写方向修改属于高风险边界操作，不能凭历史模板自动复用。

## 适用前提

涉及 Modbus、BACnet、MQTT 或其他通讯输入输出时适用。

## 推荐范围

可根据点表修正名称、地址、对象号、比例和发布方向；缺少点表时必须追问。

## 控制链要求

通讯输入应作为外部读信号，通讯输出应由内部变量或控制结果发布。

## 风险和禁止项

禁止复制功能块时复用 BACnet 对象号；禁止把读点当写点或把写点当反馈。

## 需要追问的信息

需要通讯协议、站号、地址、对象号、读写方向、比例、单位和唯一性要求。

## 可转补丁条件

点表明确且冲突检查通过时可转补丁；对象号或地址不确定时必须阻断。

## 调试验收检查

检查通讯读写、地址唯一性、对象号唯一性、比例换算、离线报警和上位机显示。
