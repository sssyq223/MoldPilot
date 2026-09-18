from collections import Counter, defaultdict
from pydantic import Field, model_validator
from sqlalchemy import select, and_
from domain_packs.mold import models as m
from domain_packs.mold.authorization import access, predicate, select_fields
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel


class DesignRouteContextInput(StrictModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    identifier: str | None = Field(default=None, min_length=1, max_length=200,
        description='项目编号/名称、设计单号、图纸版本、物料编号/名称、计划任务或工程联络线索。')

    @model_validator(mode='after')
    def one_locator(self):
        if bool(self.project_id)==bool(self.identifier):
            raise ValueError('project_id 和 identifier 须且只能填写一项')
        if self.identifier:
            self.identifier=self.identifier.strip()
            if not self.identifier:raise ValueError('线索不能为空')
        return self


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
              predicate(db,user,'design_route.read',{'project_id':m.Project.id}))
    rows=list(db.scalars(select(m.Project).where(gate).order_by(m.Project.code).limit(501)))
    return rows[:500],len(rows)>500


def _visible_subjects(db,user,project_ids,kind,allowed_tools):
    if kind=='design_route':
        if 'query_design_route_context' not in allowed_tools and 'query_design_route' not in allowed_tools:return []
    elif kind=='project_plan':
        if 'query_project_plan_context' not in allowed_tools and 'query_project_plan' not in allowed_tools:return []
    elif 'query_'+kind not in allowed_tools:return []
    from domain_packs.mold.erp.core.domains import data as subject_data
    result=[]
    for subject in db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id.in_(project_ids),
        m.BusinessSubject.kind==kind).order_by(m.BusinessSubject.created_at.desc(),m.BusinessSubject.id).limit(501)):
        try:result.append(subject_data(db,user,subject))
        except DomainError:continue
    return result[:500]


def _resolve(db,user,data:DesignRouteContextInput,allowed_tools:set[str]):
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
        designs=_visible_subjects(db,user,list(by_id),'design_route',allowed_tools)
        material_ids=set()
        for subject in designs:
            add(subject.get('project_id'),subject.get('id'),'设计单ID')
            add(subject.get('project_id'),subject.get('number'),'设计单号')
            detail=subject.get('detail') if isinstance(subject.get('detail'),dict) else {}
            add(subject.get('project_id'),detail.get('drawing_revision'),'图纸版本')
            add(subject.get('project_id'),detail.get('drawing_evidence'),'图纸依据')
            for item in detail.get('items') or []:
                add(subject.get('project_id'),item.get('material_id'),'BOM物料ID')
                add(subject.get('project_id'),item.get('route'),'加工路线')
                if item.get('material_id'):material_ids.add(item['material_id'])
        if material_ids:
            for material in db.scalars(select(m.Material).where(m.Material.id.in_(material_ids)).limit(501)):
                for subject in designs:
                    items=(subject.get('detail') or {}).get('items',[]) if isinstance(subject.get('detail'),dict) else []
                    if any(item.get('material_id')==material.id for item in items):
                        add(subject.get('project_id'),material.code,'物料编号')
                        add(subject.get('project_id'),material.name,'物料名称')
        for plan in _visible_subjects(db,user,list(by_id),'project_plan',allowed_tools):
            for task in (plan.get('detail') or {}).get('tasks',[]) if isinstance(plan.get('detail'),dict) else []:
                add(plan.get('project_id'),task.get('key'),'计划任务标识')
                add(plan.get('project_id'),task.get('name'),'计划任务名称')
        if 'query_contact_cases' in allowed_tools:
            for case in db.scalars(select(m.ContactCase).where(m.ContactCase.project_id.in_(list(by_id))).limit(501)):
                add(case.project_id,case.title,'工程联络标题')
                add(case.project_id,case.mold_number,'工程联络模具号')
                add(case.project_id,case.product_ref,'工程联络产品')
    if not scores:return None,[],truncated
    best=max(scores.values());ids=[pid for pid,score in scores.items() if score==best]
    if len(ids)!=1:return None,[_project_card(db,user,by_id[pid],reasons[pid]) for pid in ids[:20]],truncated
    return by_id[ids[0]],reasons[ids[0]],truncated


