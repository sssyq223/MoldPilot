---
name: project-lifecycle-orchestration
description: 只读核对一个项目从承接、合同、正式开工和基线计划，经设计采购制造或整套委外、装配试模、交付验收，到财务结算、归档和最终关闭的全生命周期位置；先总览再逐层展开，不执行 ERP 动作。
---

# 项目全生命周期协调

1. 先调用 `query_project_lifecycle_context`，只用本轮项目 ID、编号或名称定位唯一项目；多候选时要求用户选择，不能合并项目事实。
2. 先说明 `analysis.project_lifecycle.current_segment`、三个分段摘要、资料矛盾和权限缺口。总览只决定当前应展开哪一段，不替代分段或专用工具的正式证据。
3. 用户继续核对时，只调用总览返回的一个分段协调器：启动用 `query_project_kickoff_context`，执行用 `query_project_execution_context`，收尾用 `query_project_completion_context`；项目暂停时先用 `query_project_control_context`。
4. 分段协调器返回下一阶段能力后，才按需继续一轮工具调用。不要一次搜索、启用或调用全部业务工具，也不要把不同阶段的摘要拼成写入参数。
5. `UNAVAILABLE` 代表能力或权限不可见，不能当成“未发生”；后续事实不能倒推承接、开工、计划或前序审批已经完成。状态与证据冲突时如实说明并请求核对。
6. 本 Skill 全程只读。用户明确要求办理时，必须切换到对应专用 Skill，重新读取真实 ID、版本、材料和审批流程；任何 `prepare_*` 都须本人确认，领域应用回执才表示事实生效。
