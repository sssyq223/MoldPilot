from datetime import timedelta
from decimal import Decimal
import secrets
from sqlalchemy import select, exists, and_
from . import bpm
from .db import now,aware
from .models import (Project, Material, PurchaseRequest, PurchaseLine, User, WorkflowDefinition,
                     ApprovalInstance, ApprovalSeat, ApprovalAction, HumanIntent)
from .models import BusinessSubject
from .authorization import require, predicate, select_fields, access
from .errors import DomainError
from .events import record
from .security import digest


def request_lines(db, req):
    return list(db.execute(select(PurchaseLine, Material).join(Material).where(PurchaseLine.request_id == req.id)))


def request_access(db, user, req, permission):
    if isinstance(req, BusinessSubject):
        from .domains import authorize
        return authorize(db,user,req,permission.rsplit('.',1)[-1])
    lines = request_lines(db, req)
    if not lines: raise DomainError("INCOMPLETE", "申请缺少明细")
    fields = None
    for line, material in lines:
        allowed = require(db, user, permission, {"project_id": req.project_id, "category": material.category})
        fields = allowed if fields is None else fields & allowed
    return fields or frozenset()


def request_data(db, user, req):
    allowed = request_access(db, user, req, "purchase.read")
    data = {"id": req.id, "number": req.number, "project_id": req.project_id, "remark": req.remark,
            "status": req.status, "revision": req.revision, "created_by": req.created_by,
            "created_at": req.created_at.isoformat()}
    data = select_fields(data, allowed)
    data["lines"] = [select_fields({"material_id": m.id, "material_name": m.name, "category": m.category,
                                  "quantity": str(l.quantity), "unit": m.unit, "due_date": str(l.due_date)},
                                 access(db, user, "purchase.read", {"project_id": req.project_id, "category": m.category}).fields)
                     for l, m in request_lines(db, req)]
    return data


def visible_requests(db, user):
    allowed = predicate(db, user, "purchase.read", {"project_id": PurchaseRequest.project_id, "category": Material.category})
    # Complete-document view only. Never expose a mixed document's header or totals.
    forbidden = exists(select(PurchaseLine.id).join(Material).where(PurchaseLine.request_id == PurchaseRequest.id, ~allowed))
    has_lines = exists(select(PurchaseLine.id).where(PurchaseLine.request_id == PurchaseRequest.id))
    return select(PurchaseRequest).where(has_lines, ~forbidden)


def create_request(db, user, data):
    project = db.get(Project, data.project_id)
    if not project: raise DomainError("NOT_FOUND", "项目不存在或无权访问", 404)
    materials = []
    for line in data.lines:
        material = db.get(Material, line.material_id)
        if not material: raise DomainError("MATERIAL_UNKNOWN", "物料未登记，请由人员核对")
        require(db, user, "purchase.create", {"project_id": project.id, "category": material.category})
        materials.append(material)
    req = PurchaseRequest(number="PR-"+secrets.token_hex(6).upper(), project_id=project.id, created_by=user.id, remark=data.remark)
    db.add(req); db.flush()
    for line in data.lines:
        db.add(PurchaseLine(request_id=req.id, material_id=line.material_id, quantity=line.quantity, due_date=line.due_date))
    record(db, user, "purchase.draft.created", req.id)
    db.flush()
    return req


