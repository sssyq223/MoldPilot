from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal
from fastapi import APIRouter, Depends
from pydantic import Field, ValidationError, field_validator, model_validator
from sqlalchemy import select, and_
from domain_packs.mold import models as m, domains, domain_schemas as s, workflow_selection
from domain_packs.mold.authorization import access, fingerprint, predicate, require, select_fields
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import get_db, now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.security import current_user
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.erp.commercial import contract_documents
from domain_packs.mold.erp.commercial import contract_relations


class ContractContextInput(StrictModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    identifier: str | None = Field(default=None, min_length=1, max_length=200,
        description='项目编号/名称、合同号、合同业务单号、模具号或其他可见业务线索。')

    @model_validator(mode='after')
    def one_locator(self):
        if bool(self.project_id)==bool(self.identifier):
            raise ValueError('project_id 和 identifier 须且只能填写一项')
        if self.identifier:
            self.identifier=self.identifier.strip()
            if not self.identifier:raise ValueError('线索不能为空')
        return self


class ContractSettlementAllocationInput(StrictModel):
    source_record_id: str = Field(min_length=1, max_length=36,
        description='前序合同版本链中的实际回款或实际付款确认记录 ID。')
    target_stage_name: str = Field(min_length=1, max_length=100,
        description='该历史实收实付在新合同中归属的付款节点名称。')


class ContractProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    contract_kind: Literal['sales_contract', 'full_outsource_contract'] = Field(
        description='合同类型：销售合同或整套委外合同。')
    customer_id: str | None = Field(default=None, max_length=36)
    supplier_id: str | None = Field(default=None, max_length=36)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    contract_number: str = Field(min_length=1, max_length=100)
    expected_date: date | None = None
    received_date: date | None = Field(default=None,
        description='合同原件实际到达日期；销售合同登记必须填写，并与本轮上传附件一并留痕。')
    replaces_id: str | None = Field(default=None, max_length=36)
    relation_type: Literal['ORIGINAL','REPLACEMENT','ADDITION'] = Field(
        default='ORIGINAL', description='原始合同、替代合同或追加合同。')
    settlement_allocation_evidence: str | None = Field(default=None, min_length=1, max_length=4000,
        description='替代合同对历史已收已付逐条归属的财务确认依据。')
    settlement_allocations: list[ContractSettlementAllocationInput] = Field(default_factory=list, max_length=100)
    stages: list[s.StageInput] = Field(default_factory=list, max_length=30)
    remark: str = Field(default='', max_length=4000)
    workflow_definition_id: str = Field(min_length=1, max_length=36)
    file_ids: list[str] = Field(min_length=1, max_length=10,
        description='本轮任务中明确上传的合同原件文件 ID；审批将冻结这些文件版本。')
    document_source: Literal['ELECTRONIC','PAPER_SCAN','OTHER'] = 'ELECTRONIC'
    material_review_id: str | None = Field(default=None, min_length=1, max_length=36,
        description='当审批流程绑定资料模板时，填写本人已确认的资料核对包 ID；无资料模板流程保持 null。')

    @field_validator('file_ids')
    @classmethod
    def unique_file_ids(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('合同附件不能重复')
        return value

    @model_validator(mode='after')
    def validate_relation(self):
        record_ids=[item.source_record_id for item in self.settlement_allocations]
        if len(record_ids)!=len(set(record_ids)):
            raise ValueError('同一历史实收实付记录不能重复分配')
        stage_names=[stage.name for stage in self.stages]
        if len(stage_names)!=len(set(stage_names)):
            raise ValueError('同一合同的付款节点名称不能重复')
        if self.relation_type=='ORIGINAL':
            if self.replaces_id or self.settlement_allocation_evidence or self.settlement_allocations:
                raise ValueError('原始合同不能包含前序合同或历史收付款分配')
        elif self.relation_type=='ADDITION':
            if not self.replaces_id:
                raise ValueError('追加合同必须关联前序合同')
            if self.settlement_allocation_evidence or self.settlement_allocations:
                raise ValueError('追加合同不迁移历史收付款')
        elif not self.replaces_id or not self.settlement_allocation_evidence:
            raise ValueError('替代合同必须关联前序合同并填写历史收付款分配依据')
        return self


class ContractSigningRecordProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    contract_subject_id: str = Field(min_length=1, max_length=36)
    template_name: str = Field(default='', max_length=150)
    signing_method: Literal['MANUAL','OFFLINE_FILE','IMPORT','ERP','OTHER'] = 'OFFLINE_FILE'
    status: Literal['DRAFT','UNDER_REVIEW','SIGNED','REJECTED','CANCELLED'] = 'SIGNED'
    signed_date: date | None = None
    signed_file_id: str | None = Field(default=None, max_length=36)
    signed_file_title: str = Field(default='', max_length=200)
    supplier_signer: str = Field(default='', max_length=120)
    evidence: str = Field(min_length=1, max_length=4000)
    source_ref: str | None = Field(default=None, max_length=120)


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
              predicate(db,user,'project.dossier.read',{'project_id':m.Project.id}))
    rows=list(db.scalars(select(m.Project).where(gate).order_by(m.Project.code).limit(501)))
    return rows[:500],len(rows)>500


def _contract_subjects(db,user,project_ids,kind,allowed_tools):
    if 'query_'+kind not in allowed_tools:return []
    from domain_packs.mold.erp.core.domains import data as subject_data
    result=[]
    for subject in db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id.in_(project_ids),
        m.BusinessSubject.kind==kind).order_by(m.BusinessSubject.created_at.desc(),m.BusinessSubject.id).limit(501)):
        try:result.append(subject_data(db,user,subject))
        except DomainError:continue
    return result[:500]


