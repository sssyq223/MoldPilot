"""Permission-preserving project dossier queries for the conversation workbench."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pydantic import Field, model_validator
from sqlalchemy import select, and_
from domain_packs.mold import models as m
from domain_packs.mold.authorization import access, predicate, select_fields
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel


class ProjectDossierInput(StrictModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    identifier: str | None = Field(default=None, min_length=1, max_length=200,
        description='项目编号/名称、模具号、联络单、订单、合同或业务单据编号。')

    @model_validator(mode='after')
    def one_locator(self):
        if bool(self.project_id) == bool(self.identifier):
            raise ValueError('项目标识和业务编号须且只能填写一项')
        if self.identifier:
            self.identifier=self.identifier.strip()
            if not self.identifier:raise ValueError('业务编号不能为空')
        return self


def _project_card(db,user,project,matched_by=()):
    fields=access(db,user,'project.read',{'project_id':project.id}).fields
    card=select_fields({'id':project.id,'code':project.code,'name':project.name,'status':project.status},fields)
    card['matched_by']=sorted(set(matched_by))
    return card


def _strength(value,needle):
    if value is None:return 0
    value=str(value).casefold()
    return 2 if value==needle else 1 if needle in value else 0


def _resolve(db,user,data,allowed_tools):
    gate=and_(predicate(db,user,'project.read',{'project_id':m.Project.id}),
              predicate(db,user,'project.dossier.read',{'project_id':m.Project.id}))
    visible=list(db.scalars(select(m.Project).where(gate)
        .order_by(m.Project.code).limit(501)))
    truncated=len(visible)>500;visible=visible[:500]
    by_id={p.id:p for p in visible}
    if data.project_id:
        project=by_id.get(data.project_id)
        return project,([] if project else None),truncated
    needle=data.identifier.casefold();scores=defaultdict(int);reasons=defaultdict(list)
    def add(project_id,value,label):
        if project_id not in by_id:return
        score=_strength(value,needle)
        if score:
            scores[project_id]=max(scores[project_id],score)
            reasons[project_id].append(label)
    for project in visible:
        add(project.id,project.id,'项目ID');add(project.id,project.code,'项目编号');add(project.id,project.name,'项目名称')
    if by_id:
        for link,mold in db.execute(select(m.ProjectMold,m.Mold).join(m.Mold,m.Mold.id==m.ProjectMold.mold_id)
                                    .where(m.ProjectMold.project_id.in_(by_id.keys()))):
            add(link.project_id,mold.internal_number,'模具号');add(link.project_id,mold.name,'模具名称')
    if 'query_contact_cases' in allowed_tools:
        from domain_packs.mold.erp.change.contacts import permitted
        contacts=list(db.scalars(select(m.ContactCase).where(m.ContactCase.project_id.in_(by_id.keys())).limit(501)))
        for case in contacts[:500]:
            if not permitted(db,user,'read',case):continue
            for value,label in ((case.id,'联络单ID'),(case.title,'联络单标题'),(case.customer_ref,'客户引用'),
                                (case.mold_number,'联络模具号'),(case.product_ref,'产品料号')):add(case.project_id,value,label)
            refs=db.scalars(select(m.ContactTask.affected_ref).where(m.ContactTask.case_id==case.id).limit(101))
            for ref in list(refs)[:100]:add(case.project_id,ref,'联络影响对象')
    if 'query_purchase_orders' in allowed_tools:
        from domain_packs.mold.erp.procurement.procurement import order_data
        for order in db.scalars(select(m.PurchaseOrder).where(m.PurchaseOrder.project_id.in_(by_id.keys())).limit(501)):
            try:visible_order=order_data(db,user,order)
            except DomainError:continue
            add(order.project_id,visible_order.get('id'),'采购单ID');add(order.project_id,visible_order.get('number'),'采购单号')
    from domain_packs.mold.erp.core.domain_schemas import CATALOG
    from domain_packs.mold.erp.core.domains import data as subject_data
    for kind in CATALOG:
        if 'query_'+kind not in allowed_tools:continue
        subjects=db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id.in_(by_id.keys()),
            m.BusinessSubject.kind==kind).limit(501))
        for subject in subjects:
            try:visible_subject=subject_data(db,user,subject)
            except DomainError:continue
            add(subject.project_id,visible_subject.get('id'),'业务记录ID')
            add(subject.project_id,visible_subject.get('number'),'业务单号')
            if kind in {'sales_contract','full_outsource_contract'}:
                detail=visible_subject.get('detail') if isinstance(visible_subject.get('detail'),dict) else {}
                add(subject.project_id,detail.get('contract_number'),'合同编号')
    if not scores:return None,[],truncated
    best=max(scores.values());ids=[pid for pid,score in scores.items() if score==best]
    if len(ids)!=1:return None,[_project_card(db,user,by_id[pid],reasons[pid]) for pid in ids[:20]],truncated
    return by_id[ids[0]],reasons[ids[0]],truncated


def _subjects(db,user,project_id,allowed_tools):
    from domain_packs.mold.erp.core.domain_schemas import CATALOG
    from domain_packs.mold.erp.core.domains import data as subject_data
    result={};truncated=[];skipped=[]
    for kind,spec in CATALOG.items():
        if 'query_'+kind not in allowed_tools:
            skipped.append(spec['name']);continue
        rows=list(db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id==project_id,
            m.BusinessSubject.kind==kind).order_by(m.BusinessSubject.created_at.desc(),m.BusinessSubject.id).limit(21)))
        visible=[]
        for subject in rows:
            try:visible.append(subject_data(db,user,subject))
            except DomainError:continue
        if len(visible)>20:truncated.append(spec['name'])
        if visible:result[kind]=visible[:20]
    return result,truncated,skipped


def _contacts(db,user,project_id,allowed_tools):
    if 'query_contact_cases' not in allowed_tools:return [],False
    from domain_packs.mold.erp.change.contacts import permitted
    rows=list(db.scalars(select(m.ContactCase).where(m.ContactCase.project_id==project_id)
        .order_by(m.ContactCase.created_at.desc(),m.ContactCase.id).limit(21)))
    data=[]
    for case in rows[:20]:
        if not permitted(db,user,'read',case):continue
        tasks=list(db.scalars(select(m.ContactTask).where(m.ContactTask.case_id==case.id)
            .order_by(m.ContactTask.created_at.desc(),m.ContactTask.id).limit(21)))
        data.append({'id':case.id,'title':case.title,'status':'CLOSED' if case.closed_at else 'OPEN',
            'current_stage':case.current_stage,'mold_number':case.mold_number,'revision':case.revision,
            'impacts':[{'id':task.id,'affected_type':task.affected_type,'affected_ref':task.affected_ref,
                'action':task.planned_action,'status':task.status,'delivery_impact_days':task.delivery_impact_days,
                'estimated_amount':str(task.estimated_amount) if task.estimated_amount is not None else None,
                'currency':task.currency} for task in tasks[:20]],'impacts_truncated':len(tasks)>20})
    return data,len(rows)>20


def _orders(db,user,project_id,allowed_tools):
    if 'query_purchase_orders' not in allowed_tools:return [],False
    from domain_packs.mold.erp.procurement.procurement import order_data
    rows=list(db.scalars(select(m.PurchaseOrder).where(m.PurchaseOrder.project_id==project_id)
        .order_by(m.PurchaseOrder.created_at.desc(),m.PurchaseOrder.id).limit(21)))
    result=[]
    for order in rows[:20]:
        try:result.append(order_data(db,user,order))
        except DomainError:continue
    return result,len(rows)>20


def _totals(rows,amount_key,currency_key):
    totals=defaultdict(Decimal)
    for row in rows:
        amount=row.get(amount_key);currency=row.get(currency_key)
        if amount is None or not currency:continue
        try:totals[currency]+=Decimal(str(amount))
        except InvalidOperation:continue
    return [{'currency':currency,'amount':str(amount)} for currency,amount in sorted(totals.items())]


def query(db,user,input_data,allowed_tools):
    project,reasons,resolution_truncated=_resolve(db,user,input_data,allowed_tools)
    base={'source':'agent_db','as_of':now().isoformat(),
          'limitations':['只汇总当前用户同时具有数据权限和查询工具能力的记录；缺失类别不得解释为业务不存在。',
                         'Agent 数据与 ERP 原生引用分开呈现，本工具不复制或修改 ERP 单据。']}
    if project is None:
        if reasons is None:
            return {**base,'resolution':'NOT_FOUND_OR_FORBIDDEN','data':[],
                'limitations':base['limitations']+['指定项目不可见、已不存在或当前无权访问；为避免泄露不进一步区分。']}
        if not reasons:
            return {**base,'resolution':'NOT_FOUND','data':[],
                'limitations':base['limitations']+(['项目候选检索最多检查前500个可见项目。'] if resolution_truncated else [])}
        return {**base,'resolution':'AMBIGUOUS','data':reasons,
            'limitations':base['limitations']+['编号命中多个可见项目，请使用候选项目 ID 再查询。']}
    subjects,subject_truncated,skipped_subjects=_subjects(db,user,project.id,allowed_tools)
    contacts,contacts_truncated=_contacts(db,user,project.id,allowed_tools)
    orders,orders_truncated=_orders(db,user,project.id,allowed_tools)
    dossier_fields=access(db,user,'project.dossier.read',{'project_id':project.id}).fields
    profile=db.get(m.ProjectProfile,project.id);molds=[]
    for link,mold in db.execute(select(m.ProjectMold,m.Mold).join(m.Mold,m.Mold.id==m.ProjectMold.mold_id)
                                .where(m.ProjectMold.project_id==project.id)):
        molds.append(select_fields({'id':mold.id,'number':mold.internal_number,'name':mold.name,'status':mold.status},dossier_fields))
    plan_records=subjects.get('plan_change',[])+subjects.get('project_plan',[])
    active_plans=[item for item in plan_records if item.get('status')=='EFFECTIVE']
    active_plan=max(active_plans,key=lambda item:item.get('created_at','')) if active_plans else None
    plan_tasks=(active_plan or {}).get('detail',{}).get('tasks',[])
    running=[task for task in plan_tasks if task.get('status')=='RUNNING']
    overdue=[task for task in plan_tasks if task.get('status')!='DONE' and task.get('planned_end') and str(task['planned_end'])<now().date().isoformat()]
    order_lines=[line for order in orders for line in order.get('lines',[])]
    committed=[]
    for order in orders:
        if order.get('status') not in {'ISSUED','CLOSED'}:continue
        for line in order.get('lines',[]):
            if line.get('unit_price') is not None:
                committed.append({'amount':Decimal(str(line['quantity']))*Decimal(str(line['unit_price'])),
                                  'currency':order.get('currency')})
    payment_records=subjects.get('supplier_payment',[])
    confirmations=[payment for record in payment_records for payment in record.get('detail',{}).get('payments',[])]
    customer_nodes=[{'contract_number':record.get('detail',{}).get('contract_number'),
        'subject_number':record.get('number'),'status':record.get('status'),
        'stages':record.get('detail',{}).get('stages',[])} for record in subjects.get('sales_contract',[])]
    risk_signals=[]
    if overdue:risk_signals.append({'signal':'OVERDUE_PLAN_TASKS','count':len(overdue),'task_ids':[t.get('id') for t in overdue[:20]]})
    open_contacts=[case for case in contacts if case['status']=='OPEN']
    if open_contacts:risk_signals.append({'signal':'OPEN_ENGINEERING_CONTACTS','count':len(open_contacts),'contact_ids':[c['id'] for c in open_contacts]})
    open_exceptions=[exception for line in order_lines for exception in line.get('exceptions',[]) if exception.get('status')=='OPEN']
    if open_exceptions:risk_signals.append({'signal':'REPORTED_SUPPLIER_EXCEPTIONS','count':len(open_exceptions),
        'exception_ids':[e.get('id') for e in open_exceptions[:20]]})
    stage={'project_status':project.status,'active_plan_number':active_plan.get('number') if active_plan else None,
           'running_tasks':[{'id':t.get('id'),'key':t.get('key'),'name':t.get('name')} for t in running[:20]],
           'statement':'以项目状态和当前已生效计划为准；没有有效计划时不推断实际阶段。'}
    profile_data={'customer_id':profile.customer_id if profile else None,
                  'customer_due_date':profile.customer_due_date.isoformat() if profile and profile.customer_due_date else None,
                  'execution_mode':profile.execution_mode if profile else None,
                  'settlement_status':profile.settlement_status if profile else None}
    dossier={'project':_project_card(db,user,project,reasons or ['项目ID']),
        'profile':select_fields(profile_data,dossier_fields),
        'molds':molds,'current_stage':stage,'risk_signals':risk_signals,'contacts':contacts,'purchase_orders':orders,
        'business_records':subjects,'financial_summary':{
            'issued_purchase_commitments':_totals(committed,'amount','currency'),
            'confirmed_supplier_payments':_totals(confirmations,'amount','currency'),
            'customer_payment_nodes':customer_nodes,
            'calculation_basis':['采购承诺额=当前可见正式/关闭订单明细数量×快照单价，未定价明细不计入。',
                                 '供应商实付=当前可见付款确认及冲正的有符号金额按币种求和。',
                                 '客户付款节点仅展示合同条件；当前 Agent 未取得客户实际回款台账，不能据此声称已回款。']},
        'coverage':{'skipped_business_categories':skipped_subjects,'truncated_business_categories':subject_truncated,
                    'contacts_truncated':contacts_truncated,'purchase_orders_truncated':orders_truncated,
                    'candidate_search_truncated':resolution_truncated}}
    if skipped_subjects:base['limitations'].append('未分配查询工具的业务类别：'+'、'.join(skipped_subjects))
    if any((subject_truncated,contacts_truncated,orders_truncated,resolution_truncated)):
        base['limitations'].append('部分结果达到单类别20条或候选500项上限，不能视为全量。')
    return {**base,'resolution':'RESOLVED','data':[dossier]}