def enter_stage(db, instance, definition, req):
    node = definition.config["nodes"][instance.stage_index]
    from .assignments import resolve_users
    sources=[];candidates=[]
    try:candidates,sources=resolve_users(db,node)
    except DomainError:
        instance.incident='ASSIGNMENT_BLOCKED'
    resolution={'node':node['key'],'resolved_at':now().isoformat(),'sources':sources,
                'rule':node.get('assignment',{'users':node.get('users',[])}),'candidates':candidates}
    eligible = []
    for user_id in candidates:
        candidate = db.get(User, user_id)
        if not candidate or not candidate.active: continue
        try:
            request_access(db, candidate, req, "purchase.approve")
            fields=request_access(db, candidate, req, "purchase.read")
            required={'project_id','detail','remark'} if isinstance(req,BusinessSubject) else {'project_id','material_id','quantity','due_date','remark'}
            if '*' not in fields and not required<=fields:continue
            eligible.append(user_id)
        except DomainError: continue
    resolution['eligible']=eligible
    resolution['blocked']=not eligible or (node['mode']=='ALL' and len(eligible)!=len(candidates))
    instance.assignment_snapshots={**(instance.assignment_snapshots or {}),str(instance.stage_index):resolution}
    if resolution['blocked']:
        instance.incident = "ASSIGNMENT_BLOCKED"
        record(db, None, "approval.assignment.blocked", instance.id)
        return
    instance.incident=None
    for user_id in eligible:
        db.add(ApprovalSeat(instance_id=instance.id, stage_index=instance.stage_index, user_id=user_id))
    record(db, None, "approval.pending", instance.id, recipients=eligible)


def submit_request(db, user, req_id, revision, definition_id):
    req = db.scalar(select(PurchaseRequest).where(PurchaseRequest.id == req_id).with_for_update())
    if not req: raise DomainError("NOT_FOUND", "申请不存在或无权访问", 404)
    request_access(db, user, req, "purchase.submit")
    if req.revision != revision: raise DomainError("VERSION_CONFLICT", "申请已变化，请重新核对", 409)
    if req.status not in {"DRAFT", "REJECTED", "RETURNED"}: raise DomainError("INVALID_STATE", "当前状态不能提交", 409)
    from .workflow_selection import require_template
    definition = require_template(db, user, req, definition_id)
    project = db.get(Project, req.project_id)
    if project.status in {"PAUSED", "TERMINATED", "CLOSED"}: raise DomainError("PROJECT_BLOCKED", "项目当前状态禁止采购流转")
    if req.status != "DRAFT": req.revision += 1
    req.status, req.round_no = "SUBMITTED", req.round_no + 1
    snapshot = {"request_id": req.id, "number": req.number, "project_id": req.project_id,
                "remark": req.remark, "submitted_at": now().isoformat(), "submitter": {
                    "id": user.id, "username": user.username, "name": user.display_name, "department": user.department},
                "lines": [{"material_id": m.id, "material_name": m.name, "category": m.category,
                           "quantity": str(l.quantity), "unit": m.unit, "due_date": str(l.due_date)} for l, m in request_lines(db, req)]}
    instance = ApprovalInstance(request_id=req.id, definition_id=definition.id, revision=req.revision,
                                round_no=req.round_no, snapshot=snapshot, snapshot_hash=bpm.content_hash(snapshot),
                                engine_state=bpm.start_engine(definition.bpmn_xml))
    db.add(instance); db.flush()
    enter_stage(db, instance, definition, req)
    record(db, user, "purchase.submitted", req.id, {"instance_id": instance.id})
    return {"request_id": req.id, "instance_id": instance.id, "status": "SUBMITTED"}


