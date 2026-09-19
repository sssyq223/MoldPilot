from collections import defaultdict
from datetime import date
from typing import Literal
from pydantic import Field, ValidationError, model_validator
from fastapi import APIRouter, Depends
from sqlalchemy import select, and_
from domain_packs.mold import models as m, domains, domain_schemas as s, workflow_selection
from domain_packs.mold.authorization import access, predicate, require, select_fields, fingerprint
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.db import get_db, now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.security import current_user
from domain_packs.mold.ports.schemas import StrictModel


class StartReadinessInput(StrictModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    identifier: str | None = Field(default=None, min_length=1, max_length=200,
        description='项目编号/名称、承接单号、开工通知单号、合同号、模具号等。')

    @model_validator(mode='after')
    def one_locator(self):
        if bool(self.project_id)==bool(self.identifier):
            raise ValueError('project_id 和 identifier 须且只能填写一项')
        if self.identifier:
            self.identifier=self.identifier.strip()
            if not self.identifier:raise ValueError('线索不能为空')
        return self


class StartProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36,
        description='query_internal_start_readiness 返回的真实 project_id。')
    project_version: int = Field(ge=1,
        description='query_internal_start_readiness 返回的项目 row_version。')
    source_subject_id: str = Field(min_length=1, max_length=36,
        description='query_internal_start_readiness 返回的已生效承接记录 ID。')
    bid_intake_revision_id: str = Field(min_length=1, max_length=36,
        description='query_internal_start_readiness 返回的当前中标接收版本 ID。')
    execution_mode: Literal['INTERNAL','FULL_OUTSOURCE'] | None = Field(default=None,
        description='最终加工方式；未填时沿用承接记录中的 execution_mode。')
    effective_date: date = Field(description='正式内部开工生效日期。')
    evidence: str = Field(min_length=1, max_length=4000,
        description='客户开工通知、工艺方案确认或项目负责人下达依据。')
    expected_contract_date: date | None = Field(default=None,
        description='销售合同尚未到达时必须填写的预计到达日期；合同晚到不阻塞已满足条件的开工。')
    workflow_definition_id: str = Field(min_length=1, max_length=36,
        description='query_internal_start_readiness 返回或管理员配置的正式开工审批流程 ID。')


def _strength(value,needle):
    if value is None:return 0
    value=str(value).casefold();needle=str(needle).casefold()
    return 100 if value==needle else 50 if needle in value else 0


def _project_card(db,user,project,matched_by=()):
    fields=access(db,user,'project.read',{'project_id':project.id}).fields
    card=select_fields({'id':project.id,'code':project.code,'name':project.name,'status':project.status,
                        'row_version':project.row_version},fields)
    card['matched_by']=sorted(set(matched_by))
    return card


def _visible_projects(db,user):
    gate=and_(predicate(db,user,'project.read',{'project_id':m.Project.id}),
              predicate(db,user,'internal_start.read',{'project_id':m.Project.id}))
    rows=list(db.scalars(select(m.Project).where(gate).order_by(m.Project.code).limit(501)))
    return rows[:500],len(rows)>500


def _visible_subjects(db,user,project_ids,kind,allowed_tools):
    if kind=='internal_start':
        if 'query_internal_start_readiness' not in allowed_tools and 'query_internal_start' not in allowed_tools:return []
    elif 'query_'+kind not in allowed_tools:return []
    from domain_packs.mold.erp.core.domains import data as subject_data
    result=[]
    for subject in db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id.in_(project_ids),
        m.BusinessSubject.kind==kind).order_by(m.BusinessSubject.created_at.desc(),m.BusinessSubject.id).limit(501)):
        try:result.append(subject_data(db,user,subject))
        except DomainError:continue
    return result[:500]