def _profile(db,user,project_id):
    fields=access(db,user,'project.read',{'project_id':project_id}).fields
    profile=db.get(m.ProjectProfile,project_id)
    if not profile:return None
    return select_fields({'execution_mode':profile.execution_mode,
        'customer_due_date':profile.customer_due_date.isoformat() if profile.customer_due_date else None,
        'settlement_status':profile.settlement_status},fields | {'execution_mode','customer_due_date','settlement_status'})


def _material_cards(db,designs):
    material_ids={item.get('material_id') for design in designs for item in (design.get('detail') or {}).get('items',[])
                  if isinstance(design.get('detail'),dict) and item.get('material_id')}
    materials={row.id:{'id':row.id,'code':row.code,'name':row.name,'category':row.category,'unit':row.unit}
               for row in db.scalars(select(m.Material).where(m.Material.id.in_(material_ids)).limit(501))} if material_ids else {}
    result=[]
    for design in designs:
        detail=design.get('detail') or {}
        for item in detail.get('items',[]) if isinstance(detail,dict) else []:
            material=materials.get(item.get('material_id'),{'id':item.get('material_id')})
            result.append({**material,'design_id':design.get('id'),'design_number':design.get('number'),
                'quantity':item.get('quantity'),'route':item.get('route'),'task_id':item.get('task_id')})
    return result


def _linked_tasks(db,user,designs,allowed_tools):
    if 'query_project_plan_context' not in allowed_tools and 'query_project_plan' not in allowed_tools:return []
    ids={item.get('task_id') for design in designs for item in (design.get('detail') or {}).get('items',[])
         if isinstance(design.get('detail'),dict) and item.get('task_id')}
    result=[]
    from domain_packs.mold.erp.core.domains import data as subject_data
    for task in db.scalars(select(m.PlanTask).where(m.PlanTask.id.in_(ids)).limit(501)) if ids else []:
        subject=db.get(m.BusinessSubject,task.plan_id)
        try:
            plan=subject_data(db,user,subject)
        except DomainError:
            continue
        result.append({'id':task.id,'key':task.key,'name':task.name,'status':task.status,
            'planned_start':task.planned_start.isoformat(),'planned_end':task.planned_end.isoformat(),
            'plan_id':subject.id,'plan_number':plan.get('number'),'plan_status':plan.get('status')})
    return result


def _contact_impacts(db,user,project_id,materials,tasks,designs,allowed_tools):
    if 'query_contact_cases' not in allowed_tools:return []
    from domain_packs.mold.erp.change.contacts import permitted
    material_terms={v for item in materials for v in (item.get('id'),item.get('code'),item.get('name')) if v}
    task_terms={v for task in tasks for v in (task.get('id'),task.get('key'),task.get('name')) if v}
    design_terms={v for design in designs for v in (design.get('id'),design.get('number'),(design.get('detail') or {}).get('drawing_revision')) if v}
    result=[]
    q=select(m.ContactCase).where(m.ContactCase.project_id==project_id,
        predicate(db,user,'contact.read',{'project_id':m.ContactCase.project_id,'category':m.ContactCase.category})
    ).order_by(m.ContactCase.created_at.desc(),m.ContactCase.id).limit(100)
    for case in db.scalars(q):
        if not permitted(db,user,'read',case):continue
        tasks_out=[]
        for task in db.scalars(select(m.ContactTask).where(m.ContactTask.case_id==case.id,m.ContactTask.status!='CANCELLED')
                               .order_by(m.ContactTask.created_at.desc(),m.ContactTask.id).limit(50)):
            ref=task.affected_ref or ''
            relevant=task.affected_type in {'DRAWING','MATERIAL','PLAN_NODE','WIP_TASK'}
            relevant=relevant or ref in material_terms or ref in task_terms or ref in design_terms
            if not relevant:continue
            tasks_out.append({'id':task.id,'title':task.title,'status':task.status,'affected_type':task.affected_type,
                'affected_ref':task.affected_ref,'planned_action':task.planned_action,
                'delivery_impact_days':task.delivery_impact_days,
                'estimated_amount':str(task.estimated_amount) if task.estimated_amount is not None else None,
                'currency':task.currency})
        if tasks_out or case.problem_source in {'DESIGN_ISSUE','CUSTOMER_CHANGE','PROCESS_IMPROVEMENT'}:
            result.append({'id':case.id,'title':case.title,'mode':case.mode,
                'collaboration_status':'CLOSED' if case.closed_at else 'HISTORY_RECORD' if case.mode=='HISTORY' else 'OPEN',
                'problem_source':case.problem_source,'current_stage':case.current_stage,
                'change_type':case.change_type,'urgency':case.urgency,'tasks':tasks_out[:20]})
    return result[:20]