def workflow_options(db,user,project,kind):
    scope={'project_id':project.id}
    require_contract_permissions(db,user,kind,scope,submit=True)
    rows=db.scalars(select(m.WorkflowDefinition).where(m.WorkflowDefinition.status=='PUBLISHED').order_by(
        m.WorkflowDefinition.process_key,m.WorkflowDefinition.version.desc()))
    result=[]
    categories={'outsource'} if kind=='full_outsource_contract' else set()
    for row in rows:
        if not workflow_selection.matches(row.config,{'business_type':kind,'categories':categories,'design_type':None}):
            continue
        result.append(workflow_selection.metadata(row,db))
    return result


def require_contract_permissions(db,user,kind,scope,submit=False):
    require(db,user,'project.read',scope)
    require(db,user,kind+'.read',scope)
    if submit:
        require(db,user,kind+'.create',scope)
        require(db,user,kind+'.submit',scope)


def _resolve(db,user,data:ContractContextInput,allowed_tools:set[str]):
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
        for kind,label in (('sales_contract','销售合同'),('full_outsource_contract','整套委外合同')):
            for subject in _contract_subjects(db,user,list(by_id),kind,allowed_tools):
                detail=subject.get('detail') if isinstance(subject.get('detail'),dict) else {}
                add(subject.get('project_id'),subject.get('id'),label+'ID')
                add(subject.get('project_id'),subject.get('number'),label+'业务单号')
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


def _records(db,user,project_id,allowed_tools):
    result={};truncated={}
    for kind in ('sales_contract','full_outsource_contract'):
        if 'query_'+kind not in allowed_tools:
            result[kind]=[];truncated[kind]=False;continue
        rows=list(db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id==project_id,
            m.BusinessSubject.kind==kind).order_by(m.BusinessSubject.created_at.desc(),m.BusinessSubject.id).limit(21)))
        visible=[]
        from domain_packs.mold.erp.core.domains import data as subject_data
        for subject in rows[:20]:
            try:visible.append(subject_data(db,user,subject))
            except DomainError:continue
        result[kind]=visible;truncated[kind]=len(rows)>20
    return result,truncated


def _decimal(value):
    if value is None:return None
    try:return Decimal(str(value))
    except (InvalidOperation,ValueError):return None


def _totals(records,statuses=None):
    amount=defaultdict(Decimal);stages=defaultdict(Decimal);counts=defaultdict(int)
    for record in records:
        if statuses is not None and record.get('status') not in statuses:continue
        detail=record.get('detail') if isinstance(record.get('detail'),dict) else {}
        currency=detail.get('currency')
        value=_decimal(detail.get('amount'))
        if currency and value is not None:
            amount[currency]+=value;counts[currency]+=1
        for stage in detail.get('stages') or []:
            stage_amount=_decimal(stage.get('amount'))
            stage_currency=stage.get('currency') or currency
            if stage_currency and stage_amount is not None:stages[stage_currency]+=stage_amount
    return [{'currency':currency,'contract_amount':str(amount[currency]),'stage_amount':str(stages[currency]),
             'contract_count':counts[currency]} for currency in sorted(set(amount)|set(stages))]