def _resolve(db,user,data:StartReadinessInput,allowed_tools:set[str]):
    visible,truncated=_visible_projects(db,user)
    by_id={project.id:project for project in visible}
    if data.project_id:
        project=by_id.get(data.project_id)
        return project,([] if project else None),truncated
    scores=defaultdict(int);reasons=defaultdict(list)
    def add(project_id,value,label):
        if project_id not in by_id:return
        score=_strength(value,data.identifier)
        if score:
            scores[project_id]=max(scores[project_id],score)
            reasons[project_id].append(label)
    for project in visible:
        add(project.id,project.id,'项目ID');add(project.id,project.code,'项目编号');add(project.id,project.name,'项目名称')
    if by_id:
        for kind,label in (('internal_start','开工通知'),('quote_acceptance','承接决定'),
                           ('sales_contract','销售合同'),('full_outsource_contract','整套委外合同')):
            for subject in _visible_subjects(db,user,list(by_id),kind,allowed_tools):
                detail=subject.get('detail') if isinstance(subject.get('detail'),dict) else {}
                add(subject.get('project_id'),subject.get('id'),label+'ID')
                add(subject.get('project_id'),subject.get('number'),label+'业务单号')
                if kind in {'sales_contract','full_outsource_contract'}:
                    add(subject.get('project_id'),detail.get('contract_number'),label+'号')
        if 'query_business_object_candidates' in allowed_tools:
            from domain_packs.mold.erp.core.business_matching import BusinessMatchInput,query as match_query
            matches=match_query(db,user,BusinessMatchInput(identifier=data.identifier),allowed_tools)
            for row in matches.get('data',[]):
                project=row.get('project',{})
                project_id=project.get('id')
                if project_id in by_id:
                    scores[project_id]=max(scores[project_id],100 if row.get('match_quality')=='EXACT' else 50)
                    reasons[project_id].extend('候选匹配：'+e.get('label','线索') for e in row.get('evidence',[])[:5])
    if not scores:return None,[],truncated
    best=max(scores.values());ids=[pid for pid,score in scores.items() if score==best]
    if len(ids)!=1:return None,[_project_card(db,user,by_id[pid],reasons[pid]) for pid in ids[:20]],truncated
    return by_id[ids[0]],reasons[ids[0]],truncated


def _project_profile(db,user,project_id):
    fields=access(db,user,'project.read',{'project_id':project_id}).fields
    profile=db.get(m.ProjectProfile,project_id)
    if not profile:return None
    return select_fields({'customer_id':profile.customer_id,
        'execution_mode':profile.execution_mode,
        'customer_due_date':profile.customer_due_date.isoformat() if profile.customer_due_date else None,
        'settlement_status':profile.settlement_status},fields | {'execution_mode','customer_due_date','settlement_status'})


def _records(db,user,project_id,allowed_tools):
    data={}
    for kind in ('quote_acceptance','internal_start','sales_contract','full_outsource_contract','project_plan','plan_change'):
        rows=_visible_subjects(db,user,[project_id],kind,allowed_tools)
        data[kind]=rows[:20]
    return data


def workflow_options(db,user,project):
    scope={'project_id':project.id}
    require(db,user,'internal_start.read',scope)
    require(db,user,'internal_start.submit',scope)
    rows=db.scalars(select(m.WorkflowDefinition).where(m.WorkflowDefinition.status=='PUBLISHED').order_by(
        m.WorkflowDefinition.process_key,m.WorkflowDefinition.version.desc()))
    result=[]
    for row in rows:
        if not workflow_selection.matches(row.config,{'business_type':'internal_start','categories':set(),'design_type':None}):continue
        if row.config.get('material_contract') is not None:continue
        result.append(workflow_selection.metadata(row,db))
    return result


def _latest_effective(records,kind,decision=None):
    for row in records.get(kind,[]):
        detail=row.get('detail') if isinstance(row.get('detail'),dict) else {}
        if row.get('status')=='EFFECTIVE' and (decision is None or detail.get('decision')==decision):
            return row
    return None


def _open_records(records,kind):
    return [row for row in records.get(kind,[]) if row.get('status') in {'DRAFT','SUBMITTED','RETURNED','REJECTED','APPLY_BLOCKED'}]


