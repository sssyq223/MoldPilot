from collections import defaultdict
from pydantic import Field, model_validator
from sqlalchemy import select, and_
from . import models as m
from .authorization import access, predicate, select_fields
from .db import now
from .errors import DomainError
from .schemas import StrictModel


class BusinessMatchInput(StrictModel):
    identifier: str | None = Field(default=None, min_length=1, max_length=200,
        description='任意业务线索，例如项目编号/名称、模具号、合同号、订单号或联络单标题。')
    project_code: str | None = Field(default=None, min_length=1, max_length=80)
    project_name: str | None = Field(default=None, min_length=1, max_length=150)
    customer_name: str | None = Field(default=None, min_length=1, max_length=150)
    mold_number: str | None = Field(default=None, min_length=1, max_length=80)
    contract_number: str | None = Field(default=None, min_length=1, max_length=150)
    order_number: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode='after')
    def at_least_one_locator(self):
        present=[]
        for key in ('identifier','project_code','project_name','customer_name','mold_number','contract_number','order_number'):
            value=getattr(self,key)
            if isinstance(value,str):
                value=value.strip()
                setattr(self,key,value or None)
            if getattr(self,key):present.append(key)
        if not present:raise ValueError('至少提供一项业务线索')
        return self


def _strength(value, needle):
    if value is None or not needle:return 0
    value=str(value).casefold();needle=str(needle).casefold()
    if value==needle:return 100
    if needle in value:return 50
    return 0


def _project_card(db,user,project,matched_by=()):
    fields=access(db,user,'project.read',{'project_id':project.id}).fields
    card=select_fields({'id':project.id,'code':project.code,'name':project.name,'status':project.status},fields)
    card['matched_by']=sorted(set(matched_by))
    return card


def _add(matches,project_id,score,label,value,source):
    if not score:return
    row=matches[project_id]
    row['score']=max(row['score'],score)
    row['matched_by'].add(label)
    row['evidence'].append({'label':label,'value':str(value),'source':source,'match':'EXACT' if score>=100 else 'PARTIAL'})


def _checks(data:BusinessMatchInput):
    checks=[]
    if data.identifier:
        for label in ('任意线索',):
            checks.append((label,data.identifier))
    for attr,label in (('project_code','项目编号'),('project_name','项目名称'),('customer_name','客户名称'),
                       ('mold_number','模具号'),('contract_number','合同号'),('order_number','采购单号')):
        value=getattr(data,attr)
        if value:checks.append((label,value))
    return checks


def query(db,user,data:BusinessMatchInput,allowed_tools:set[str]):
    gate=and_(predicate(db,user,'project.read',{'project_id':m.Project.id}),
              predicate(db,user,'project.dossier.read',{'project_id':m.Project.id}))
    visible=list(db.scalars(select(m.Project).where(gate).order_by(m.Project.code).limit(501)))
    truncated=len(visible)>500;visible=visible[:500]
    by_id={p.id:p for p in visible}
    matches=defaultdict(lambda:{'score':0,'matched_by':set(),'evidence':[]})
    checks=_checks(data)

    def consider(project_id,value,label,source,only_for=None):
        if project_id not in by_id:return
        for requested_label,needle in checks:
            if only_for and requested_label not in only_for and requested_label!='任意线索':continue
            score=_strength(value,needle)
            _add(matches,project_id,score,label,value,source)

    for project in visible:
        consider(project.id,project.id,'项目ID','project')
        consider(project.id,project.code,'项目编号','project',{'项目编号'})
        consider(project.id,project.name,'项目名称','project',{'项目名称'})

    if by_id:
        project_ids=list(by_id)
        profiles=db.execute(select(m.ProjectProfile,m.Customer).join(m.Customer,m.Customer.id==m.ProjectProfile.customer_id)
                            .where(m.ProjectProfile.project_id.in_(project_ids))).all()
        for profile,customer in profiles:
            consider(profile.project_id,customer.name,'客户名称','project_profile/customer',{'客户名称'})
        for link,mold in db.execute(select(m.ProjectMold,m.Mold).join(m.Mold,m.Mold.id==m.ProjectMold.mold_id)
                                    .where(m.ProjectMold.project_id.in_(project_ids))):
            consider(link.project_id,mold.internal_number,'模具号','project_mold/mold',{'模具号'})
            consider(link.project_id,mold.name,'模具名称','project_mold/mold')

    if 'query_purchase_orders' in allowed_tools:
        from .procurement import order_data
        for order in db.scalars(select(m.PurchaseOrder).where(m.PurchaseOrder.project_id.in_(list(by_id))).limit(501)):
            try:visible_order=order_data(db,user,order)
            except DomainError:continue
            consider(order.project_id,visible_order.get('number'),'采购单号','purchase_order',{'采购单号'})
            consider(order.project_id,visible_order.get('id'),'采购单ID','purchase_order')

    from .domain_schemas import CATALOG
    from .domains import data as subject_data
    for kind in CATALOG:
        tool='query_'+kind
        if tool not in allowed_tools:continue
        for subject in db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id.in_(list(by_id)),
            m.BusinessSubject.kind==kind).limit(501)):
            try:visible_subject=subject_data(db,user,subject)
            except DomainError:continue
            consider(subject.project_id,visible_subject.get('number'),'业务单号','business_subject')
            consider(subject.project_id,visible_subject.get('id'),'业务记录ID','business_subject')
            if kind in {'sales_contract','full_outsource_contract'}:
                detail=visible_subject.get('detail') if isinstance(visible_subject.get('detail'),dict) else {}
                consider(subject.project_id,detail.get('contract_number'),'合同号','business_subject/contract',{'合同号'})

    if 'query_contact_cases' in allowed_tools:
        from .contacts import permitted
        for case in db.scalars(select(m.ContactCase).where(m.ContactCase.project_id.in_(list(by_id))).limit(501)):
            if not permitted(db,user,'read',case):continue
            for value,label in ((case.id,'联络单ID'),(case.title,'联络单标题'),(case.customer_ref,'客户引用'),
                                (case.mold_number,'联络模具号'),(case.product_ref,'产品料号')):
                consider(case.project_id,value,label,'contact_case')

    candidates=[]
    for project_id,row in matches.items():
        if row['score']<=0:continue
        project=by_id[project_id]
        evidence=sorted(row['evidence'],key=lambda item:(item['match']!='EXACT',item['label'],item['value']))[:20]
        candidates.append({'project':_project_card(db,user,project,row['matched_by']),'score':row['score'],
                           'match_quality':'EXACT' if row['score']>=100 else 'PARTIAL','evidence':evidence})
    candidates.sort(key=lambda row:(-row['score'],row['project'].get('code','')))
    best=candidates[0]['score'] if candidates else 0
    best_count=sum(1 for row in candidates if row['score']==best)
    resolution='NOT_FOUND' if not candidates else 'UNIQUE_CANDIDATE' if best_count==1 and best>=100 else 'MULTIPLE_CANDIDATES'
    limitations=['只返回当前用户同时具备项目读取和项目业务档案读取权限的候选，不查询隐藏项目。',
                 '候选匹配只用于人工确认或后续核对，不会自动创建、合并、承接或关闭业务对象。',
                 '合同、订单和联络线索仅在当前用户已具备对应查询工具与业务权限时参与匹配。']
    if truncated:limitations.append('最多检查前500个可见项目，结果可能未覆盖全部可见范围。')
    return {'resolution':resolution,'data':candidates[:20],'source':'agent_db','as_of':now().isoformat(),
            'limit':20,'limitations':limitations}