def _replacement_map(records):
    by_id={record.get('id'):record for record in records}
    replaced_by=defaultdict(list)
    for record in records:
        detail=record.get('detail') if isinstance(record.get('detail'),dict) else {}
        if detail.get('replaces_id'):replaced_by[detail['replaces_id']].append(record)
    links=[]
    for record in records:
        detail=record.get('detail') if isinstance(record.get('detail'),dict) else {}
        if detail.get('replaces_id') or record.get('id') in replaced_by:
            links.append({'contract_id':record.get('id'),'contract_number':detail.get('contract_number'),
                'status':record.get('status'),'relation_type':detail.get('relation_type') or 'REPLACEMENT',
                'replaces_id':detail.get('replaces_id'),
                'replaces_number':(by_id.get(detail.get('replaces_id')) or {}).get('detail',{}).get('contract_number')
                    if isinstance((by_id.get(detail.get('replaces_id')) or {}).get('detail'),dict) else None,
                'replaced_by':[{'contract_id':row.get('id'),'contract_number':(row.get('detail') or {}).get('contract_number'),
                    'relation_type':(row.get('detail') or {}).get('relation_type') or 'REPLACEMENT',
                    'status':row.get('status')}
                    for row in replaced_by.get(record.get('id'),[])],
                'settlement_allocations':detail.get('settlement_allocations') or []})
    return links


def _late_expected(records):
    today=now().date().isoformat()
    result=[]
    for record in records:
        detail=record.get('detail') if isinstance(record.get('detail'),dict) else {}
        expected=detail.get('expected_date')
        if expected and str(expected)<today and record.get('status') not in {'EFFECTIVE','CLOSED'}:
            result.append({'id':record.get('id'),'number':record.get('number'),
                'contract_number':detail.get('contract_number'),'status':record.get('status'),
                'expected_date':expected})
    return result


def query(db,user,data:ContractContextInput,allowed_tools:set[str]):
    project,alternatives,truncated=_resolve(db,user,data,allowed_tools)
    limitations=['只读取当前用户可见且具备项目业务档案读取权限的项目。',
                 '合同明细仅在对应销售合同或整套委外合同查询工具及业务权限同时可用时返回。',
                 '本工具只汇总合同上下文，不上传合同、不做 OCR、不确认收付款、不替代财务核对或合同审批。']
    if truncated:limitations.append('最多检查前500个可见项目，结果可能未覆盖全部可见范围。')
    if project:
        records,record_truncated=_records(db,user,project.id,allowed_tools)
        sales=records['sales_contract'];outsource=records['full_outsource_contract']
        all_records=sales+outsource
        if record_truncated['sales_contract']:limitations.append('销售合同最多返回最新20条。')
        if record_truncated['full_outsource_contract']:limitations.append('整套委外合同最多返回最新20条。')
        skipped=[]
        if 'query_sales_contract' not in allowed_tools:skipped.append('销售合同')
        if 'query_full_outsource_contract' not in allowed_tools:skipped.append('整套委外合同')
        if skipped:limitations.append('未分配对应合同查询工具，未返回：'+'、'.join(skipped))
        workflows={}
        if 'prepare_contract_record' in allowed_tools:
            for kind in ('sales_contract','full_outsource_contract'):
                try:workflows[kind]=workflow_options(db,user,project,kind)
                except DomainError as error:limitations.append(s.CATALOG[kind]['name']+'当前人员不可提交，未返回可选流程：'+error.message)
        return {'resolution':'RESOLVED','data':[{'project':_project_card(db,user,project,alternatives or ('项目定位',)),
            'sales_contracts':sales,'full_outsource_contracts':outsource,
            'contract_totals':{'sales_contract':_totals(sales),'full_outsource_contract':_totals(outsource)},
            'current_effective_contract_totals':{
                'sales_contract':_totals(sales,{'EFFECTIVE'}),
                'full_outsource_contract':_totals(outsource,{'EFFECTIVE'}),
            },
            'replacement_links':_replacement_map(all_records),
            'late_expected_contracts':_late_expected(all_records),
            'workflow_options':workflows,
            'derived_status':{
                'has_sales_contract':bool(sales),
                'has_full_outsource_contract':bool(outsource),
                'has_effective_sales_contract':any(row.get('status')=='EFFECTIVE' for row in sales),
                'has_effective_full_outsource_contract':any(row.get('status')=='EFFECTIVE' for row in outsource),
                'has_replacement_relation':bool(_replacement_map(all_records)),
                'has_pending_contract_relation':any(
                    (row.get('detail') or {}).get('relation_type') in {'REPLACEMENT','ADDITION'}
                    and row.get('status')!='EFFECTIVE' for row in all_records),
                'has_late_expected_contract':bool(_late_expected(all_records))}}],
            'source':'agent_db','as_of':now().isoformat(),'limitations':limitations}
    if alternatives is None:
        return {'resolution':'NOT_FOUND_OR_FORBIDDEN','data':[],'source':'agent_db','as_of':now().isoformat(),
                'limitations':limitations}
    if alternatives:
        return {'resolution':'MULTIPLE_CANDIDATES','data':alternatives,'source':'agent_db','as_of':now().isoformat(),
                'limitations':limitations+['线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。']}
    return {'resolution':'NOT_FOUND','data':[],'source':'agent_db','as_of':now().isoformat(),
            'limitations':limitations}