def approval_detail(db, user, instance):
    req = load_subject(db,instance)
    fields = request_access(db, user, req, "purchase.read")
    definition = db.get(WorkflowDefinition, instance.definition_id)
    seats = list(db.scalars(select(ApprovalSeat).where(ApprovalSeat.instance_id == instance.id)))
    current = next((s for s in seats if s.user_id == user.id and s.status == "PENDING" and s.stage_index == instance.stage_index), None)
    matched, missing = bpm.reject_findings(definition.config["nodes"][min(instance.stage_index, len(definition.config["nodes"])-1)], instance.snapshot,definition.config.get('material_contract'))
    actions = []
    if current and instance.status == "RUNNING":
        try:
            request_access(db, user, req, "purchase.approve")
            actions = ["REJECT"] if matched else ["REJECT", "RETURN"] if missing else ["APPROVE", "REJECT", "RETURN"]
        except DomainError: pass
    # Approval requires a complete, authorized material snapshot, not merely an assigned seat.
    required = {"project_id", "material_id", "quantity", "due_date", "remark"} if instance.request_id else {'project_id','detail','remark'}
    complete = "*" in fields or required <= fields
    material_notice=None
    if isinstance(req,BusinessSubject) and req.kind=='contact_resolution' and instance.status=='RUNNING':
        from .contact_lifecycle import ensure_materials
        try:
            from .models import ContactResolution
            ensure_materials(db,db.get(ContactResolution,req.id))
        except DomainError:
            actions=[a for a in actions if a in {'REJECT','RETURN'}]
            material_notice='联络单事项或附件已变化，请驳回或退回后重新核对方案。'
    if not complete: actions = []
    snapshot = {**instance.snapshot, "lines": [select_fields(l, fields) for l in instance.snapshot["lines"]]}
    if "remark" not in fields and "*" not in fields: snapshot.pop("remark", None)
    if isinstance(req,BusinessSubject):
        metadata={k:snapshot[k] for k in ('submitter','submitted_at') if k in snapshot}
        snapshot={**select_fields(snapshot,fields),**metadata,'lines':[]}
    elif '*' not in fields:
        for key in ('project_id','number','remark'):
            if key not in fields:snapshot.pop(key,None)
    return {"id": instance.id, "status": instance.status, "revision": instance.revision,
            "version": instance.version, "snapshot": snapshot, "snapshot_hash": instance.snapshot_hash,
            "business_type": 'purchase_request' if instance.request_id else req.kind,"material_notice":material_notice,
            "definition": {"name": definition.name, "version": definition.version},
            "nodes": [{"name": n["name"], "key": n["key"], "mode": n["mode"]} for n in definition.config["nodes"]],
            "stage_index": instance.stage_index, "incident": instance.incident,
            "assigned_people": [{"name":db.get(User,s.user_id).display_name,"stage_index":s.stage_index,"status":s.status} for s in seats],
            "seat_id": current.id if current else None, "seat_version": current.version if current else None,
            "allowed_actions": actions, "rejection_reasons": matched if complete else [],
            "missing_rules": missing if complete else [], "materials_complete": complete,
            "history": [{"user": a.user_snapshot, "decision": a.decision, "comment": a.comment,
                         "at": a.created_at.isoformat()} for a in db.scalars(select(ApprovalAction).where(ApprovalAction.instance_id == instance.id).order_by(ApprovalAction.created_at))]}