def _readiness(project,records,allowed_tools,start_conditions):
    blockers=[];warnings=[];hints=[]
    latest_accept=_latest_effective(records,'quote_acceptance','ACCEPT')
    latest_reject=_latest_effective(records,'quote_acceptance','REJECT')
    latest_start=_latest_effective(records,'internal_start','START')
    if latest_reject:blockers.append('当前可见范围存在有效拒单记录，不能据此准备正式开工。')
    if 'query_quote_acceptance' not in allowed_tools and 'query_quote_acceptance_context' not in allowed_tools:
        warnings.append('未分配报价承接查询工具，不能核对承接依据。')
    elif not latest_accept:blockers.append('当前可见范围未见有效承接记录。')
    blockers.extend(start_conditions.get('blockers') or [])
    if latest_start:
        hints.append('当前可见范围已有有效正式开工通知，后续应核对计划审批和执行状态。')
    elif project.status=='DRAFT':
        hints.append('项目仍为未开工状态；若承接依据和客户开工条件均已人工确认，可准备正式开工申请。')
    elif project.status=='ACTIVE':
        warnings.append('项目已处于执行中，但当前可见范围未见有效正式开工通知，请核对历史开工依据。')
    elif project.status in {'CLOSED','TERMINATED'}:
        blockers.append('项目已关闭或终止，不应准备普通正式开工。')
    elif project.status=='PAUSED':
        blockers.append('项目已暂停，须先按暂停恢复流程处理。')
    if not records.get('sales_contract'):
        warnings.append('当前可见范围未见销售合同；合同晚到不必然阻塞开工，但需保留开工依据和后续补合同核对。')
    if records.get('project_plan') or records.get('plan_change'):
        hints.append('已存在计划记录；计划审批与正式开工仍须分别核对。')
    else:
        warnings.append('当前可见范围未见项目计划；正式开工后仍需按项目计划审批结果执行。')
    return {'known_blockers':blockers,'warnings':warnings,'hints':hints,
        'can_prepare_start_from_known_facts':bool(latest_accept) and bool(start_conditions.get('complete')) and not blockers and not latest_start and project.status=='DRAFT',
        'has_effective_acceptance':bool(latest_accept),
        'has_effective_rejection':bool(latest_reject),
        'has_effective_internal_start':bool(latest_start),
        'project_status':project.status}


START_STATE_SEQUENCE = (
    ("AWAITING_ACCEPTANCE", "待承接确认"),
    ("ACCEPTED_AWAITING_START_CONDITIONS", "已承接待开工条件"),
    ("AWAITING_FORMAL_ISSUE", "待正式下达"),
    ("FORMALLY_ISSUED", "已正式下达"),
    ("AWAITING_PLAN_APPROVAL", "待计划审批"),
    ("EXECUTING", "执行中"),
)


def _business_state(project, records, start_conditions):
    latest_accept = _latest_effective(records, "quote_acceptance", "ACCEPT")
    latest_reject = _latest_effective(records, "quote_acceptance", "REJECT")
    latest_start = _latest_effective(records, "internal_start", "START")
    plans = records.get("project_plan", []) + records.get("plan_change", [])
    effective_plan = next(
        (row for row in plans if row.get("status") == "EFFECTIVE"), None
    )
    pending_plan = next(
        (
            row
            for row in plans
            if row.get("status")
            in {"DRAFT", "SUBMITTED", "RETURNED", "APPLY_BLOCKED"}
        ),
        None,
    )

    if latest_reject and not latest_accept:
        return {
            "key": "REJECTED",
            "name": "已拒单结束",
            "reason": "当前有效承接决定为拒单；原因保留在独立拒单材料中。",
            "sequence": [
                {"key": key, "name": name, "status": "NOT_APPLICABLE"}
                for key, name in START_STATE_SEQUENCE
            ],
        }
    if project.status == "PAUSED":
        key, reason = "PAUSED", "项目已暂停，恢复后继续按正式开工和计划事实执行。"
    elif project.status == "TERMINATED":
        key, reason = "TERMINATED", "项目已终止，后续按终止结算与关闭流程处理。"
    elif project.status == "CLOSED":
        key, reason = "CLOSED", "项目已关闭。"
    elif effective_plan and latest_start:
        key, reason = "EXECUTING", "正式开工与生效项目计划均已具备。"
    elif pending_plan and latest_start:
        key, reason = "AWAITING_PLAN_APPROVAL", "正式开工已生效，项目计划尚未生效。"
    elif latest_start:
        key, reason = "FORMALLY_ISSUED", "正式开工已生效，下一步应准备项目计划审批。"
    elif latest_accept and start_conditions.get("complete"):
        key, reason = "AWAITING_FORMAL_ISSUE", "承接与客户开工条件齐备，尚待正式下达。"
    elif latest_accept:
        key, reason = (
            "ACCEPTED_AWAITING_START_CONDITIONS",
            "承接已生效，客户工艺确认或外部开工条件尚未齐备。",
        )
    else:
        key, reason = "AWAITING_ACCEPTANCE", "尚未见有效承接决定。"

    index = {item[0]: position for position, item in enumerate(START_STATE_SEQUENCE)}
    current_index = index.get(key)
    sequence = []
    for position, (state_key, name) in enumerate(START_STATE_SEQUENCE):
        if current_index is None:
            status = "SUSPENDED" if state_key == "EXECUTING" else "RECORDED"
        elif position < current_index:
            status = "DONE"
        elif position == current_index:
            status = "CURRENT"
        else:
            status = "PENDING"
        sequence.append({"key": state_key, "name": name, "status": status})
    return {"key": key, "name": dict(START_STATE_SEQUENCE).get(key, key),
            "reason": reason, "sequence": sequence}