def contract_schema():
    return ContractProposalInput.model_json_schema()


def contract_signing_record_schema():
    return ContractSigningRecordProposalInput.model_json_schema()


def parse_contract(arguments):
    try:return ContractProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError('INVALID_TOOL_INPUT','合同登记参数不完整或不符合要求：'+error.errors()[0]['msg']) from None


def parse_contract_signing_record(arguments):
    try:data=ContractSigningRecordProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError('INVALID_TOOL_INPUT','合同签署记录参数不完整或不符合要求：'+error.errors()[0]['msg']) from None
    if data.status=='SIGNED' and not data.signed_date:
        raise DomainError('INVALID_TOOL_INPUT','已签署合同必须填写签署日期')
    if data.status=='SIGNED' and not (data.signed_file_id or data.signed_file_title):
        raise DomainError('INVALID_TOOL_INPUT','已签署合同必须填写签署文件或文件标题')
    if data.status!='SIGNED' and data.signed_date:
        raise DomainError('INVALID_TOOL_INPUT','非已签署状态不要填写签署日期')
    return data


def _party_display(db,data):
    if data.contract_kind=='sales_contract':
        customer=db.get(m.Customer,data.customer_id) if data.customer_id else None
        return {'客户':customer.name if customer else '未找到客户', '供应商':'不适用'}
    supplier=db.get(m.Supplier,data.supplier_id) if data.supplier_id else None
    return {'客户':'不适用', '供应商':supplier.name if supplier else '未找到供应商'}


def _duplicate_contract(db,project_id,kind,contract_number):
    return db.scalar(select(m.BusinessSubject).join(m.ContractDetail,m.ContractDetail.subject_id==m.BusinessSubject.id).where(
        m.BusinessSubject.project_id==project_id,
        m.BusinessSubject.kind==kind,
        m.BusinessSubject.status.in_(['DRAFT','SUBMITTED','RETURNED','APPLY_BLOCKED','EFFECTIVE']),
        m.ContractDetail.contract_number==contract_number).order_by(m.BusinessSubject.created_at.desc()).limit(1))


def _contract_detail(data):
    return s.ContractInput(customer_id=data.customer_id,supplier_id=data.supplier_id,
        amount=data.amount,currency=data.currency,contract_number=data.contract_number,
        expected_date=data.expected_date,replaces_id=data.replaces_id,
        relation_type=data.relation_type,
        settlement_allocation_evidence=data.settlement_allocation_evidence,
        stages=data.stages)