def decide(db, user, payload):
    instance = db.scalar(select(ApprovalInstance).where(ApprovalInstance.id == payload["instance_id"]).with_for_update())
    if not instance: raise DomainError("NOT_FOUND", "审批不存在", 404)
    req = load_subject(db,instance,lock=True)
    request_access(db, user, req, "purchase.approve")
    detail = approval_detail(db, user, instance)
    if instance.version != payload["version"] or instance.snapshot_hash != payload["snapshot_hash"]:
        raise DomainError("VERSION_CONFLICT", "审批资料或节点已变化", 409)
    if payload["decision"] not in detail["allowed_actions"]:
        raise DomainError("RULE_BLOCKED", "当前任务不允许此决定，请核对权限与驳回条件", 409)
    seat = db.get(ApprovalSeat, detail["seat_id"])
    if seat.id != payload["seat_id"] or seat.version != payload["seat_version"]:
        raise DomainError("VERSION_CONFLICT", "审批席位已变化", 409)
    seat.status, seat.version = payload["decision"], seat.version + 1
    db.add(ApprovalAction(instance_id=instance.id, seat_id=seat.id, user_id=user.id,
                         user_snapshot={"name": user.display_name, "username": user.username, "department": user.department},
                         decision=payload["decision"], comment=payload["comment"], snapshot_hash=instance.snapshot_hash))
    definition = db.get(WorkflowDefinition, instance.definition_id)
    node = definition.config["nodes"][instance.stage_index]
    db.flush()
    peers = list(db.scalars(select(ApprovalSeat).where(ApprovalSeat.instance_id == instance.id, ApprovalSeat.stage_index == instance.stage_index)))
    if payload["decision"] in {"REJECT", "RETURN"}:
        instance.status = "REJECTED" if payload["decision"] == "REJECT" else "RETURNED"
        req.status = instance.status
        if isinstance(req,BusinessSubject):
            from .domains import release_reservation
            release_reservation(db,req)
        for peer in peers:
            if peer.status == "PENDING": peer.status = "CANCELLED"
    elif node["mode"] == "ANY" or all(s.status == "APPROVE" for s in peers):
        for peer in peers:
            if peer.status == "PENDING": peer.status = "CANCELLED"
        target = bpm.route_target(definition.config, instance.stage_index, instance.snapshot)
        instance.engine_state = bpm.advance_engine(instance.engine_state, node["key"], target if 'routes' in node else None)
        instance.stage_index = bpm.current_stage(instance.engine_state, definition.config)
        record(db, user, 'approval.routed', instance.id, {'from': node['key'], 'to': target, 'snapshot_hash': instance.snapshot_hash})
        if instance.stage_index == len(definition.config["nodes"]):
            instance.status, req.status = "COMPLETED", "APPROVED"
            if isinstance(req,BusinessSubject):
                from .domains import apply
                try:
                    with db.begin_nested():apply(db,user,req)
                except DomainError as error:
                    req.status='APPLY_BLOCKED';instance.incident=error.code
            else:
                from .procurement import create_execution_order
                create_execution_order(db,req)
        else: enter_stage(db, instance, definition, req)
    instance.version += 1
    record(db, user, "approval.decided", instance.id, {"decision": payload["decision"], "business_status": req.status}, [req.created_by])
    return {"instance_id": instance.id, "status": instance.status, "business_status": req.status}


def create_intent(db, user, action, resource_id, payload):
    if action == "approval.decide":
        instance = db.get(ApprovalInstance, resource_id)
        if not instance: raise DomainError("NOT_FOUND", "审批不存在", 404)
        detail = approval_detail(db, user, instance)
        if payload["decision"] not in detail["allowed_actions"]: raise DomainError("RULE_BLOCKED", "当前任务不允许此决定", 409)
    elif action == "purchase.submit":
        req = db.get(PurchaseRequest, resource_id)
        if not req: raise DomainError("NOT_FOUND", "申请不存在", 404)
        request_access(db, user, req, "purchase.submit")
        from .workflow_selection import require_template
        require_template(db, user, req, payload['definition_id'])
    elif action == 'business.submit':
        from .domains import authorize
        subject=db.get(BusinessSubject,resource_id)
        if not subject:raise DomainError('NOT_FOUND','单据不存在',404)
        authorize(db,user,subject,'submit')
        from .workflow_selection import require_template
        require_template(db, user, subject, payload['definition_id'])
    elif action == 'contact.execute':
        from .contact_tools import validate_intent
        validate_intent(db,user,payload)
    elif action == 'project_control.execute':
        from .project_control_tools import validate_intent
        validate_intent(db,user,payload)
    elif action == 'project_closure.execute':
        from .project_closure_tools import validate_intent
        validate_intent(db,user,payload)
    elif action.startswith('domain.'):
        from .domain_commands import validate_command
        validate_command(db,user,action[7:],resource_id,payload)
    else: raise DomainError("ACTION_UNKNOWN", "未登记的人工动作")
    challenge = secrets.token_urlsafe(32)
    intent = HumanIntent(user_id=user.id, action=action, resource_id=resource_id, payload=payload,
                         payload_hash=bpm.content_hash(payload), challenge_hash=digest(challenge), expires_at=now()+timedelta(minutes=5))
    db.add(intent); db.flush()
    return {"id": intent.id, "challenge": challenge, "payload": payload, "expires_at": intent.expires_at.isoformat()}