def _design_card(design):
    if not design:return None
    detail=design.get('detail') if isinstance(design.get('detail'),dict) else {}
    return {'id':design.get('id'),'number':design.get('number'),'status':design.get('status'),
        'created_at':design.get('created_at'),'drawing_revision':detail.get('drawing_revision'),
        'drawing_evidence':detail.get('drawing_evidence')}


def _item_snapshot(item,material=None):
    return {'material_id':item.get('material_id'),'code':(material or {}).get('code'),
        'name':(material or {}).get('name'),'category':(material or {}).get('category'),
        'quantity':item.get('quantity'),'route':item.get('route'),'task_id':item.get('task_id')}


def _revision_impact(designs,materials,tasks):
    effective=[row for row in designs if row.get('status')=='EFFECTIVE']
    if not effective:
        return {'status':'NO_EFFECTIVE_DESIGN','latest_design':None,'previous_design':None,
            'added_items':[],'removed_items':[],'changed_items':[],'unchanged_count':0,
            'affected_plan_tasks':[],'summary':{'added':0,'removed':0,'changed':0,'unchanged':0,
                'route_changed':0,'quantity_changed':0,'task_link_changed':0},
            'derived_status':{'has_revision_comparison':False,'has_bom_or_route_changes':False,
                'has_route_changes':False,'has_task_link_changes':False},
            'limitations':['未见生效设计版本，不能比较正式图纸/BOM/路线改版影响。']}
    ordered=sorted(designs,key=lambda row:(row.get('created_at') or '',row.get('number') or '',row.get('id') or ''),reverse=True)
    latest=max(effective,key=lambda row:(row.get('created_at') or '',row.get('number') or '',row.get('id') or ''))
    previous=next((row for row in ordered if row.get('id')!=latest.get('id')
        and row.get('status') not in {'DRAFT','SUBMITTED','RETURNED','APPLY_BLOCKED','CANCELLED'}),None)
    if not previous:
        return {'status':'NO_PREVIOUS_COMPARABLE_DESIGN','latest_design':_design_card(latest),'previous_design':None,
            'added_items':[],'removed_items':[],'changed_items':[],'unchanged_count':0,
            'affected_plan_tasks':[],'summary':{'added':0,'removed':0,'changed':0,'unchanged':0,
                'route_changed':0,'quantity_changed':0,'task_link_changed':0},
            'derived_status':{'has_revision_comparison':False,'has_bom_or_route_changes':False,
                'has_route_changes':False,'has_task_link_changes':False},
            'limitations':['当前可见范围未见可比较的上一版设计路线，只能展示当前生效版本。']}
    material_by_design={(item.get('design_id'),item.get('id')):item for item in materials if item.get('id')}
    task_by_id={task.get('id'):task for task in tasks if task.get('id')}
    def items(design):
        detail=design.get('detail') if isinstance(design.get('detail'),dict) else {}
        return {item.get('material_id'):item for item in detail.get('items',[]) if item.get('material_id')}
    current=items(latest);prior=items(previous)
    added=[];removed=[];changed=[];unchanged=0;affected_task_ids=set()
    for material_id,item in current.items():
        if material_id not in prior:
            added.append(_item_snapshot(item,material_by_design.get((latest.get('id'),material_id))))
            if item.get('task_id'):affected_task_ids.add(item.get('task_id'))
    for material_id,item in prior.items():
        if material_id not in current:
            removed.append(_item_snapshot(item,material_by_design.get((previous.get('id'),material_id))))
            if item.get('task_id'):affected_task_ids.add(item.get('task_id'))
    route_changed=quantity_changed=task_link_changed=0
    for material_id,item in current.items():
        old=prior.get(material_id)
        if not old:continue
        changes=[]
        if str(old.get('quantity'))!=str(item.get('quantity')):
            changes.append('QUANTITY_CHANGED');quantity_changed+=1
        if old.get('route')!=item.get('route'):
            changes.append('ROUTE_CHANGED');route_changed+=1
        if old.get('task_id')!=item.get('task_id'):
            changes.append('TASK_LINK_CHANGED');task_link_changed+=1
        if changes:
            changed.append({'material_id':material_id,'code':material_by_design.get((latest.get('id'),material_id),{}).get('code')
                or material_by_design.get((previous.get('id'),material_id),{}).get('code'),
                'name':material_by_design.get((latest.get('id'),material_id),{}).get('name')
                or material_by_design.get((previous.get('id'),material_id),{}).get('name'),
                'changes':changes,'from':_item_snapshot(old,material_by_design.get((previous.get('id'),material_id))),
                'to':_item_snapshot(item,material_by_design.get((latest.get('id'),material_id)))})
            if old.get('task_id'):affected_task_ids.add(old.get('task_id'))
            if item.get('task_id'):affected_task_ids.add(item.get('task_id'))
        else:unchanged+=1
    affected_tasks=[task_by_id[task_id] for task_id in sorted(affected_task_ids) if task_id in task_by_id]
    has_changes=bool(added or removed or changed)
    return {'status':'COMPARED','latest_design':_design_card(latest),'previous_design':_design_card(previous),
        'added_items':added[:50],'removed_items':removed[:50],'changed_items':changed[:50],
        'unchanged_count':unchanged,'affected_plan_tasks':affected_tasks[:50],
        'summary':{'added':len(added),'removed':len(removed),'changed':len(changed),'unchanged':unchanged,
            'route_changed':route_changed,'quantity_changed':quantity_changed,'task_link_changed':task_link_changed},
        'derived_status':{'has_revision_comparison':True,'has_bom_or_route_changes':has_changes,
            'has_route_changes':route_changed>0,'has_task_link_changes':task_link_changed>0},
        'limitations':['改版影响仅比较当前可见上一版与当前生效版的 Agent 设计路线事实；不生成图纸、不同步 ERP BOM、不自动调整计划任务。']}