def _preview_settlement_allocations(db,data,detail):
    if data.relation_type=='ORIGINAL':return None,[]
    predecessor=db.get(m.BusinessSubject,data.replaces_id)
    if (not predecessor or predecessor.project_id!=data.project_id or predecessor.kind!=data.contract_kind
            or predecessor.status!='EFFECTIVE'):
        raise DomainError('CONTRACT_PREDECESSOR_INVALID','前序合同不存在、类型不符或已不是当前有效合同，请重新查询',409)
    old=db.get(m.ContractDetail,predecessor.id)
    if not old or old.currency!=detail.currency:
        raise DomainError('CONTRACT_CURRENCY_MISMATCH','新旧合同币种必须一致',409)
    if data.contract_kind=='sales_contract' and old.customer_id!=detail.customer_id:
        raise DomainError('CONTRACT_PARTY_MISMATCH','销售合同替代或追加必须保持同一客户',409)
    if data.contract_kind=='full_outsource_contract' and old.supplier_id!=detail.supplier_id:
        raise DomainError('CONTRACT_PARTY_MISMATCH','委外合同替代或追加必须保持同一供应商',409)
    if data.relation_type=='ADDITION':return predecessor,[]

    sibling=db.scalar(select(m.BusinessSubject).join(m.ContractDetail,m.ContractDetail.subject_id==m.BusinessSubject.id).where(
        m.ContractDetail.replaces_id==predecessor.id,
        m.ContractDetail.relation_type=='REPLACEMENT',
        m.BusinessSubject.status.in_(contract_relations.ACTIVE_REPLACEMENT_STATUSES)).limit(1))
    if sibling:raise DomainError('CONTRACT_REPLACEMENT_EXISTS','前序合同已有在途或生效替代版本，不能重复替代',409)

    actual=contract_relations.settlement_records(db,predecessor)
    by_id={row['source_record_id']:row for row in actual}
    requested={row.source_record_id:row.target_stage_name for row in data.settlement_allocations}
    if set(requested)!=set(by_id):
        raise DomainError('CONTRACT_SETTLEMENT_ALLOCATION_INCOMPLETE',
            '替代合同必须逐条分配前序版本链中的全部历史实收实付；不能遗漏或重复',409)
    stages={stage.name:stage for stage in detail.stages}
    totals=defaultdict(Decimal);cards=[]
    for record_id,target_name in requested.items():
        target=stages.get(target_name)
        source=by_id[record_id]
        if not target:raise DomainError('CONTRACT_TARGET_STAGE_INVALID','历史收付款目标节点不存在',409)
        totals[target_name]+=source['amount']
        cards.append({**source,'target_stage_name':target_name})
    for name,amount in totals.items():
        if amount<0 or amount>stages[name].amount:
            raise DomainError('CONTRACT_STAGE_ALLOCATION_OVERFLOW','历史收付款分配后节点金额小于零或超过节点金额',409)
    total=sum((row['amount'] for row in cards),Decimal(0))
    if total<0 or total>detail.amount:
        raise DomainError('CONTRACT_ALLOCATION_OVERFLOW','历史收付款净额小于零或超过替代合同金额',409)
    return predecessor,cards