def query(db,user,data:StartReadinessInput,allowed_tools:set[str]):
    project,alternatives,truncated=_resolve(db,user,data,allowed_tools)
    limitations=['只读取当前用户可见且具备正式开工读取权限的项目。',
                 '承接、合同和计划上下文仅在对应查询工具及业务权限可用时返回。',
                 '本工具只做正式开工条件核对，不创建开工通知、不下达任务、不执行采购/生产/装配。']
    if truncated:limitations.append('最多检查前500个可见项目，结果可能未覆盖全部可见范围。')
    if project:
        records=_records(db,user,project.id,allowed_tools)
        from domain_packs.mold.tools.erp.commercial.bid_intake_tools import start_condition_snapshot
        from domain_packs.mold.erp.project import start_dispatches, start_materials
        start_conditions=start_condition_snapshot(db,user,project.id)
        latest_start=_latest_effective(records,'internal_start','START')
        skipped=[]
        if 'query_quote_acceptance' not in allowed_tools and 'query_quote_acceptance_context' not in allowed_tools:skipped.append('承接依据')
        if 'query_sales_contract' not in allowed_tools:skipped.append('销售合同')
        if 'query_project_plan' not in allowed_tools and 'query_plan_change' not in allowed_tools:skipped.append('项目计划')
        if skipped:limitations.append('未分配对应查询工具，无法返回：'+'、'.join(skipped))
        workflows=[]
        if 'prepare_internal_start' in allowed_tools:
            try:workflows=workflow_options(db,user,project)
            except DomainError as error:limitations.append('当前人员缺少正式开工提交权限，未返回可选开工审批流程：'+error.message)
        start_material = (
            start_materials.card(db, latest_start.get('id'))
            if latest_start and start_conditions.get('visible') else None
        )
        contract_visible = 'query_sales_contract' in allowed_tools
        contract_follow_up = start_materials.contract_follow_up(
            db, project.id, latest_start.get('id') if latest_start else None,
            records['sales_contract'] if contract_visible else None,
        )
        department_handoffs = start_dispatches.summary(
            db, latest_start.get('id') if latest_start else None
        )
        finance_handoff = next(
            (item for item in department_handoffs.get('items', [])
             if item.get('role_key') == 'FINANCE_OWNER'),
            None,
        )
        model_context = {
            'project': {
                'code': project.code,
                'name': project.name,
                'status': project.status,
            },
            'formal_start': {
                'exists': bool(latest_start),
                'number': latest_start.get('number') if latest_start else None,
                'status': latest_start.get('status') if latest_start else None,
                'effective_date': (
                    (latest_start.get('detail') or {}).get('effective_date')
                    if latest_start else None
                ),
            },
            'frozen_start_material': start_materials.frozen_material_model_context(
                start_material
            ),
            'contract_follow_up': start_materials.contract_follow_up_model_context(
                contract_follow_up
            ),
            'finance_handoff': {
                'overall_status': department_handoffs.get('status'),
                'delivery_state': finance_handoff.get('delivery_state') if finance_handoff else None,
                'notification_delivered': bool(
                    finance_handoff
                    and finance_handoff.get('delivery_state') == 'DELIVERED'
                ),
                # The current dispatch receipt proves notification delivery,
                # not that a human opened it or completed finance processing.
                'explicit_receipt_acknowledged': None,
                'recipient_count': finance_handoff.get('recipient_count') if finance_handoff else 0,
                'gaps': department_handoffs.get('gaps', []),
            },
        }
        return {'resolution':'RESOLVED','data':[{'project':_project_card(db,user,project,alternatives or ('项目定位',)),
            'profile':_project_profile(db,user,project.id),
            'quote_acceptance':records['quote_acceptance'],
            'latest_acceptance':_latest_effective(records,'quote_acceptance','ACCEPT'),
            'latest_rejection':_latest_effective(records,'quote_acceptance','REJECT'),
            'internal_starts':records['internal_start'],
            'latest_internal_start':latest_start,
            'open_start_requests':_open_records(records,'internal_start'),
            'sales_contracts':records['sales_contract'],
            'full_outsource_contracts':records['full_outsource_contract'],
            'plans':records['project_plan']+records['plan_change'],
            'customer_start_conditions':start_conditions,
            'readiness':_readiness(project,records,allowed_tools,start_conditions),
            'business_state':_business_state(project,records,start_conditions),
            'formal_start_material':start_material,
            'contract_follow_up':contract_follow_up,
            'department_handoffs':department_handoffs,
            'workflow_options':workflows}],
            'model_context':model_context,
            'source':'agent_db','as_of':now().isoformat(),'limitations':limitations}
    if alternatives is None:
        return {'resolution':'NOT_FOUND_OR_FORBIDDEN','data':[],'source':'agent_db','as_of':now().isoformat(),
                'limitations':limitations}
    if alternatives:
        return {'resolution':'MULTIPLE_CANDIDATES','data':alternatives,'source':'agent_db','as_of':now().isoformat(),
                'limitations':limitations+['线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。']}
    return {'resolution':'NOT_FOUND','data':[],'source':'agent_db','as_of':now().isoformat(),
            'limitations':limitations}


