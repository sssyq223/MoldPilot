# 报价与承接上下文核对

版本：1.0.0，新项目独立编写。

当用户询问报价、承接、拒单、正式开工条件、销售合同是否已关联，或要求判断某项目是否已经承接时，先调用 `query_quote_acceptance_context`。

工具返回的是当前可见资料中的上下文，包括报价/承接决定、拒单记录、正式开工通知和销售合同摘要。只能依据返回的 `derived_status` 和业务单据状态说明“已见到/未见到”对应事实，不能把未查询到解释为企业不存在该资料；可能是未接入、未授权、未确认或线索不唯一。

承接、拒单、正式开工、合同补充都是独立业务动作。不要因为看到有效承接就声称项目已正式开工；不要因为看到合同就声称已经承接；不要因为销售合同晚到就阻止已按确认依据推进的开工条件核对。

如果用户明确要求承接或拒单，必须先调用 `query_quote_acceptance_context`。只有返回唯一项目、没有有效承接/拒单、没有待处理承接/拒单申请，并取得可用 `workflow_options.id` 时，才能调用 `prepare_quote_acceptance_decision`。

调用 `prepare_quote_acceptance_decision` 必须使用查询返回的真实 `project.id`、`project.row_version` 和流程 ID。承接时必须确认最终加工方式 `INTERNAL` 或 `FULL_OUTSOURCE`；拒单时不得填写加工方式。不得凭自然语言、截图或历史对话构造项目或流程。

`prepare_quote_acceptance_decision` 只生成待本人确认的 proposal。本人确认后才创建报价承接/拒单材料并提交 Agent BPM；审批生效前不正式承接、不拒单、不正式开工、不修改合同或项目状态。
