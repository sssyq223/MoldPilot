# ERP 能力复用与开发归属核对

> 后续确认：用户已允许复用现有 ERP 接口，正式操作必须人工确认；Agent 新增采购仅为辅材、办公用品、试模料。以下是较早的诊断记录，完整更新以 `ERP_SCOPE_AUDIT.md` 为准，不再等待接口复用原则的批准。

核对日期：2026-09-14。源码为用户提供的关联参考；没有修改、启动或写入 ERP。

## 已确认的偏差

本次开发的 `domains.py / procurement.py / domain_extensions.py` 中，采购价格审批、订单草稿、正式下单及后续收货是 Agent 本地实现，使用合成数据测试，并未调用 ERP。用户指出 ERP 已有价格审批与下单；该部分不能作为已经完成 ERP 复用的交付成果。

后续暂停扩大重复采购实现。保留现有代码与迁移记录用于核对，不能通过删除已执行迁移或清空数据库纠正归属。

## 已核实的 ERP 入口

源码基准：`work/erp_review_20260914/source/management-system-zhangwenjin/ruoyi-fastapi-backend`。

| 能力 | 已有入口 | 已观察到的鉴权与行为 |
|---|---|---|
| 采购分组查询 | GET `/purchase/decision/list`、`/{group_id}` | 登录身份、接口权限、责任品类和采购负责人范围校验 |
| 价格审批预览 | GET `/purchase/decision/{group_id}/quote-approval-preview` | 读取 ERP 权威报价审批材料，先校验分组访问权 |
| 五金报价提交审批 | POST `/purchase/decision/{group_id}/hardware-quote` | 需要 `purchase:decision:quoteApproval:submit`；校验采购处理人及审批人员 |
| 采购决策确认 | POST `/purchase/decision/{group_id}/confirm` | 需要 `purchase:decision:confirm`，核对分组处理人 |
| 正式生成采购订单 | POST `/purchase/decision/{group_id}/create-order` | 需要 `purchase:order:add`，核对处理人，经现有业务 Service 生成订单 |
| 采购入库 | `module_admin/service/material_inbound_service.py` | 已有订单来源、供应商、接单状态、数量与权威价格校验；接口契约待继续核对 |

上述入口来自 `module_admin/controller/purchase_decision_controller.py`，不是旧 Agent Tool/Skill。本项目不复用旧 Tool/Skill。

## 必须先统一的执行边界

V3.6 明确 ERP 原库只读、保留原事实；同时本项目要复用 ERP 已有能力。用户正在确认是否允许：独立人工确认后，由新受控适配器调用已有 ERP 业务接口，业务事实仍归 ERP。

在明确前，不开启 ERP 写入调用，不把本地采购状态当作 ERP 订单状态。查询数据归属目录与本项目新增 BPM、Harness、权限、消息等能力可以继续开发。

若允许接口执行，还须验证真实用户映射、双侧权限、现有 API 幂等保障、超时后的权威状态查询及对账。无法安全调用的现有接口应明确阻塞原因，不静默降级为本地建单，也不自动修改 ERP 接口。

## 需要逐项核对的已有/新增能力

其他本地试验实现（设计/BOM、计划、装配、试模、委外合同、库存、付款等）同样需要与 ERP 源码对照，先登记唯一业务归属，再决定复用接口、只读查询或本项目新增。不能凭“ERP 大概没有”继续重建。
