"""查询报价拒绝记录工具"""
from datetime import date
from pydantic import Field
from sqlalchemy import select, and_

from agent_core.tool_base import SessionTool, StrictModel
from domain_packs.mold.erp.commercial.quotation_models import QuotationRejection
from domain_packs.mold.erp.core.domain_models import BusinessSubject


class QueryQuotationRejectionInput(StrictModel):
    """查询报价拒绝输入"""
    quotation_subject_id: str | None = Field(None, description="按报价单业务主体ID查询")
    rejection_category: str | None = Field(None, description="按拒绝类别查询")
    date_from: date | None = Field(None, description="起始日期（包含）")
    date_to: date | None = Field(None, description="结束日期（包含）")
    limit: int = Field(50, description="返回记录数量限制，默认50")
    offset: int = Field(0, description="分页偏移量，默认0")


class QueryQuotationRejectionTool(SessionTool):
    """查询报价拒绝Tool - 查询符合条件的拒绝记录"""

    def run(self, input: QueryQuotationRejectionInput) -> dict:
        """
        查询报价拒绝记录

        返回：
        {
            'total': 总记录数,
            'records': [拒绝记录列表],
            'limit': 限制数量,
            'offset': 偏移量
        }
        """
        session = self.session

        # 构建查询条件
        conditions = []

        if input.quotation_subject_id:
            conditions.append(QuotationRejection.quotation_subject_id == input.quotation_subject_id)

        if input.rejection_category:
            conditions.append(QuotationRejection.rejection_category == input.rejection_category)

        if input.date_from:
            conditions.append(QuotationRejection.decision_date >= input.date_from)

        if input.date_to:
            conditions.append(QuotationRejection.decision_date <= input.date_to)

        # 查询总数
        count_query = select(QuotationRejection)
        if conditions:
            count_query = count_query.where(and_(*conditions))
        total = session.execute(count_query).scalars().all()
        total_count = len(total)

        # 查询记录（带分页）
        query = (
            select(QuotationRejection, BusinessSubject)
            .join(BusinessSubject, QuotationRejection.quotation_subject_id == BusinessSubject.id)
            .order_by(QuotationRejection.decision_date.desc())
            .limit(input.limit)
            .offset(input.offset)
        )

        if conditions:
            query = query.where(and_(*conditions))

        results = session.execute(query).all()

        # 构建返回数据
        records = []
        for rejection, quotation_subject in results:
            records.append({
                'rejection_id': rejection.subject_id,
                'quotation_subject_id': rejection.quotation_subject_id,
                'quotation_number': quotation_subject.number,
                'rejection_category': rejection.rejection_category,
                'rejection_reason': rejection.rejection_reason,
                'decision_date': rejection.decision_date.isoformat(),
                'decided_by': rejection.decided_by,
                'evidence': rejection.evidence,
                'created_at': rejection.created_at.isoformat() if rejection.created_at else None,
                'row_version': rejection.row_version
            })

        return {
            'total': total_count,
            'records': records,
            'limit': input.limit,
            'offset': input.offset
        }