def preview_contract(db,user,data:ContractProposalInput,run):
    project=db.get(m.Project,data.project_id)
    if not project:raise DomainError('NOT_FOUND','项目不存在',404)
    scope={'project_id':project.id}
    require_contract_permissions(db,user,data.contract_kind,scope,submit=True)
    if project.row_version!=data.project_version:
        raise DomainError('VERSION_CONFLICT','项目状态已变化，请重新查询后准备',409)
    if project.status in {'CLOSED','TERMINATED'}:
        raise DomainError('PROJECT_BLOCKED','项目已关闭或终止，不能准备普通合同',409)
    detail=_contract_detail(data)
    if data.contract_kind=='sales_contract':
        if not data.received_date:
            raise DomainError('CONTRACT_RECEIVED_DATE_REQUIRED','登记销售合同必须填写合同原件实际到达日期',409)
        customer=db.get(m.Customer,detail.customer_id) if detail.customer_id else None
        if not customer or not customer.active or detail.supplier_id:
            raise DomainError('PARTY_INVALID','销售合同须关联有效客户，且不能填写供应商')
    else:
        supplier=db.get(m.Supplier,detail.supplier_id) if detail.supplier_id else None
        if not supplier or not supplier.active or supplier.category!='outsource' or detail.customer_id:
            raise DomainError('PARTY_INVALID','整套委外合同须关联有效委外供应商，且不能填写客户')
    if sum((stage.amount for stage in detail.stages),Decimal(0))>detail.amount:
        raise DomainError('STAGE_OVERFLOW','合同阶段金额合计超出合同金额')
    predecessor,allocation_cards=_preview_settlement_allocations(db,data,detail)
    duplicate=_duplicate_contract(db,project.id,data.contract_kind,detail.contract_number)
    if duplicate:
        raise DomainError('CONTRACT_DUPLICATE','当前项目已有相同合同号的未关闭合同材料，请勿重复准备',409)
    blobs=contract_documents.validate_proposal_files(
        db,user,data.file_ids,run,project.id,data.contract_kind)
    options=workflow_options(db,user,project,data.contract_kind)
    selected=next((item for item in options if item['id']==data.workflow_definition_id),None)
    if not selected:raise DomainError('WORKFLOW_MISMATCH','审批模板不可用，请重新查询流程选项',409)
    stages=[{
        '名称':stage.name,
        '金额':str(stage.amount)+' '+detail.currency,
        '比例':(str(stage.ratio_percent)+'%') if stage.ratio_percent is not None else '按固定金额',
        '条件':stage.condition,
        '触发事件':stage.trigger_event or '待财务结构化确认',
        '触发日期':stage.trigger_date.isoformat() if stage.trigger_date else '尚未触发',
        '账期':(str(stage.credit_days)+'天') if stage.credit_days is not None else '未登记',
        '预计到期日':stage.expected_due_date.isoformat() if stage.expected_due_date else '待触发或待确认',
    } for stage in detail.stages]
    party=_party_display(db,data)
    relation_labels={'ORIGINAL':'原始合同','REPLACEMENT':'替代合同','ADDITION':'追加合同'}
    display={'操作':'登记销售合同' if data.contract_kind=='sales_contract' else '登记整套委外合同',
        '项目':project.code+' · '+project.name,
        '项目版本':project.row_version,
        **party,
        '合同号':detail.contract_number,
        '合同金额':str(detail.amount)+' '+detail.currency,
        '合同关系':relation_labels[data.relation_type],
        '前序合同':(db.get(m.ContractDetail,predecessor.id).contract_number if predecessor else '无'),
        '历史实收实付分配':[{
            '记录类型':'客户实际回款' if row['record_type']=='CUSTOMER_RECEIPT' else '供应商实际付款',
            '来源记录':row['source_record_id'],'来源合同':row['source_contract_id'],
            '日期':row['date'],'流水':row['reference'],
            '金额':str(row['amount'])+' '+row['currency'],'新合同节点':row['target_stage_name'],
        } for row in allocation_cards] or ['无历史实收实付需要迁移'],
        '分配依据':data.settlement_allocation_evidence or '不适用',
        '预计签订或补齐日期':detail.expected_date.isoformat() if detail.expected_date else '未填写',
        '合同实际到达日期':data.received_date.isoformat() if data.received_date else '未填写',
        '付款节点':stages or ['未登记付款节点'],
        '合同附件':[blob.filename for blob in blobs],
        '附件来源':{'ELECTRONIC':'电子合同','PAPER_SCAN':'纸质合同扫描件','OTHER':'其他人工资料'}[data.document_source],
        '备注':data.remark or '无',
        '审批流程':selected['name']+' · 第'+str(selected['version'])+'版',
        '说明':'本人确认后仅创建合同材料并提交 Agent BPM；替代关系仅在审批生效时关闭前序版本，历史实收实付仍保留在原记录并按明确节点归属，不执行收付款或 ERP 合同操作。'}
    return detail,display,blobs,allocation_cards