def start_schema():
    return StartProposalInput.model_json_schema()


def parse_start(arguments):
    try:return StartProposalInput.model_validate(arguments or {})
    except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','正式开工参数不完整或不符合要求：'+error.errors()[0]['msg']) from None


def _effective_start_exists(db,project_id):
    return db.scalar(select(m.BusinessSubject.id).where(m.BusinessSubject.project_id==project_id,
        m.BusinessSubject.kind=='internal_start',m.BusinessSubject.status=='EFFECTIVE').limit(1))


def preview_start(db,user,data:StartProposalInput):
    project=db.get(m.Project,data.project_id)
    if not project:raise DomainError('NOT_FOUND','项目不存在',404)
    scope={'project_id':project.id}
    require(db,user,'project.read',scope)
    require(db,user,'quote_acceptance.read',scope)
    require(db,user,'project.dossier.read',scope)
    require(db,user,'internal_start.read',scope)
    require(db,user,'internal_start.create',scope)
    require(db,user,'internal_start.submit',scope)
    if project.row_version!=data.project_version:
        raise DomainError('VERSION_CONFLICT','项目状态已变化，请重新查询后准备',409)
    if project.status!='DRAFT':
        raise DomainError('START_STATE','只有未开工项目可准备正式开工',409)
    if _effective_start_exists(db,project.id):
        raise DomainError('START_EXISTS','当前项目已有有效正式开工通知',409)
    source=domains.require_source(db,data.source_subject_id,project.id,{'quote_acceptance'})
    source_detail=db.get(m.BusinessDecisionDetail,source.id)
    if not source_detail or source_detail.decision!='ACCEPT':
        raise DomainError('NOT_ACCEPTED','前置承接记录不是有效承接决定',409)
    from domain_packs.mold.tools.erp.commercial.bid_intake_tools import start_condition_snapshot
    start_conditions=start_condition_snapshot(db,user,project.id)
    if data.bid_intake_revision_id!=start_conditions.get('current_revision_id'):
        raise DomainError('BID_INTAKE_VERSION_CONFLICT','中标接收资料已变化，请重新查询当前版本',409)
    if not start_conditions.get('complete'):
        raise DomainError(
            'START_CONDITIONS_MISSING',
            '客户正式开工条件尚不完整：'+'；'.join(start_conditions.get('blockers') or ['无法核对客户开工条件']),
            409,
        )
    execution_mode=data.execution_mode or source_detail.execution_mode
    detail=s.DecisionInput(source_subject_id=source.id,decision='START',execution_mode=execution_mode,
        effective_date=data.effective_date,evidence=data.evidence,amount=None,currency=None)
    options=workflow_options(db,user,project)
    selected=next((item for item in options if item['id']==data.workflow_definition_id),None)
    if not selected:raise DomainError('WORKFLOW_MISMATCH','审批模板不可用，请重新查询流程选项',409)
    sales_contracts=[];sales_contract_visible=True
    try:
        require(db,user,'sales_contract.read',scope)
        sales_contracts=list(db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id==project.id,
            m.BusinessSubject.kind=='sales_contract',m.BusinessSubject.status=='EFFECTIVE').order_by(
            m.BusinessSubject.created_at.desc(),m.BusinessSubject.id).limit(20)))
    except DomainError:
        sales_contract_visible=False
    from domain_packs.mold.erp.project import start_materials
    material=start_materials.build(
        db,project,data.bid_intake_revision_id,data.effective_date,
        data.expected_contract_date,contract_visibility=sales_contract_visible,
    )
    plans=[];plan_visible=True
    try:
        require(db,user,'project_plan.read',scope)
        plans.extend(db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id==project.id,
            m.BusinessSubject.kind=='project_plan').order_by(m.BusinessSubject.created_at.desc(),m.BusinessSubject.id).limit(20)))
    except DomainError:
        plan_visible=False
    try:
        require(db,user,'plan_change.read',scope)
        plans.extend(db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id==project.id,
            m.BusinessSubject.kind=='plan_change').order_by(m.BusinessSubject.created_at.desc(),m.BusinessSubject.id).limit(20)))
    except DomainError:
        plan_visible=False
    warnings=[]
    if not sales_contract_visible:warnings.append('当前人员未分配销售合同读取权限；不能在本提案中展示合同事实，合同核对需由有权人员处理。')
    elif not sales_contracts:warnings.append('当前可见范围未见有效销售合同；合同晚到不必然阻塞开工，但需保留本次开工依据并后续补合同核对。')
    if not plan_visible:warnings.append('当前人员未分配项目计划读取权限；正式开工后仍需由有权人员核对计划审批结果。')
    elif not plans:warnings.append('当前可见范围未见项目计划；正式开工后仍需按项目计划审批结果执行，不能直接下达采购、生产、装配或试模任务。')
    display={'操作':'正式内部开工通知',
        '项目':project.code+' · '+project.name,
        '项目版本':project.row_version,
        '承接依据':source.number+' · 第'+str(source.revision)+'版',
        '中标接收依据':'V'+str(start_conditions['current_version'])+' · '+start_conditions['current_revision_id'],
        '客户工艺方案':'已人工确认 · '+start_conditions['customer_process_confirmation_evidence'],
        '客户外部订单':start_conditions['external_order_number'],
        '客户及联系人':material['customer'],
        '客户模具号':material['customer_mold_number'] or '未填写',
        '机型或物料号':material['customer_model_or_material'] or '未填写',
        '内部模具号':[row['internal_number'] for row in material['internal_molds']],
        '业务类型':'已有模具设变' if material['processing_kind']=='MOLD_CHANGE' else '新模',
        '客户开工日期':start_conditions['external_start_date'],
        '客户交期':start_conditions['customer_due_date'],
        '客户开工通知附件':'已核对',
        '最终加工方式':execution_mode or '未登记（请核对承接记录）',
        '正式开工日期':data.effective_date.isoformat(),
        '开工依据':data.evidence,
        '销售合同':('未授权查看' if not sales_contract_visible else ('已见 '+str(len(sales_contracts))+' 条有效合同' if sales_contracts else '当前未见有效销售合同')),
        '合同预计到达':data.expected_contract_date.isoformat() if data.expected_contract_date else '已有合同或当前发起人无权核对',
        '项目计划':('未授权查看' if not plan_visible else ('已见 '+str(len(plans))+' 条计划/变更记录' if plans else '当前未见计划记录')),
        '审批流程':selected['name']+' · 第'+str(selected['version'])+'版',
        '注意事项':warnings or ['承接、合同、内部正式开工和项目计划仍是不同事实；审批生效前不会改变项目状态。'],
        '说明':'本人确认后仅创建正式开工通知材料并提交 Agent BPM；审批生效后项目才从未开工转为执行中，不自动下达 ERP 执行任务。'}
    return detail,display


