---
document_id: SAFE-COND-001
title: 电厂智能诊断安全边界 演示样例
doc_type: safety_notice
version: DEMO-1.0
applicable_units: [U01]
system: condenser
access_level: operator
source_status: demo_verified
---

# 人工确认要求

智能诊断结果用于辅助运行人员核查。风险等级为 high 或 critical 时，应提示具备权限的运行人员优先关注并复核数据质量、现场状态和正式规程。

# 禁止自动控制

系统可以读取数据、分析趋势、关联预警、检索知识和生成报告，但不得直接向 DCS、SIS、PLC、阀门、泵组或其他现场执行设备下发控制命令。

