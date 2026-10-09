"""设变小范围标识工具"""
from pydantic import Field
from sqlalchemy import select

from agent_core.tool_base import SessionTool, StrictModel
from domain_packs.mold.erp.core.domain_models import EngineeringChangeDetail, BusinessSubject


class MarkMinorChangeInput(StrictModel):
    """标记小范围设变输入"""
    change_subject_id: str = Field(description="设变业务主体ID")
    is_minor: bool = Field(description="是否为小范围设变")
    reason: str | None = Field(None, description="标记原因说明（可选）")


class MarkMinorChangeTool(SessionTool):
    """标记小范围设变Tool - 标识工程设变的影响范围"""

    def run(self, input: MarkMinorChangeInput) -> dict:
        """
        标记设变是否为小范围

        工作流程：
        1. 验证设变记录存在
        2. 更新is_minor_change字段
        3. 记录标记原因（如果提供）
        4. 返回更新结果
        """
        session = self.session

        # 1. 验证设变记录存在
        change_detail = session.execute(
            select(EngineeringChangeDetail)
            .where(EngineeringChangeDetail.subject_id == input.change_subject_id)
        ).scalar_one_or_none()

        if not change_detail:
            raise ValueError(f"设变记录不存在: {input.change_subject_id}")

        # 获取业务主体信息
        change_subject = session.execute(
            select(BusinessSubject)
            .where(BusinessSubject.id == input.change_subject_id)
        ).scalar_one_or_none()

        if not change_subject:
            raise ValueError(f"设变业务主体不存在: {input.change_subject_id}")

        # 验证是设变类型
        if not change_subject.kind.startswith('ENGINEERING_CHANGE'):
            raise ValueError(f"业务主体不是设变类型: {change_subject.kind}")

        # 2. 更新is_minor_change字段
        old_value = change_detail.is_minor_change
        change_detail.is_minor_change = 1 if input.is_minor else 0

        # 3. 如果提供了标记原因，更新到remark
        if input.reason:
            mark_info = f"标记为{'小范围' if input.is_minor else '重大'}设变: {input.reason}"
            if change_subject.remark:
                change_subject.remark += f"\n{mark_info}"
            else:
                change_subject.remark = mark_info

        session.flush()

        # 4. 返回更新结果
        return {
            'change_subject_id': input.change_subject_id,
            'change_number': change_subject.number,
            'is_minor_change': bool(change_detail.is_minor_change),
            'previous_value': bool(old_value),
            'changed': old_value != change_detail.is_minor_change,
            'reason': input.reason,
            'updated_by': self.user_context.user_id
        }


class QueryMinorChangeInput(StrictModel):
    """查询小范围设变输入"""
    project_id: str | None = Field(None, description="按项目ID筛选")
    is_minor_only: bool = Field(False, description="仅返回小范围设变")
    limit: int = Field(50, description="返回记录数量限制")
    offset: int = Field(0, description="分页偏移量")


class QueryMinorChangeTool(SessionTool):
    """查询小范围设变Tool - 查询带有规模标识的设变记录"""

    def run(self, input: QueryMinorChangeInput) -> dict:
        """
        查询设变记录及其规模标识

        返回：
        {
            'total': 总记录数,
            'records': [设变记录列表],
            'limit': 限制数量,
            'offset': 偏移量
        }
        """
        session = self.session

        # 构建查询
        query = (
            select(EngineeringChangeDetail, BusinessSubject)
            .join(BusinessSubject, EngineeringChangeDetail.subject_id == BusinessSubject.id)
        )

        # 添加筛选条件
        if input.project_id:
            query = query.where(BusinessSubject.project_id == input.project_id)

        if input.is_minor_only:
            query = query.where(EngineeringChangeDetail.is_minor_change == 1)

        # 查询总数
        total_results = session.execute(query).all()
        total_count = len(total_results)

        # 应用分页并排序
        query = query.order_by(BusinessSubject.created_at.desc())
        query = query.limit(input.limit).offset(input.offset)

        results = session.execute(query).all()

        # 构建返回数据
        records = []
        for change_detail, change_subject in results:
            records.append({
                'change_subject_id': change_detail.subject_id,
                'change_number': change_subject.number,
                'project_id': change_subject.project_id,
                'problem': change_detail.problem,
                'solution': change_detail.solution,
                'is_minor_change': bool(change_detail.is_minor_change),
                'customer_due_affected': change_detail.customer_due_affected,
                'status': change_subject.status,
                'created_by': change_subject.created_by,
                'created_at': change_subject.created_at.isoformat()
            })

        return {
            'total': total_count,
            'records': records,
            'limit': input.limit,
            'offset': input.offset
        }