def preview_contract_signing_record(db,user,data:ContractSigningRecordProposalInput):
    project=db.get(m.Project,data.project_id)
    if not project:raise DomainError('NOT_FOUND','项目不存在',404)
    scope={'project_id':project.id,'category':'outsource'}
    require(db,user,'project.read',{'project_id':project.id})
    require(db,user,'full_outsource_contract.read',scope)
    require(db,user,'full_outsource_contract.execute',scope)
    if project.row_version!=data.project_version:
        raise DomainError('VERSION_CONFLICT','项目状态已变化，请重新查询后准备',409)
    contract=db.get(m.BusinessSubject,data.contract_subject_id)
    if not contract or contract.project_id!=project.id or contract.kind!='full_outsource_contract':
        raise DomainError('CONTRACT_NOT_FOUND','整套委外合同不存在或不属于该项目',404)
    detail=db.get(m.ContractDetail,contract.id)
    if not detail or not detail.supplier_id:
        raise DomainError('CONTRACT_ROLE_INVALID','签署记录必须关联整套委外供应商合同',409)
    supplier=db.get(m.Supplier,detail.supplier_id)
    if data.status=='SIGNED' and contract.status not in {'EFFECTIVE','CLOSED'}:
        raise DomainError('CONTRACT_NOT_EFFECTIVE','只有已生效或已关闭的整套委外合同才能登记已签署文件',409)
    if data.signed_file_id:
        from domain_packs.mold.ports.files import uploaded_file
        uploaded_file(db,user,data.signed_file_id)
    if data.source_ref and db.scalar(select(m.ContractSigningRecord.id).where(
        m.ContractSigningRecord.contract_subject_id==contract.id,
        m.ContractSigningRecord.status==data.status,
        m.ContractSigningRecord.source_ref==data.source_ref)):
        raise DomainError('CONTRACT_SIGNING_DUPLICATE','该合同签署来源已登记',409)
    display={'操作':'登记整套委外合同签署文件',
        '项目':project.code+' · '+project.name,
        '项目版本':project.row_version,
        '整套委外合同':detail.contract_number,
        '供应商':supplier.name if supplier else detail.supplier_id,
        '模板名称':data.template_name or '未填写',
        '签署方式':data.signing_method,
        '签署状态':data.status,
        '签署日期':data.signed_date.isoformat() if data.signed_date else '未签署',
        '签署文件':data.signed_file_title or (data.signed_file_id or '未登记文件'),
        '供应商签署人':data.supplier_signer or '未填写',
        '来源引用':data.source_ref or '未填写',
        '依据':data.evidence,
        '说明':'本人确认后仅登记人工签署文件或签署状态证据；不发起电子签署、不修改合同审批状态、不确认收付款。'}
    return contract,display


def create_contract_signing_record(db,user,data:ContractSigningRecordProposalInput):
    contract,_=preview_contract_signing_record(db,user,data)
    row=m.ContractSigningRecord(contract_subject_id=contract.id,
        template_name=data.template_name,signing_method=data.signing_method,status=data.status,
        signed_date=data.signed_date,signed_file_id=data.signed_file_id,
        signed_file_title=data.signed_file_title,supplier_signer=data.supplier_signer,
        buyer_reviewer_id=user.id,approved_by=user.id if data.status=='SIGNED' else None,
        evidence=data.evidence,source_system='MANUAL',source_ref=data.source_ref,recorded_by=user.id)
    db.add(row);db.flush();return row


def execute_contract_tool(db,user,key,arguments,run=None):
    if key=='prepare_contract_record':
        data=parse_contract(arguments)
        _,display,_,_=preview_contract(db,user,data,run)
        proposal={'kind':data.contract_kind,'action':'contract_record','requires_approval':True,
            'input':data.model_dump(mode='json'),'display':display,
            'confirmation_policy':proposal_confirmation_policy(run,requires_approval=True)}
        limitations=['仅准备合同登记建议；本人确认后才冻结本轮合同原件、创建业务材料并提交审批，审批完成前不代表正式合同或收付款事实。替代合同的历史实收实付只建立不可变归属，不复制、删除或改写原财务记录；追加合同保持独立有效。']
    elif key=='prepare_contract_signing_record':
        data=parse_contract_signing_record(arguments)
        _,display=preview_contract_signing_record(db,user,data)
        proposal={'kind':'contract_signing_record','action':'contract_signing_record',
            'requires_approval':False,'input':data.model_dump(mode='json'),'display':display,
            'confirmation_policy':proposal_confirmation_policy(run,requires_approval=False)}
        limitations=['仅准备整套委外合同签署证据登记建议；本人确认后才写入签署记录，不发起电子签署、不改变合同审批状态。']
    else:raise DomainError('TOOL_UNKNOWN','工具未实现',403)
    return {'data':[],'source':'agent_proposal','as_of':now().isoformat(),'proposal':proposal,
        'limitations':limitations}


def source(db,user,step_id):
    from domain_packs.mold.tool_gateway import available_tools
    step=db.get(m.Step,step_id);run=db.get(m.Run,step.run_id) if step else None
    if not run or run.user_id!=user.id:raise DomainError('NOT_FOUND','操作建议不存在或无权访问',404)
    if run.status not in {'RUNNING','SUCCEEDED'}:raise DomainError('PROPOSAL_STOPPED','任务已停止，请重新准备操作',409)
    if run.security_version!=user.security_version or run.checkpoint.get('authorization_hash')!=fingerprint(db,user):
        raise DomainError('AUTHORIZATION_CHANGED','授权已变化，请重新准备操作',403)
    proposal=step.result.get('proposal')
    if step.tool not in available_tools(db,user) or step.tool not in {'prepare_contract_record','prepare_contract_signing_record'} or not proposal:
        raise DomainError('TOOL_FORBIDDEN','操作能力不可用',403)
    return proposal