def _design_plan_change_candidates(project,revision_impact,allowed_tools):
    if revision_impact.get('status')!='COMPARED':
        return []
    if not revision_impact.get('derived_status',{}).get('has_bom_or_route_changes'):
        return []
    recommended=[]
    if 'query_project_plan_context' in allowed_tools:
        recommended.append('query_project_plan_context')
        if 'prepare_project_plan_change' in allowed_tools:
            recommended.append('prepare_project_plan_change')
    affected_tasks=revision_impact.get('affected_plan_tasks') or []
    plan_ids={task.get('plan_id') for task in affected_tasks if task.get('plan_id')}
    primary_plan_tasks=[task for task in affected_tasks if task.get('plan_status')=='EFFECTIVE'] or affected_tasks
    previous_id=primary_plan_tasks[0].get('plan_id') if len(plan_ids)==1 and primary_plan_tasks else None
    previous_number=primary_plan_tasks[0].get('plan_number') if previous_id else None
    evidence_gaps=[]
    if not recommended:
        evidence_gaps.append('当前会话未分配项目计划查询工具，不能继续核对计划变更基线。')
    if not affected_tasks:
        evidence_gaps.append('改版差异尚未匹配到当前权限内可见的计划任务。')
    if affected_tasks and not previous_id:
        evidence_gaps.append('改版影响关联到多个或未知计划版本，必须先由项目负责人确认当前有效计划。')
    seed_status='READY_TO_QUERY_PLAN_CONTEXT' if previous_id and recommended else 'NEEDS_CONTEXT'
    def item_change(row,change_type):
        source=row if change_type!='CHANGED' else row.get('to',{})
        return {'change_type':change_type,'material_id':source.get('material_id'),'code':source.get('code'),
            'name':source.get('name'),'route':source.get('route'),'quantity':source.get('quantity'),
            'task_id':source.get('task_id'),'changes':row.get('changes',[]) if change_type=='CHANGED' else []}
    change_intent=[item_change(row,'ADDED') for row in revision_impact.get('added_items',[])]
    change_intent.extend(item_change(row,'REMOVED') for row in revision_impact.get('removed_items',[]))
    change_intent.extend(item_change(row,'CHANGED') for row in revision_impact.get('changed_items',[]))
    latest=revision_impact.get('latest_design') or {}
    previous=revision_impact.get('previous_design') or {}
    return [{
        'source':'design_revision_impact',
        'candidate_status':'READY_FOR_PLAN_CONTEXT_QUERY' if seed_status=='READY_TO_QUERY_PLAN_CONTEXT' and not evidence_gaps else 'NEEDS_CONTEXT',
        'design_revision':{'from':previous.get('drawing_revision'),'to':latest.get('drawing_revision'),
            'previous_design_id':previous.get('id'),'latest_design_id':latest.get('id')},
        'matched_plan_tasks':primary_plan_tasks[:20],
        'evidence_gaps':evidence_gaps,
        'recommended_next_tools':recommended,
        'plan_change_prepare_seed':{
            'status':seed_status,
            'project_id':project.id,
            'project_version':project.row_version,
            'previous_id':previous_id,
            'previous_plan_number':previous_number,
            'source_design_id':latest.get('id'),
            'source_design_number':latest.get('number'),
            'source_previous_design_id':previous.get('id'),
            'candidate_task_keys':[task.get('key') for task in primary_plan_tasks if task.get('key')],
            'change_intent':change_intent[:50],
            'reason_basis':'设计路线从 {old} 调整到 {new}，BOM/路线差异需项目负责人核对是否触发计划变更。'.format(
                old=previous.get('drawing_revision') or previous.get('number') or '上一版',
                new=latest.get('drawing_revision') or latest.get('number') or '当前版'),
            'required_before_prepare':[
                '必须先调用 query_project_plan_context，以当前有效计划 previous_id、project_version、完整任务清单和 workflow_options 为准。',
                'prepare_project_plan_change 的 tasks 必须提交变更后的完整任务列表；未受影响节点保持原值。',
                'BOM数量、采购/委外路线或任务关联变化只是计划调整依据，不能自动推导节点日期、自动顺延全部节点或修改客户承诺交期。',
            ],
        },
        'guardrail':'只提示项目负责人核对并准备计划变更；不会自动改计划、自动同步 ERP BOM、自动下达采购/加工或跳过 BPM 审批。',
    }]