def confirm_intent(db, user, intent_id, challenge):
    intent = db.scalar(select(HumanIntent).where(HumanIntent.id == intent_id, HumanIntent.user_id == user.id).with_for_update())
    if not intent or not secrets.compare_digest(intent.challenge_hash, digest(challenge)):
        raise DomainError("CONFIRMATION_INVALID", "确认凭证无效", 403)
    if intent.receipt is not None: return intent.receipt
    if aware(intent.expires_at) <= now(): raise DomainError("CONFIRMATION_EXPIRED", "请重新核对并确认", 409)
    if intent.payload_hash != bpm.content_hash(intent.payload): raise DomainError("CONFIRMATION_INVALID", "确认内容不一致", 409)
    if intent.action == "approval.decide": result = decide(db, user, intent.payload)
    elif intent.action=='purchase.submit': result = submit_request(db, user, intent.resource_id, **intent.payload)
    elif intent.action=='business.submit': result=submit_subject(db,user,intent.resource_id,**intent.payload)
    elif intent.action=='contact.execute':
        from .contact_tools import confirm
        result=confirm(db,user,intent.payload)
    elif intent.action=='project_control.execute':
        from .project_control_tools import confirm
        result=confirm(db,user,intent.payload)
    elif intent.action=='project_closure.execute':
        from .project_closure_tools import confirm
        result=confirm(db,user,intent.payload)
    elif intent.action.startswith('domain.'):
        from .domain_commands import execute_command
        result=execute_command(db,user,intent.action[7:],intent.resource_id,intent.payload)
    else:raise DomainError('ACTION_UNKNOWN','未登记的人工动作')
    intent.receipt = result
    record(db, user, "human.confirmed", intent.id, {"action": intent.action, "payload_hash": intent.payload_hash})
    return result


def load_subject(db,instance,lock=False):
    model,identifier=(PurchaseRequest,instance.request_id) if instance.request_id else (BusinessSubject,instance.subject_id)
    q=select(model).where(model.id==identifier)
    return db.scalar(q.with_for_update() if lock else q)


def submit_subject(db,user,subject_id,revision,definition_id):
    from . import domains
    subject=db.scalar(select(BusinessSubject).where(BusinessSubject.id==subject_id).with_for_update())
    if not subject:raise DomainError('NOT_FOUND','业务单据不存在',404)
    domains.authorize(db,user,subject,'submit')
    if subject.revision!=revision:raise DomainError('VERSION_CONFLICT','业务资料已变化',409)
    if subject.status not in {'DRAFT','REJECTED','RETURNED'}:raise DomainError('INVALID_STATE','当前状态不能提交',409)
    from .workflow_selection import require_template
    definition = require_template(db, user, subject, definition_id)
    domains.before_submit(db,user,subject)
    if subject.status!='DRAFT':subject.revision+=1
    subject.status='SUBMITTED';subject.round_no+=1
    detail=domains.typed_detail(db,subject)
    snapshot={**domains.values(subject),'detail':detail,'lines':[],
              'amount':detail.get('amount'),'currency':detail.get('currency'),
              'submitted_at':now().isoformat(),'submitter':{'id':user.id,'username':user.username,'name':user.display_name,'department':user.department}}
    instance=ApprovalInstance(subject_id=subject.id,definition_id=definition.id,revision=subject.revision,
                              round_no=subject.round_no,snapshot=snapshot,snapshot_hash=bpm.content_hash(snapshot),
                              engine_state=bpm.start_engine(definition.bpmn_xml))
    db.add(instance);db.flush();enter_stage(db,instance,definition,subject)
    record(db,user,'business.submitted',subject.id,{'instance_id':instance.id})
    return {'subject_id':subject.id,'instance_id':instance.id,'status':subject.status}
