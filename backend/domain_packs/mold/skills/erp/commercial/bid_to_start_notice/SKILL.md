# 中标到内部开工通知草稿

## 触发前置

人工确认文件类型为 `BID_NOTICE` 后，由系统通过本 Skill 的受信任事件 Tool 自动触发；不得要求用户再发送聊天指令。只消费已经确认的 `bid_notice.confirmed` 事件。事件必须同时带有
`file_version_id`、`classification_id`、`inbound_record_id`、证据快照、
`classifier_version` 和 `operation_id`；缺任一项只返回结构化缺项，不继续执行。

## 步骤

1. 读取已确认的中标来源和页级证据，按项目、报价和历史模具查询生成候选；中标邮件字段必须保留页码、来源文字块和置信度，不能把中标金额当成正式合同金额。
2. 自动调用事件 Tool 创建一条仅供超级管理员处理的内部开工通知草稿；重复事件复用原草稿。
3. 项目部（当前由超级管理员兼任）调用 `prepare_admin_start_notice_update` 补充资料；再调用 `prepare_admin_start_department_dispatch` 把通知单分发给选定部门（设计/采购/制造/装配/财务），只创建送达记录，不代填回执。
4. 部门（当前同样由超级管理员兼任）通过 `prepare_admin_start_department_ack` 逐部门确认收到；项目部用 `query_admin_start_notices` 查看哪些部门已收到、哪些未收到；回执状态仅展示，不阻塞最终决定。
5. 项目部最后调用 `prepare_admin_start_notice_decision` 人工确认内部承接、整套委外或拒绝；未分发部门或未全部回执不阻止决定，但决定结果会附带回执快照留痕。
6. 承接/委外确认后状态进入 `READY_FOR_CONTRACT_MATCH`；拒绝进入 `REJECTED`，所有分发、回执、决定和版本追加留痕。
7. 合同 OCR/接收完成后自动调用合同匹配 Tool 生成候选；正式合同关联仍需超级管理员人工确认，不得静默绑定。
8. 旧的项目匹配、`BidIntakeRevision` 和正式 `StartNotice` 链路只在后续资料齐备后使用，不由文档类型确认直接创建。
9. 多候选不得自动选择；自动候选不等于人工决定或正式合同绑定。项目和内部模具必须从当前可见的真实项目候选中选择，不能手填或猜测内部 ID。

## 幂等与失败

同一文件版本、内容哈希和来源事件复用原 `inbound_record` 与草稿。未知回执先查询
`operation_id`；OCR/模型/事件发布失败进入 `NEEDS_REVIEW`，保留原始文件和中间证据，
不得重复产生业务副作用。