def execute_start_tool(db,user,key,arguments,run=None):
    if key!='prepare_internal_start':raise DomainError('TOOL_UNKNOWN','工具未实现',403)
    data=parse_start(arguments)
    _,display=preview_start(db,user,data)
    from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
    proposal={'kind':'internal_start','action':'start','requires_approval':True,
        'input':data.model_dump(mode='json'),'display':display,
        'confirmation_policy':proposal_confirmation_policy(run,requires_approval=True)}
    return {'data':[],'source':'agent_proposal','as_of':now().isoformat(),'proposal':proposal,
        'limitations':['仅准备正式开工通知建议；本人确认后才创建业务材料并提交审批，审批生效前不改变项目状态或执行任务。']}


def source(db,user,step_id):
    from domain_packs.mold.tool_gateway import available_tools
    step=db.get(m.Step,step_id);run=db.get(m.Run,step.run_id) if step else None
    if not run or run.user_id!=user.id:raise DomainError('NOT_FOUND','操作建议不存在或无权访问',404)
    if run.status not in {'RUNNING','SUCCEEDED'}:raise DomainError('PROPOSAL_STOPPED','任务已停止，请重新准备操作',409)
    if run.security_version!=user.security_version or run.checkpoint.get('authorization_hash')!=fingerprint(db,user):
        raise DomainError('AUTHORIZATION_CHANGED','授权已变化，请重新准备操作',403)
    proposal=step.result.get('proposal')
    if step.tool not in available_tools(db,user) or step.tool!='prepare_internal_start' or not proposal:
        raise DomainError('TOOL_FORBIDDEN','操作能力不可用',403)
    return proposal