def _analysis(project,designs,materials,tasks,contacts,allowed_tools):
    effective=[row for row in designs if row.get('status')=='EFFECTIVE']
    open_designs=[row for row in designs if row.get('status') in {'DRAFT','SUBMITTED','RETURNED','APPLY_BLOCKED'}]
    latest=max(effective,key=lambda row:(row.get('created_at') or '',row.get('number') or '',row.get('id') or '')) if effective else None
    current_materials=[item for item in materials if latest and item.get('design_id')==latest.get('id')] if latest else materials
    route_counts=dict(Counter(item.get('route') for item in current_materials if item.get('route')))
    unlinked=[item for item in current_materials if item.get('route')=='INTERNAL' and not item.get('task_id')]
    purchase_like=[item for item in current_materials if item.get('route') in {'PURCHASE','OUTSOURCE'}]
    revision_impact=_revision_impact(designs,materials,tasks)
    plan_change_candidates=_design_plan_change_candidates(project,revision_impact,allowed_tools)
    warnings=[]
    if len(effective)>1:warnings.append('当前可见范围存在多个生效设计版本，请先核对唯一正式版本。')
    if not latest:warnings.append('当前可见范围未见生效设计版本，不能认定正式BOM或加工路线已确认。')
    if open_designs:warnings.append('存在未完成的设计/BOM/路线审批，正式版本可能即将变化。')
    if unlinked:warnings.append('存在内部加工路线物料未关联计划任务，不能据此判断加工排程已覆盖。')
    if purchase_like and not tasks:warnings.append('采购或委外路线物料未在当前权限内看到关联计划节点，请核对采购/委外任务承接。')
    if contacts:warnings.append('存在工程联络影响项，设计、BOM或路线结论需结合联络方案/复验状态确认。')
    if revision_impact['derived_status']['has_bom_or_route_changes']:
        warnings.append('当前生效设计较上一版存在BOM、数量、路线或计划关联差异，需结合设计改版依据和相关任务承接核对。')
    return {'latest_effective_design':latest,'open_design_routes':open_designs[:20],
        'route_summary':{'counts':route_counts,'materials':current_materials[:50],
            'internal_unlinked_items':[{'material_id':i.get('id'),'code':i.get('code'),'name':i.get('name')} for i in unlinked[:20]]},
        'linked_plan_tasks':tasks[:50],'engineering_contact_impacts':contacts,'revision_impact':revision_impact,
        'plan_change_candidates':plan_change_candidates,
        'warnings':warnings,'derived_status':{
            'has_effective_design_route':bool(latest),
            'has_open_design_route':bool(open_designs),
            'has_unlinked_internal_route':bool(unlinked),
            'has_purchase_or_outsource_route':bool(purchase_like),
            'has_engineering_contact_impacts':bool(contacts),
            'has_plan_change_candidates':bool(plan_change_candidates),
            **revision_impact['derived_status']}}