def validate_intent(db,user,payload):
    proposal=source(db,user,payload['step_id'])
    step=db.get(m.Step,payload['step_id'])
    run=db.get(m.Run,step.run_id) if step else None
    if content_hash(proposal)!=payload['proposal_hash']:raise DomainError('CONFIRMATION_INVALID','操作建议内容已变化',409)
    if proposal.get('kind')=='contract_signing_record':
        data=parse_contract_signing_record(proposal['input'])
        _,display=preview_contract_signing_record(db,user,data)
    else:
        data=parse_contract(proposal['input'])
        _,display,_,_=preview_contract(db,user,data,run)
    if content_hash(display)!=content_hash(proposal['display']):
        raise DomainError('VERSION_CONFLICT','项目、合同、权限或流程资料已变化，请重新准备',409)
    return proposal,data


def confirm(db,user,payload):
    from domain_packs.mold.ports.confirmation_policy import agent_permission_mode_from_proposal
    proposal,data=validate_intent(db,user,payload)
    if proposal.get('kind')=='contract_signing_record':
        record=create_contract_signing_record(db,user,data)
        return {'project_id':data.project_id,'contract_subject_id':data.contract_subject_id,
            'contract_signing_record_id':record.id,'action':'contract_signing_record','status':'CONFIRMED'}
    step=db.get(m.Step,payload['step_id'])
    run=db.get(m.Run,step.run_id) if step else None
    detail,_,blobs,allocation_cards=preview_contract(db,user,data,run)
    subject=domains.create(db,user,s.SubjectInput(kind=data.contract_kind,project_id=data.project_id,
        category='outsource' if data.contract_kind=='full_outsource_contract' else None,
        remark=data.remark or data.contract_number,detail=detail.model_dump(mode='json')))
    if data.contract_kind=='sales_contract':
        db.add(m.ContractReceiptEvidence(
            contract_subject_id=subject.id,
            received_date=data.received_date,
            recorded_by=user.id,
        ))
    stages={row.name:row for row in db.scalars(select(m.PaymentStage).where(m.PaymentStage.contract_id==subject.id))}
    for item in allocation_cards:
        target=stages[item['target_stage_name']]
        db.add(m.ContractSettlementAllocation(target_contract_id=subject.id,
            source_contract_id=item['source_contract_id'],target_stage_id=target.id,
            record_type=item['record_type'],source_record_id=item['source_record_id'],
            amount=item['amount'],currency=item['currency'],
            evidence=data.settlement_allocation_evidence,recorded_by=user.id))
    db.flush()
    contract_documents.link_initial(db,user,subject,blobs,data.document_source)
    from domain_packs.mold.erp.core.business import submit_subject
    submitted=submit_subject(db,user,subject.id,subject.revision,data.workflow_definition_id,
        data.material_review_id,agent_permission_mode=agent_permission_mode_from_proposal(proposal))
    return {'project_id':data.project_id,'subject_id':subject.id,'instance_id':submitted['instance_id'],
        'action':'contract_record','contract_kind':data.contract_kind,'status':'SUBMITTED'}


router=APIRouter()


@router.get('/api/contract-proposals/{step_id}')
def proposal_status(step_id:str,user=Depends(current_user),db=Depends(get_db)):
    source(db,user,step_id)
    intent=db.scalar(select(m.HumanIntent).where(m.HumanIntent.user_id==user.id,
        m.HumanIntent.action=='contract.execute',m.HumanIntent.resource_id==step_id,
        m.HumanIntent.receipt.is_not(None)).order_by(m.HumanIntent.created_at.desc()))
    return {'receipt':intent.receipt if intent else None}


@router.post('/api/contract-proposals/{step_id}/intent')
def intent(step_id:str,user=Depends(current_user),db=Depends(get_db)):
    from domain_packs.mold.erp.core.business import create_intent
    proposal=source(db,user,step_id)
    payload={'step_id':step_id,'proposal_hash':content_hash(proposal)}
    result=create_intent(db,user,'contract.execute',step_id,payload)
    result['display']=proposal['display']
    result['confirmation_policy']=proposal.get('confirmation_policy')
    db.commit();return result