def validate_intent(db,user,payload):
    proposal=source(db,user,payload['step_id'])
    if content_hash(proposal)!=payload['proposal_hash']:raise DomainError('CONFIRMATION_INVALID','操作建议内容已变化',409)
    data=parse_start(proposal['input'])
    _,display=preview_start(db,user,data)
    if content_hash(display)!=content_hash(proposal['display']):
        raise DomainError('VERSION_CONFLICT','项目、承接依据或流程资料已变化，请重新准备',409)
    return proposal,data


def confirm(db,user,payload):
    from domain_packs.mold.ports.confirmation_policy import agent_permission_mode_from_proposal
    proposal,data=validate_intent(db,user,payload)
    detail,_=preview_start(db,user,data)
    subject=domains.create(db,user,s.SubjectInput(kind='internal_start',project_id=data.project_id,
        remark=data.evidence,detail=detail.model_dump(mode='json')))
    project=db.get(m.Project,data.project_id)
    try:
        require(db,user,'sales_contract.read',{'project_id':data.project_id})
        contract_visibility=True
    except DomainError:
        contract_visibility=False
    from domain_packs.mold.erp.project import start_materials
    material=start_materials.build(
        db,project,data.bid_intake_revision_id,data.effective_date,
        data.expected_contract_date,contract_visibility=contract_visibility,
    )
    start_materials.create(
        db,user,subject,data.bid_intake_revision_id,material,data.expected_contract_date,
    )
    from domain_packs.mold.tools.erp.commercial.bid_intake_tools import link_lifecycle_subject
    link_lifecycle_subject(
        db,user,data.project_id,subject,'INTERNAL_START',
        source_revision_id=data.bid_intake_revision_id,
    )
    from domain_packs.mold.erp.core.business import submit_subject
    submitted=submit_subject(db,user,subject.id,subject.revision,data.workflow_definition_id,
        agent_permission_mode=agent_permission_mode_from_proposal(proposal))
    return {'project_id':data.project_id,'subject_id':subject.id,'instance_id':submitted['instance_id'],
        'action':'start','status':'SUBMITTED'}


router=APIRouter()


@router.get('/api/internal-start-proposals/{step_id}')
def proposal_status(step_id:str,user=Depends(current_user),db=Depends(get_db)):
    source(db,user,step_id)
    intent=db.scalar(select(m.HumanIntent).where(m.HumanIntent.user_id==user.id,
        m.HumanIntent.action=='internal_start.execute',m.HumanIntent.resource_id==step_id,
        m.HumanIntent.receipt['status'].as_string()=='SUBMITTED').order_by(m.HumanIntent.created_at.desc()))
    return {'receipt':intent.receipt if intent else None}


@router.post('/api/internal-start-proposals/{step_id}/intent')
def intent(step_id:str,user=Depends(current_user),db=Depends(get_db)):
    from domain_packs.mold.erp.core.business import create_intent
    proposal=source(db,user,step_id)
    payload={'step_id':step_id,'proposal_hash':content_hash(proposal)}
    result=create_intent(db,user,'internal_start.execute',step_id,payload)
    result['display']=proposal['display']
    result['confirmation_policy']=proposal.get('confirmation_policy')
    db.commit();return result