def query(db,user,data:DesignRouteContextInput,allowed_tools:set[str]):
    project,alternatives,truncated=_resolve(db,user,data,allowed_tools)
    limitations=['只读取当前用户可见且具备设计路线读取权限的项目。',
                 '本工具只核对设计、BOM、加工路线与关联上下文，不生成图纸、不上传成果、不替代ERP设计/BOM登记。',
                 '采购、加工、装配、试模和交付执行仍须以对应业务工具、ERP回执或人工审批材料为准。']
    if truncated:limitations.append('最多检查前500个可见项目，结果可能未覆盖全部可见范围。')
    if project:
        designs=_visible_subjects(db,user,[project.id],'design_route',allowed_tools)[:20]
        materials=_material_cards(db,designs)
        tasks=_linked_tasks(db,user,designs,allowed_tools)
        contacts=_contact_impacts(db,user,project.id,materials,tasks,designs,allowed_tools)
        skipped=[]
        if 'query_project_plan_context' not in allowed_tools and 'query_project_plan' not in allowed_tools:skipped.append('计划任务详情')
        if 'query_contact_cases' not in allowed_tools:skipped.append('工程联络影响')
        if skipped:limitations.append('未分配对应查询工具，未返回：'+'、'.join(skipped))
        return {'resolution':'RESOLVED','data':[{'project':_project_card(db,user,project,alternatives or ('项目定位',)),
            'profile':_profile(db,user,project.id),'design_routes':designs,'analysis':_analysis(project,designs,materials,tasks,contacts,allowed_tools)}],
            'source':'agent_db','as_of':now().isoformat(),'limitations':limitations}
    if alternatives is None:
        return {'resolution':'NOT_FOUND_OR_FORBIDDEN','data':[],'source':'agent_db','as_of':now().isoformat(),
            'limitations':limitations}
    if alternatives:
        return {'resolution':'MULTIPLE_CANDIDATES','data':alternatives,'source':'agent_db','as_of':now().isoformat(),
            'limitations':limitations+['线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。']}
    return {'resolution':'NOT_FOUND','data':[],'source':'agent_db','as_of':now().isoformat(),
        'limitations':limitations}
