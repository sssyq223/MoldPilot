"""Mold product assembly: host metadata, routes and presentation policy."""
import re


PUBLIC_METADATA = {
    "id": "mold",
    "product_name": "MoldPilot",
    "display_name": "模具项目智能工作台",
    "tagline": "从一个任务开始，让业务能力协同工作。",
    "attachment_run": {"enabled": False, "media_types": ["application/pdf", "image/png", "image/jpeg"]},
    "attachment_processing": {"enabled": True, "batch_upload": True, "media_types": ["application/pdf", "image/png", "image/jpeg", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]},
    "workspace_tabs": [
        {"key": "approvals", "name": "审批材料", "hint": "查看待审批事项、节点和依据"},
        {"key": "contacts", "name": "联络单材料", "hint": "查看工程联络单、附件和协作进度"},
    ],
    "proposal_presentation": {
        "action_prefixes": ["登记", "确认", "准备"],
        "action_suffixes": ["证据登记", "证据"],
        "detail_links": {
            "contact": {
                "target": "contacts",
                "receipt_field": "case_id",
                "label": "查看材料",
            },
        },
        "value_names": {
            "supplier_design": "供应商设计",
            "supplier_purchase": "供应商采购",
            "supplier_production": "供应商生产",
            "supplier_quality": "供应商质检",
            "supplier_assembly": "供应商装配",
            "supplier_trial": "供应商试模",
            "supplier_acceptance": "供应商验收",
            "ON_TRACK": "正常推进",
            "AT_RISK": "存在风险",
            "BLOCKED": "已阻塞",
            "DONE": "已完成",
            "REWORK": "返工中",
            "INITIAL": "初次验收",
            "RECHECK": "整改复验",
            "PASSED": "通过",
            "FAILED": "未通过",
            "CONDITIONALLY_PASSED": "有条件通过",
            "SUPPLIER": "供应商",
            "CUSTOMER": "客户",
            "SHARED": "共同责任",
            "UNKNOWN": "未确定",
            "MANUAL": "手工录入",
            "IMPORT": "导入",
            "ERP": "ERP 同步",
            "EMAIL": "邮件",
            "OTHER": "其他",
        },
    },
}
APP_TITLE = "模具工作台 · 独立 Agent"


def conversation_title(prompt: str) -> str:
    text = re.sub(r"\s+", " ", (prompt or "").strip())
    code = next((match.group(0) for match in re.finditer(
        r"\b[A-Z][A-Z0-9]+-[A-Z0-9-]+\b", text
    )), "")
    topic_rules = [
        (("正式开工", "开工"), "开工条件核对"),
        (("权限", "审计", "通知", "附件", "来源治理"), "权限治理核对"),
        (("财务", "收付款", "回款", "付款", "开票", "发票"), "财务节点核对"),
        (("客户验收", "出厂", "出库", "物流", "签收", "交付"), "交付物流核对"),
        (("中标", "客户分类", "承接", "拒单"), "中标接收核对"),
        (("报价", "成本", "工艺", "采购价格"), "报价评估核对"),
        (("合同", "销售合同", "整套委外合同"), "合同上下文核对"),
        (("项目计划", "节点", "逾期"), "项目计划核对"),
        (("设计", "BOM", "路线", "图纸"), "设计BOM核对"),
        (("制造", "质检", "检验", "报工"), "制造质检核对"),
        (("装配", "试模"), "装配试模核对"),
        (("采购订单", "采购价格", "采购"), "采购上下文核对"),
        (("项目业务档案", "业务档案"), "项目档案核对"),
        (("暂停", "恢复"), "暂停恢复核对"),
        (("终止", "关闭", "结项"), "项目关闭核对"),
    ]
    topic = next((name for keys, name in topic_rules if any(key in text for key in keys)), "")
    if code and topic:
        return f"{code} {topic}"[:80]
    if code:
        return f"{code} 查询"[:80]
    cleaned = re.sub(r"^(请|帮我|查询|核对|查看|分析)\s*", "", text)
    cleaned = re.sub(r"(请调用|调用).*$", "", cleaned).strip(" ，。；;")
    return (cleaned[:28] + "…") if len(cleaned) > 28 else (cleaned or "新对话")


def after_files_uploaded(db, user, blobs, request_key):
    """上传事务内登记识别作业，不通过 Agent 调度，不确认任何机器候选。"""
    from uuid import UUID, uuid5
    from domain_packs.mold.erp.commercial import contract_intake
    documents = [blob for blob in blobs if blob.media_type in {
        'application/pdf', 'image/png', 'image/jpeg',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    }]
    if documents:
        contract_intake.create(db, user, conversation_id=documents[0].conversation_id,
                              file_ids=[blob.id for blob in documents], request_key=str(uuid5(UUID(request_key), 'document-intake')))


def install(app, domain_router) -> None:
    """Install only the mold product's HTTP surface into the generic host."""
    app.include_router(domain_router)

    from domain_packs.mold.erp.change.contacts import router as contact_router
    from domain_packs.mold.erp.design.erp_design_upload import router as erp_design_upload_router
    from domain_packs.mold.erp.core.domain_api import install as install_domain_api
    from domain_packs.mold.tools.erp.change.contact_tools import router as contact_proposal_router
    from domain_packs.mold.tools.erp.project.project_control_tools import router as project_control_proposal_router
    from domain_packs.mold.tools.erp.project.project_closure_tools import router as project_closure_proposal_router
    from domain_packs.mold.tools.erp.project.plan_tools import router as project_plan_proposal_router
    from domain_packs.mold.tools.erp.project.start_tools import router as internal_start_proposal_router
    from domain_packs.mold.tools.erp.commercial.quote_tools import router as quote_acceptance_proposal_router
    from domain_packs.mold.tools.erp.commercial.quotation_tools import router as quotation_proposal_router
    from domain_packs.mold.tools.erp.commercial.bid_intake_tools import router as bid_intake_proposal_router
    from domain_packs.mold.tools.erp.commercial.contract_tools import router as contract_proposal_router
    from domain_packs.mold.tools.erp.finance.finance_context_tools import router as finance_proposal_router
    from domain_packs.mold.tools.erp.procurement.full_outsource_tools import router as full_outsource_proposal_router

    app.include_router(contact_router)
    app.include_router(erp_design_upload_router)
    from domain_packs.mold.erp.commercial.document_workflow_api import router as document_workflow_router
    app.include_router(document_workflow_router)
    # 中标到开工通知的领域模型对应 mb0d0e000014～mb0d0e000016 迁移。
    # 本地设变承接对应 mb0d0e000017；工作台入口走 Skill/Tool/Proposal，正式库迁移获批并落地前不执行这些领域写入。
    install_domain_api(app)
    for router in (
        contact_proposal_router,
        project_control_proposal_router,
        project_closure_proposal_router,
        project_plan_proposal_router,
        internal_start_proposal_router,
        quote_acceptance_proposal_router,
        quotation_proposal_router,
        bid_intake_proposal_router,
        contract_proposal_router,
        finance_proposal_router,
        full_outsource_proposal_router,
    ):
        app.include_router(router)
