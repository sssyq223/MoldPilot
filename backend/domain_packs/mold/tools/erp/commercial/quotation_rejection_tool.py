"""报价拒绝工具 - 记录不接受的报价请求"""
from datetime import date
from pydantic import Field
from sqlalchemy import select

from agent_core.tool_base import SessionTool, StrictModel
from domain_packs.mold.erp.commercial.quotation_models import QuotationRejection, REJECTION_CATEGORIES
from domain_packs.mold.erp.core.domain_models import BusinessSubject
from domain_packs.mold.tools.erp.core.subject_tools import CreateSubjectTool, CreateSubjectInput


class RejectQuotationInput(StrictModel):
    """拒绝报价输入"""
    quotation_subject_id: str = Field(description="报价单业务主体ID")
    rejection_category: str = Field(description=f"拒绝类别，可选值：{', '.join(REJECTION_CATEGORIES)}")
    rejection_reason: str = Field(description="拒绝原因详细说明")
    decision_date: date = Field(description="决策日期")
    evidence: str | None = Field(None, description="证据材料（可选）")


class RejectQuotationTool(SessionTool):
    """拒绝报价Tool - 记录不接受的报价请求"""

    def run(self, input: RejectQuotationInput) -> dict:
        """
        拒绝报价并记录原因

        工作流程：
        1. 验证报价单存在
        2. 验证拒绝类别有效
        3. 创建business_subject记录
        4. 创建quotation_rejection记录
        5. 返回拒绝记录详情
        """
        session = self.session

        # 1. 验证报价单存在
        quotation_subject = session.execute(
            select(BusinessSubject).where(BusinessSubject.id == input.quotation_subject_id)
        ).scalar_one_or_none()

        if not quotation_subject:
            raise ValueError(f"报价单不存在: {input.quotation_subject_id}")

        # 验证是报价类型的业务主体
        if not quotation_subject.kind.startswith('QUOTATION'):
            raise ValueError(f"业务主体不是报价类型: {quotation_subject.kind}")

        # 2. 验证拒绝类别
        if input.rejection_category not in REJECTION_CATEGORIES:
            raise ValueError(
                f"无效的拒绝类别: {input.rejection_category}，"
                f"有效类别：{', '.join(REJECTION_CATEGORIES)}"
            )

        # 3. 创建business_subject记录
        create_subject_tool = CreateSubjectTool(session=session, user_context=self.user_context)
        rejection_subject = create_subject_tool.run(CreateSubjectInput(
            kind='QUOTATION_REJECTION',
            project_id=quotation_subject.project_id,
            category=input.rejection_category,
            remark=f"拒绝报价：{quotation_subject.number}"
        ))

        # 4. 创建quotation_rejection记录
        rejection = QuotationRejection(
            subject_id=rejection_subject['id'],
            quotation_subject_id=input.quotation_subject_id,
            rejection_category=input.rejection_category,
            rejection_reason=input.rejection_reason,
            decision_date=input.decision_date,
            decided_by=self.user_context.user_id,
            evidence=input.evidence
        )

        session.add(rejection)
        session.flush()

        # 5. 返回拒绝记录详情
        return {
            'rejection_id': rejection.subject_id,
            'quotation_subject_id': rejection.quotation_subject_id,
            'quotation_number': quotation_subject.number,
            'rejection_category': rejection.rejection_category,
            'rejection_reason': rejection.rejection_reason,
            'decision_date': rejection.decision_date.isoformat(),
            'decided_by': rejection.decided_by,
            'evidence': rejection.evidence,
            'created_at': rejection.created_at.isoformat() if rejection.created_at else None
        }
