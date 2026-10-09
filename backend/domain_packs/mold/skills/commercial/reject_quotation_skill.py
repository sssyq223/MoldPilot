"""报价拒绝Skill - 封装完整的报价拒绝业务流程"""
from datetime import date
from agent_core.skill_base import skill, WorkflowAgent
from domain_packs.mold.tools.erp.commercial.quotation_rejection_tool import (
    RejectQuotationTool,
    RejectQuotationInput
)
from domain_packs.mold.tools.erp.commercial.query_quotation_rejection_tool import (
    QueryQuotationRejectionTool,
    QueryQuotationRejectionInput
)


@skill(
    name="reject_quotation",
    description="拒绝报价请求并记录原因",
    requires_approval=True
)
async def reject_quotation_skill(
    agent: WorkflowAgent,
    quotation_number: str,
    rejection_category: str,
    rejection_reason: str,
    decision_date: date | None = None,
    evidence: str | None = None
) -> dict:
    """
    拒绝报价Skill

    工作流程：
    1. 根据报价单号查询报价单信息
    2. 验证报价单状态（必须是可以拒绝的状态）
    3. 调用RejectQuotationTool记录拒绝
    4. 返回拒绝记录详情

    参数：
        quotation_number: 报价单号
        rejection_category: 拒绝类别
        rejection_reason: 拒绝原因详细说明
        decision_date: 决策日期（默认为今天）
        evidence: 证据材料（可选）

    返回：拒绝记录详情
    """
    from sqlalchemy import select
    from domain_packs.mold.erp.core.domain_models import BusinessSubject
    from domain_packs.mold.erp.commercial.quotation_models import REJECTION_CATEGORIES

    session = agent.session

    # 1. 查询报价单
    agent.log(f"查询报价单: {quotation_number}")
    quotation_subject = session.execute(
        select(BusinessSubject)
        .where(BusinessSubject.number == quotation_number)
    ).scalar_one_or_none()

    if not quotation_subject:
        raise ValueError(f"报价单不存在: {quotation_number}")

    # 2. 验证报价单状态
    agent.log(f"验证报价单状态: {quotation_subject.status}")
    if quotation_subject.status not in ['DRAFT', 'SUBMITTED']:
        raise ValueError(
            f"报价单状态为 {quotation_subject.status}，不能拒绝。"
            f"只有 DRAFT 或 SUBMITTED 状态的报价单可以拒绝。"
        )

    # 检查是否已经拒绝过
    existing_rejection = QueryQuotationRejectionTool(
        session=session,
        user_context=agent.user_context
    ).run(QueryQuotationRejectionInput(
        quotation_subject_id=quotation_subject.id
    ))

    if existing_rejection['total'] > 0:
        raise ValueError(f"报价单 {quotation_number} 已经被拒绝过")

    # 3. 记录拒绝
    agent.log(f"记录拒绝原因: {rejection_category}")
    rejection_tool = RejectQuotationTool(
        session=session,
        user_context=agent.user_context
    )

    rejection_result = rejection_tool.run(RejectQuotationInput(
        quotation_subject_id=quotation_subject.id,
        rejection_category=rejection_category,
        rejection_reason=rejection_reason,
        decision_date=decision_date or date.today(),
        evidence=evidence
    ))

    # 4. 更新报价单状态为REJECTED
    quotation_subject.status = 'REJECTED'
    session.flush()

    agent.log(f"报价拒绝完成: {rejection_result['rejection_id']}")

    return {
        'success': True,
        'rejection_id': rejection_result['rejection_id'],
        'quotation_number': quotation_number,
        'rejection_category': rejection_category,
        'rejection_reason': rejection_reason,
        'decision_date': rejection_result['decision_date'],
        'message': f"报价单 {quotation_number} 已被拒绝"
    }


@skill(
    name="list_quotation_rejections",
    description="查询报价拒绝记录列表",
    requires_approval=False
)
async def list_quotation_rejections_skill(
    agent: WorkflowAgent,
    rejection_category: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
    offset: int = 0
) -> dict:
    """
    查询报价拒绝记录列表

    参数：
        rejection_category: 按类别筛选（可选）
        date_from: 起始日期（可选）
        date_to: 结束日期（可选）
        limit: 返回数量限制
        offset: 分页偏移量

    返回：拒绝记录列表
    """
    query_tool = QueryQuotationRejectionTool(
        session=agent.session,
        user_context=agent.user_context
    )

    result = query_tool.run(QueryQuotationRejectionInput(
        rejection_category=rejection_category,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset
    ))

    agent.log(f"查询到 {result['total']} 条拒绝记录")

    return result
