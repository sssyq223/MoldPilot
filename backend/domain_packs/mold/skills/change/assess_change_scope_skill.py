"""设变规模评估Skill - 评估并标记工程设变的影响范围"""
from agent_core.skill_base import skill, WorkflowAgent
from domain_packs.mold.tools.erp.change.minor_change_tool import (
    MarkMinorChangeTool,
    MarkMinorChangeInput,
    QueryMinorChangeTool,
    QueryMinorChangeInput
)


@skill(
    name="assess_change_scope",
    description="评估工程设变的影响范围并标记是否为小范围设变",
    requires_approval=False
)
async def assess_change_scope_skill(
    agent: WorkflowAgent,
    change_number: str,
    problem_description: str,
    solution_description: str,
    auto_mark: bool = True
) -> dict:
    """
    设变规模评估Skill

    工作流程：
    1. 查询设变基本信息
    2. 分析问题和解决方案描述
    3. 评估影响的模具数量、工序数量、成本变化
    4. 根据评估结果判断是否为小范围设变
    5. 如果auto_mark=True，自动调用MarkMinorChangeTool标记
    6. 返回评估结果

    判断标准（满足任一条件即为小范围设变）：
    - 问题描述中明确提到"单个模具"、"局部"、"微调"等关键词
    - 解决方案仅涉及单一工序或单一部件
    - 描述中未提及"全部"、"所有"、"大规模"等关键词
    - 未影响客户交期（customer_due_affected=False）

    参数：
        change_number: 设变单号
        problem_description: 问题描述
        solution_description: 解决方案描述
        auto_mark: 是否自动标记（默认True）

    返回：评估结果和标记状态
    """
    from sqlalchemy import select
    from domain_packs.mold.erp.core.domain_models import BusinessSubject, EngineeringChangeDetail

    session = agent.session

    # 1. 查询设变信息
    agent.log(f"查询设变信息: {change_number}")
    change_subject = session.execute(
        select(BusinessSubject)
        .where(BusinessSubject.number == change_number)
    ).scalar_one_or_none()

    if not change_subject:
        raise ValueError(f"设变单不存在: {change_number}")

    change_detail = session.execute(
        select(EngineeringChangeDetail)
        .where(EngineeringChangeDetail.subject_id == change_subject.id)
    ).scalar_one_or_none()

    if not change_detail:
        raise ValueError(f"设变详情不存在: {change_number}")

    # 2. 分析关键词
    agent.log("分析问题和解决方案描述")

    # 小范围设变的关键词
    minor_keywords = [
        '单个', '局部', '微调', '小范围', '轻微', '个别',
        '一处', '一个', '部分', '少量', '简单'
    ]

    # 重大设变的关键词
    major_keywords = [
        '全部', '所有', '大规模', '整体', '全面', '批量',
        '多个', '大量', '重大', '根本', '彻底'
    ]

    # 统计关键词出现次数
    text = f"{problem_description} {solution_description}".lower()
    minor_score = sum(1 for kw in minor_keywords if kw in text)
    major_score = sum(1 for kw in major_keywords if kw in text)

    # 3. 综合评估
    evaluation_factors = []
    is_minor = False

    # 因素1: 关键词分析
    if minor_score > major_score:
        evaluation_factors.append("描述中包含小范围设变关键词")
        is_minor = True
    elif major_score > minor_score:
        evaluation_factors.append("描述中包含重大设变关键词")
        is_minor = False
    else:
        evaluation_factors.append("描述中未明确指出规模")

    # 因素2: 客户交期影响
    if not change_detail.customer_due_affected:
        evaluation_factors.append("不影响客户交期")
        # 不影响交期倾向于小范围设变
        if not is_minor and minor_score == major_score:
            is_minor = True
    else:
        evaluation_factors.append("影响客户交期")

    # 因素3: 问题描述长度（经验规则：描述越简短通常影响越小）
    if len(problem_description) < 100:
        evaluation_factors.append("问题描述简短")
    else:
        evaluation_factors.append("问题描述详细")

    # 4. 生成评估原因
    reason = f"评估依据: {'; '.join(evaluation_factors)}. "
    reason += f"关键词分析: 小范围{minor_score}个, 重大{major_score}个"

    agent.log(f"评估结果: {'小范围设变' if is_minor else '重大设变'}")
    agent.log(f"评估原因: {reason}")

    result = {
        'change_number': change_number,
        'change_subject_id': change_subject.id,
        'is_minor_change': is_minor,
        'evaluation_factors': evaluation_factors,
        'minor_keywords_count': minor_score,
        'major_keywords_count': major_score,
        'customer_due_affected': change_detail.customer_due_affected,
        'reason': reason,
        'marked': False
    }

    # 5. 自动标记
    if auto_mark:
        agent.log("自动标记设变规模")
        mark_tool = MarkMinorChangeTool(
            session=session,
            user_context=agent.user_context
        )

        mark_result = mark_tool.run(MarkMinorChangeInput(
            change_subject_id=change_subject.id,
            is_minor=is_minor,
            reason=reason
        ))

        result['marked'] = True
        result['mark_result'] = mark_result

    return result


@skill(
    name="mark_minor_change",
    description="手动标记工程设变为小范围或重大设变",
    requires_approval=True
)
async def mark_minor_change_skill(
    agent: WorkflowAgent,
    change_number: str,
    is_minor: bool,
    reason: str
) -> dict:
    """
    手动标记设变规模

    参数：
        change_number: 设变单号
        is_minor: 是否为小范围设变
        reason: 标记原因

    返回：标记结果
    """
    from sqlalchemy import select
    from domain_packs.mold.erp.core.domain_models import BusinessSubject

    session = agent.session

    # 查询设变
    agent.log(f"查询设变: {change_number}")
    change_subject = session.execute(
        select(BusinessSubject)
        .where(BusinessSubject.number == change_number)
    ).scalar_one_or_none()

    if not change_subject:
        raise ValueError(f"设变单不存在: {change_number}")

    # 标记
    agent.log(f"标记为: {'小范围设变' if is_minor else '重大设变'}")
    mark_tool = MarkMinorChangeTool(
        session=session,
        user_context=agent.user_context
    )

    result = mark_tool.run(MarkMinorChangeInput(
        change_subject_id=change_subject.id,
        is_minor=is_minor,
        reason=reason
    ))

    agent.log(f"标记完成: {result['change_number']}")

    return {
        'success': True,
        'change_number': change_number,
        'is_minor_change': is_minor,
        'reason': reason,
        'message': f"设变 {change_number} 已标记为{'小范围' if is_minor else '重大'}设变"
    }


@skill(
    name="list_minor_changes",
    description="查询小范围设变列表",
    requires_approval=False
)
async def list_minor_changes_skill(
    agent: WorkflowAgent,
    project_id: str | None = None,
    is_minor_only: bool = False,
    limit: int = 50,
    offset: int = 0
) -> dict:
    """
    查询设变列表（可筛选小范围设变）

    参数：
        project_id: 按项目ID筛选（可选）
        is_minor_only: 仅返回小范围设变（默认False）
        limit: 返回数量限制
        offset: 分页偏移量

    返回：设变记录列表
    """
    query_tool = QueryMinorChangeTool(
        session=agent.session,
        user_context=agent.user_context
    )

    result = query_tool.run(QueryMinorChangeInput(
        project_id=project_id,
        is_minor_only=is_minor_only,
        limit=limit,
        offset=offset
    ))

    agent.log(f"查询到 {result['total']} 条设变记录")

    return result
