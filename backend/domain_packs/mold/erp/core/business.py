from datetime import timedelta
from decimal import Decimal
import secrets
from sqlalchemy import select, exists, and_, or_, func
from domain_packs.mold import bpm
from domain_packs.mold.ports.db import now,aware
from domain_packs.mold.models import (Project, Material, PurchaseRequest, PurchaseLine, User, WorkflowDefinition,
                     ApprovalInstance, ApprovalSeat, ApprovalAction, AgentApprovalDelegation,
                     ApprovalProxyDelegation, HumanIntent,
                     MaterialBinding, AuditEvent)
from domain_packs.mold.models import BusinessSubject
from domain_packs.mold.authorization import require, predicate, select_fields, access
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.events import record
from domain_packs.mold.ports.security import digest
from domain_packs.mold.ports.proposal_registry import handler_for_action
from agent_core.domain_pack import resource_contract


def request_lines(db, req):
    return list(db.execute(select(PurchaseLine, Material).join(Material).where(PurchaseLine.request_id == req.id)))


def request_access(db, user, req, permission):
    if isinstance(req, BusinessSubject):
        from domain_packs.mold.erp.core.domains import authorize
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
    from domain_packs.mold.ports.assignments import resolve_users
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


def _active_delegation(db, user_id, definition, node):
    if not node.get("agent_auto_approval"):
        return None
    current = now()
    return db.scalar(select(AgentApprovalDelegation).where(
        AgentApprovalDelegation.user_id == user_id,
        AgentApprovalDelegation.process_key == definition.process_key,
        AgentApprovalDelegation.node_key == node["key"],
        AgentApprovalDelegation.decision == "APPROVE",
        AgentApprovalDelegation.active.is_(True),
        AgentApprovalDelegation.revoked_at.is_(None),
        or_(AgentApprovalDelegation.valid_from.is_(None), AgentApprovalDelegation.valid_from <= current),
        or_(AgentApprovalDelegation.valid_to.is_(None), AgentApprovalDelegation.valid_to > current),
    ))


def _agent_auto_policy_allows(definition, node, snapshot):
    policy = node.get("agent_auto_policy")
    if not policy:
        return True
    result = bpm.evaluate_snapshot(policy["condition"], snapshot, definition.config.get("material_contract"))
    return result is True


def process_agent_auto_approvals(db, instance_id, agent_permission_mode="ask", limit=12):
    """Apply explicit user delegations for auto-approvable nodes only.

    This is not a model decision. It uses the same approval_detail/decide gates as
    a human decision and only fills the current user's already assigned seat.
    """
    if agent_permission_mode != "delegated_auto":
        return []
    applied = []
    for _ in range(limit):
        instance = db.scalar(select(ApprovalInstance).where(ApprovalInstance.id == instance_id).with_for_update())
        if not instance or instance.status != "RUNNING":
            break
        definition = db.get(WorkflowDefinition, instance.definition_id)
        if instance.stage_index >= len(definition.config["nodes"]):
            break
        node = definition.config["nodes"][instance.stage_index]
        if not node.get("agent_auto_approval"):
            break
        if not _agent_auto_policy_allows(definition, node, instance.snapshot):
            break
        seats = list(db.scalars(select(ApprovalSeat).where(
            ApprovalSeat.instance_id == instance.id,
            ApprovalSeat.stage_index == instance.stage_index,
            ApprovalSeat.status == "PENDING",
            ApprovalSeat.parent_seat_id.is_(None),
        ).order_by(ApprovalSeat.created_at, ApprovalSeat.id)))
        if not seats:
            break
        progressed = False
        for seat in seats:
            delegated_user = db.get(User, seat.user_id)
            delegation = _active_delegation(db, seat.user_id, definition, node)
            if not delegated_user or not delegation:
                continue
            try:
                detail = approval_detail(db, delegated_user, instance)
                if "APPROVE" not in detail["allowed_actions"]:
                    continue
                payload = {
                    "instance_id": instance.id,
                    "seat_id": detail["seat_id"],
                    "seat_version": detail["seat_version"],
                    "version": detail["version"],
                    "snapshot_hash": detail["snapshot_hash"],
                    "decision": "APPROVE",
                    "comment": "Agent 根据用户预授权自动同意；未跳过权限、材料和节点规则校验。",
                }
                result = _decide(db, delegated_user, payload, actor_type="AGENT_DELEGATED", delegation_id=delegation.id)
                applied.append({"delegation_id": delegation.id, "user_id": delegated_user.id, "node": node["key"], "result": result})
                progressed = True
                break
            except DomainError:
                continue
        if not progressed:
            break
    return applied


def bind_material_snapshot(db, user, resource_type, resource_id, resource_revision, definition, material_review_id):
    if resource_type not in resource_contract().APPROVAL_RESOURCE_TYPES:
        raise DomainError("RESOURCE_TYPE_UNKNOWN", "当前业务包未登记该审批资源类型")
    from domain_packs.mold.erp.core.workflow_selection import validate_material_review
    review = validate_material_review(db, user, definition, material_review_id)
    if not review:
        return None
    material_hash = bpm.content_hash(review.material_data)
    binding = MaterialBinding(resource_type=resource_type, resource_id=resource_id, resource_revision=resource_revision,
        definition_id=definition.id, template_id=review.template_id, review_id=review.id,
        material_hash=material_hash, review_hash=review.review_hash, bound_by=user.id)
    db.add(binding); db.flush()
    return {'binding_id':binding.id,'review_id':review.id,'template_id':review.template_id,
            'template_hash':review.template_hash,'mapping_id':review.mapping_id,'mapping_hash':review.mapping_hash,
            'file_id':review.file_id,'file_sha256':review.file_sha256,'review_hash':review.review_hash,
            'material_hash':material_hash,'confirmed_by':review.confirmed_by,
            'confirmed_at':review.confirmed_at.isoformat() if review.confirmed_at else None,
            'material_data':review.material_data}


def submit_request(db, user, req_id, revision, definition_id, material_review_id=None, agent_permission_mode="ask"):
    req = db.scalar(select(PurchaseRequest).where(PurchaseRequest.id == req_id).with_for_update())
    if not req: raise DomainError("NOT_FOUND", "申请不存在或无权访问", 404)
    request_access(db, user, req, "purchase.submit")
    if req.revision != revision: raise DomainError("VERSION_CONFLICT", "申请已变化，请重新核对", 409)
    if req.status not in {"DRAFT", "REJECTED", "RETURNED"}: raise DomainError("INVALID_STATE", "当前状态不能提交", 409)
    from domain_packs.mold.erp.core.workflow_selection import require_template
    definition = require_template(db, user, req, definition_id, material_review_id)
    project = db.get(Project, req.project_id)
    if project.status in {"PAUSED", "TERMINATED", "CLOSED"}: raise DomainError("PROJECT_BLOCKED", "项目当前状态禁止采购流转")
    if req.status != "DRAFT": req.revision += 1
    req.status, req.round_no = "SUBMITTED", req.round_no + 1
    material = bind_material_snapshot(db, user, 'purchase_request', req.id, req.revision, definition, material_review_id)
    snapshot = {"request_id": req.id, "number": req.number, "project_id": req.project_id,
                "remark": req.remark, "submitted_at": now().isoformat(), "submitter": {
                    "id": user.id, "username": user.username, "name": user.display_name, "department": user.department},
                "lines": [{"material_id": m.id, "material_name": m.name, "category": m.category,
                           "quantity": str(l.quantity), "unit": m.unit, "due_date": str(l.due_date)} for l, m in request_lines(db, req)]}
    if material:
        snapshot['material_data']=material.pop('material_data')
        snapshot['material_binding']=material
    instance = ApprovalInstance(resource_type="purchase_request", resource_id=req.id,
                                definition_id=definition.id, revision=req.revision,
                                round_no=req.round_no, snapshot=snapshot, snapshot_hash=bpm.content_hash(snapshot),
                                engine_state=bpm.start_engine(definition.bpmn_xml))
    db.add(instance); db.flush()
    enter_stage(db, instance, definition, req)
    auto_approved = process_agent_auto_approvals(db, instance.id, agent_permission_mode)
    record(db, user, "purchase.submitted", req.id, {"instance_id": instance.id})
    return {"request_id": req.id, "instance_id": instance.id, "status": req.status, "agent_auto_approved": auto_approved}


def approval_detail(db, user, instance):
    req = load_subject(db,instance)
    fields = request_access(db, user, req, "purchase.read")
    definition = db.get(WorkflowDefinition, instance.definition_id)
    seats = list(db.scalars(select(ApprovalSeat).where(ApprovalSeat.instance_id == instance.id)))
    current = next((s for s in seats if s.user_id == user.id and s.status == "PENDING" and s.stage_index == instance.stage_index), None)
    proxy_delegation = None
    if not current and instance.status == "RUNNING" and instance.stage_index < len(definition.config["nodes"]):
        node = definition.config["nodes"][instance.stage_index]
        for candidate_seat in sorted(seats, key=lambda item: (item.created_at, item.id)):
            if candidate_seat.status != "PENDING" or candidate_seat.stage_index != instance.stage_index:
                continue
            proxy_delegation = active_approval_proxy(
                db, user, candidate_seat.user_id, definition, node, req
            )
            if proxy_delegation:
                current = candidate_seat
                break
    matched, missing = bpm.reject_findings(definition.config["nodes"][min(instance.stage_index, len(definition.config["nodes"])-1)], instance.snapshot,definition.config.get('material_contract'))
    actions = []
    if current and instance.status == "RUNNING":
        try:
            request_access(db, user, req, "purchase.approve")
            actions = ["REJECT"] if matched else ["REJECT", "RETURN"] if missing else ["APPROVE", "REJECT", "RETURN"]
        except DomainError: pass
    if proxy_delegation:
        actions = [action for action in actions if action in proxy_delegation.allowed_decisions]
    # Approval requires a complete, authorized material snapshot, not merely an assigned seat.
    required = ({"project_id", "material_id", "quantity", "due_date", "remark"}
                if instance.resource_type == "purchase_request" else {'project_id','detail','remark'})
    complete = "*" in fields or required <= fields
    material_notice=None
    if isinstance(req,BusinessSubject) and req.kind=='contact_resolution' and instance.status=='RUNNING':
        from domain_packs.mold.erp.change.contact_lifecycle import ensure_materials
        try:
            from domain_packs.mold.models import ContactResolution
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
    transfer_events = list(db.scalars(select(AuditEvent).where(
        AuditEvent.resource_id == instance.id,
        AuditEvent.action == "approval.seat.transferred",
    ).order_by(AuditEvent.created_at)))
    add_sign_events = list(db.scalars(select(AuditEvent).where(
        AuditEvent.resource_id == instance.id,
        AuditEvent.action == "approval.seat.added",
    ).order_by(AuditEvent.created_at)))
    transfer_options = approval_transfer_options(db, user, instance, req, definition, current) if current and not proxy_delegation else []
    add_sign_policy = node_add_sign_policy(definition, instance)
    add_sign_allowed = bool(current and not proxy_delegation and "APPROVE" in actions and add_sign_policy)
    add_sign_options = approval_add_sign_options(db, user, instance, req, definition, current) if add_sign_allowed else []
    return_options = node_return_options(definition, instance) if current and "RETURN" in actions else []
    return {"id": instance.id, "status": instance.status, "revision": instance.revision,
            "version": instance.version, "snapshot": snapshot, "snapshot_hash": instance.snapshot_hash,
            "business_type": ('purchase_request' if instance.resource_type == 'purchase_request' else req.kind),
            "material_notice":material_notice,
            "definition": {"name": definition.name, "version": definition.version},
            "nodes": [{"name": n["name"], "key": n["key"], "mode": n["mode"]} for n in definition.config["nodes"]],
            "stage_index": instance.stage_index, "incident": instance.incident,
            "assigned_people": [{"name":db.get(User,s.user_id).display_name,"stage_index":s.stage_index,
                                 "status":s.status,"seat_id":s.id,"parent_seat_id":s.parent_seat_id,
                                 "countersign_timing":s.countersign_timing} for s in seats],
            "seat_id": current.id if current else None, "seat_version": current.version if current else None,
            "proxy_delegation": approval_proxy_context(db, proxy_delegation) if proxy_delegation else None,
            "return_options": return_options,
            "transfer_allowed": bool(current and not proxy_delegation and node_allows_transfer(definition, instance)),
            "transfer_options": transfer_options,
            "add_sign_allowed": add_sign_allowed,
            "add_sign_timings": add_sign_policy["timings"] if add_sign_allowed else [],
            "add_sign_options": add_sign_options,
            "allowed_actions": actions, "rejection_reasons": matched if complete else [],
            "missing_rules": missing if complete else [], "materials_complete": complete,
            "history": [{"user": a.user_snapshot, "decision": a.decision, "comment": a.comment,
                         "decision_context": a.decision_context or {},
                         "at": a.created_at.isoformat()} for a in db.scalars(select(ApprovalAction).where(ApprovalAction.instance_id == instance.id).order_by(ApprovalAction.created_at))],
            "transfer_history": [{**event.detail, "at": event.created_at.isoformat()} for event in transfer_events],
            "add_sign_history": [{**event.detail, "at": event.created_at.isoformat()} for event in add_sign_events]}


def node_return_options(definition, instance):
    if instance.stage_index >= len(definition.config["nodes"]):
        return []
    node = definition.config["nodes"][instance.stage_index]
    targets = node.get("return_policy", {}).get("targets", ["applicant"])
    names = {item["key"]: item["name"] for item in definition.config["nodes"]}
    return [{"key": target, "name": "申请人修改" if target == "applicant" else names[target]}
            for target in targets]


def active_approval_proxy(db, actor, principal_user_id, definition, node, req):
    if not node.get("allow_proxy") or actor.id == principal_user_id or req.created_by == actor.id:
        return None
    principal = db.get(User, principal_user_id)
    if not principal or not principal.active or not actor.active:
        return None
    current = now()
    return db.scalar(select(ApprovalProxyDelegation).where(
        ApprovalProxyDelegation.principal_user_id == principal_user_id,
        ApprovalProxyDelegation.proxy_user_id == actor.id,
        ApprovalProxyDelegation.process_key == definition.process_key,
        ApprovalProxyDelegation.node_key == node["key"],
        ApprovalProxyDelegation.active.is_(True),
        ApprovalProxyDelegation.revoked_at.is_(None),
        or_(ApprovalProxyDelegation.valid_from.is_(None), ApprovalProxyDelegation.valid_from <= current),
        or_(ApprovalProxyDelegation.valid_to.is_(None), ApprovalProxyDelegation.valid_to > current),
    ).order_by(ApprovalProxyDelegation.created_at.desc()))


def approval_proxy_context(db, delegation):
    principal = db.get(User, delegation.principal_user_id)
    return {"id": delegation.id,
            "principal_user": {"id": principal.id, "display_name": principal.display_name,
                               "department": principal.department},
            "allowed_decisions": delegation.allowed_decisions,
            "reason": delegation.reason,
            "valid_from": delegation.valid_from.isoformat() if delegation.valid_from else None,
            "valid_to": delegation.valid_to.isoformat() if delegation.valid_to else None}


def node_allows_transfer(definition, instance):
    return (
        instance.status == "RUNNING"
        and instance.stage_index < len(definition.config["nodes"])
        and definition.config["nodes"][instance.stage_index].get("allow_transfer") is True
    )


def node_add_sign_policy(definition, instance):
    if instance.status != "RUNNING" or instance.stage_index >= len(definition.config["nodes"]):
        return None
    return definition.config["nodes"][instance.stage_index].get("add_sign_policy")


def _complete_approval_access(db, candidate, req):
    try:
        request_access(db, candidate, req, "purchase.approve")
        fields = request_access(db, candidate, req, "purchase.read")
        required = ({"project_id", "detail", "remark"} if isinstance(req, BusinessSubject)
                    else {"project_id", "material_id", "quantity", "due_date", "remark"})
        return "*" in fields or required <= fields
    except DomainError:
        return False


def approval_transfer_options(db, user, instance, req=None, definition=None, current=None):
    definition = definition or db.get(WorkflowDefinition, instance.definition_id)
    if not node_allows_transfer(definition, instance):
        return []
    req = req or load_subject(db, instance)
    current = current or db.scalar(select(ApprovalSeat).where(
        ApprovalSeat.instance_id == instance.id,
        ApprovalSeat.stage_index == instance.stage_index,
        ApprovalSeat.user_id == user.id,
        ApprovalSeat.status == "PENDING",
    ))
    if not current:
        return []
    occupied = set(db.scalars(select(ApprovalSeat.user_id).where(
        ApprovalSeat.instance_id == instance.id,
        ApprovalSeat.stage_index == instance.stage_index,
    )))
    candidates = list(db.scalars(select(User).where(
        User.active.is_(True),
        User.id.not_in(occupied),
    ).order_by(User.display_name, User.id)))
    return [{"id": candidate.id, "display_name": candidate.display_name, "department": candidate.department}
            for candidate in candidates if _complete_approval_access(db, candidate, req)]


def approval_add_sign_options(db, user, instance, req=None, definition=None, current=None):
    definition = definition or db.get(WorkflowDefinition, instance.definition_id)
    policy = node_add_sign_policy(definition, instance)
    if not policy:
        return []
    req = req or load_subject(db, instance)
    current = current or db.scalar(select(ApprovalSeat).where(
        ApprovalSeat.instance_id == instance.id,
        ApprovalSeat.stage_index == instance.stage_index,
        ApprovalSeat.user_id == user.id,
        ApprovalSeat.status == "PENDING",
    ))
    if not current:
        return []
    occupied = set(db.scalars(select(ApprovalSeat.user_id).where(
        ApprovalSeat.instance_id == instance.id,
        ApprovalSeat.stage_index == instance.stage_index,
    )))
    candidates = [db.get(User, user_id) for user_id in policy["users"] if user_id not in occupied]
    return [{"id": candidate.id, "display_name": candidate.display_name, "department": candidate.department}
            for candidate in candidates
            if candidate and candidate.active and _complete_approval_access(db, candidate, req)]


def transfer_approval_seat(db, user, payload):
    instance = db.scalar(select(ApprovalInstance).where(
        ApprovalInstance.id == payload["instance_id"]
    ).with_for_update())
    if not instance:
        raise DomainError("NOT_FOUND", "审批不存在", 404)
    req = load_subject(db, instance, lock=True)
    definition = db.get(WorkflowDefinition, instance.definition_id)
    if not node_allows_transfer(definition, instance):
        raise DomainError("TRANSFER_DISABLED", "当前审批节点未允许转交", 409)
    if instance.version != payload["version"] or instance.snapshot_hash != payload["snapshot_hash"]:
        raise DomainError("VERSION_CONFLICT", "审批资料或节点已变化", 409)
    seat = db.scalar(select(ApprovalSeat).where(
        ApprovalSeat.id == payload["seat_id"],
        ApprovalSeat.instance_id == instance.id,
    ).with_for_update())
    if (not seat or seat.stage_index != instance.stage_index or seat.user_id != user.id
            or seat.status != "PENDING" or seat.version != payload["seat_version"]):
        raise DomainError("VERSION_CONFLICT", "审批席位已变化", 409)
    options = {item["id"]: item for item in approval_transfer_options(
        db, user, instance, req=req, definition=definition, current=seat
    )}
    target = options.get(payload["target_user_id"])
    if not target:
        raise DomainError("TRANSFER_TARGET_INVALID", "目标人员不具备本审批的完整权限或已占用席位", 409)
    from_snapshot = {"id": user.id, "name": user.display_name, "department": user.department}
    previous_seat_version = seat.version
    seat.user_id = target["id"]
    seat.version += 1
    instance.version += 1
    snapshot_key = str(instance.stage_index)
    assignment_snapshot = dict((instance.assignment_snapshots or {}).get(snapshot_key, {}))
    transfers = list(assignment_snapshot.get("transfers", []))
    transfers.append({
        "seat_id": seat.id,
        "from_user": from_snapshot,
        "to_user": target,
        "reason": payload["reason"],
        "previous_seat_version": previous_seat_version,
        "seat_version": seat.version,
        "at": now().isoformat(),
    })
    assignment_snapshot["transfers"] = transfers
    instance.assignment_snapshots = {**(instance.assignment_snapshots or {}), snapshot_key: assignment_snapshot}
    detail = {
        "stage_index": instance.stage_index,
        "seat_id": seat.id,
        "from_user": from_snapshot,
        "to_user": target,
        "reason": payload["reason"],
        "previous_seat_version": previous_seat_version,
        "seat_version": seat.version,
        "instance_version": instance.version,
    }
    record(db, user, "approval.seat.transferred", instance.id, detail, [user.id, target["id"]])
    return {"instance_id": instance.id, "seat_id": seat.id, "status": instance.status,
            "transferred_to": target, "seat_version": seat.version, "version": instance.version}


def add_sign_approval_seat(db, user, payload):
    instance = db.scalar(select(ApprovalInstance).where(
        ApprovalInstance.id == payload["instance_id"]
    ).with_for_update())
    if not instance:
        raise DomainError("NOT_FOUND", "审批不存在", 404)
    req = load_subject(db, instance, lock=True)
    definition = db.get(WorkflowDefinition, instance.definition_id)
    policy = node_add_sign_policy(definition, instance)
    if not policy or payload["timing"] not in policy["timings"]:
        raise DomainError("ADD_SIGN_DISABLED", "当前审批节点未允许这种加签方式", 409)
    if instance.version != payload["version"] or instance.snapshot_hash != payload["snapshot_hash"]:
        raise DomainError("VERSION_CONFLICT", "审批资料或节点已变化", 409)
    seat = db.scalar(select(ApprovalSeat).where(
        ApprovalSeat.id == payload["seat_id"],
        ApprovalSeat.instance_id == instance.id,
    ).with_for_update())
    if (not seat or seat.stage_index != instance.stage_index or seat.user_id != user.id
            or seat.status != "PENDING" or seat.version != payload["seat_version"]):
        raise DomainError("VERSION_CONFLICT", "审批席位已变化", 409)
    detail = approval_detail(db, user, instance)
    options = {item["id"]: item for item in detail["add_sign_options"]}
    target = options.get(payload["target_user_id"])
    if not detail["add_sign_allowed"] or not target:
        raise DomainError("ADD_SIGN_TARGET_INVALID", "目标人员不在合格加签池内或当前规则不允许加签", 409)
    sequence = db.scalar(select(func.coalesce(func.max(ApprovalSeat.countersign_sequence), 0)).where(
        ApprovalSeat.instance_id == instance.id,
        ApprovalSeat.stage_index == instance.stage_index,
    )) + 1
    added = ApprovalSeat(
        instance_id=instance.id,
        stage_index=instance.stage_index,
        user_id=target["id"],
        status="PENDING" if payload["timing"] == "PRE" else "WAITING_PREDECESSOR",
        parent_seat_id=seat.id,
        countersign_timing=payload["timing"],
        countersign_initiated_by=user.id,
        countersign_reason=payload["reason"],
        countersign_sequence=sequence,
    )
    db.add(added)
    if payload["timing"] == "PRE":
        seat.status = "WAITING_COUNTERSIGN"
        seat.version += 1
    instance.version += 1
    db.flush()
    actor = {"id": user.id, "name": user.display_name, "department": user.department}
    addition = {
        "stage_index": instance.stage_index,
        "parent_seat_id": seat.id,
        "added_seat_id": added.id,
        "initiated_by": actor,
        "target_user": target,
        "timing": payload["timing"],
        "reason": payload["reason"],
        "sequence": sequence,
        "instance_version": instance.version,
        "at": now().isoformat(),
    }
    snapshot_key = str(instance.stage_index)
    assignment_snapshot = dict((instance.assignment_snapshots or {}).get(snapshot_key, {}))
    additions = list(assignment_snapshot.get("additions", []))
    additions.append(addition)
    assignment_snapshot["additions"] = additions
    instance.assignment_snapshots = {**(instance.assignment_snapshots or {}), snapshot_key: assignment_snapshot}
    record(db, user, "approval.seat.added", instance.id, addition,
           [target["id"]] if added.status == "PENDING" else [])
    return {"instance_id": instance.id, "seat_id": seat.id, "added_seat_id": added.id,
            "status": instance.status, "timing": payload["timing"],
            "added_user": target, "version": instance.version}


def _activate_after_approval(db, instance, seat):
    recipients = []
    if seat.countersign_timing == "PRE" and seat.parent_seat_id:
        parent = db.get(ApprovalSeat, seat.parent_seat_id)
        siblings = list(db.scalars(select(ApprovalSeat).where(
            ApprovalSeat.parent_seat_id == parent.id,
            ApprovalSeat.countersign_timing == "PRE",
        )))
        if parent.status == "WAITING_COUNTERSIGN" and all(item.status == "APPROVE" for item in siblings):
            parent.status = "PENDING"
            parent.version += 1
            recipients.append(parent.user_id)
    post_children = list(db.scalars(select(ApprovalSeat).where(
        ApprovalSeat.parent_seat_id == seat.id,
        ApprovalSeat.countersign_timing == "POST",
        ApprovalSeat.status == "WAITING_PREDECESSOR",
    )))
    for child in post_children:
        child.status = "PENDING"
        child.version += 1
        recipients.append(child.user_id)
    if recipients:
        record(db, None, "approval.pending", instance.id, recipients=sorted(set(recipients)))


def _stage_is_approved(node, peers):
    base = [seat for seat in peers if seat.parent_seat_id is None]
    additions = [seat for seat in peers if seat.parent_seat_id is not None]
    if any(seat.status != "APPROVE" for seat in additions):
        return False
    return (any(seat.status == "APPROVE" for seat in base)
            if node["mode"] == "ANY"
            else bool(base) and all(seat.status == "APPROVE" for seat in base))


def _decide(db, user, payload, actor_type="HUMAN", delegation_id=None):
    instance = db.scalar(select(ApprovalInstance).where(ApprovalInstance.id == payload["instance_id"]).with_for_update())
    if not instance: raise DomainError("NOT_FOUND", "审批不存在", 404)
    req = load_subject(db,instance,lock=True)
    request_access(db, user, req, "purchase.approve")
    detail = approval_detail(db, user, instance)
    if instance.version != payload["version"] or instance.snapshot_hash != payload["snapshot_hash"]:
        raise DomainError("VERSION_CONFLICT", "审批资料或节点已变化", 409)
    if payload["decision"] not in detail["allowed_actions"]:
        raise DomainError("RULE_BLOCKED", "当前任务不允许此决定，请核对权限与驳回条件", 409)
    return_target = None
    if payload["decision"] == "RETURN":
        return_target = next((item for item in detail["return_options"]
                              if item["key"] == payload.get("return_target_node_key")), None)
        if not return_target:
            raise DomainError("RETURN_TARGET_INVALID", "退回目标已变化或不在当前模板允许范围内", 409)
    elif payload.get("return_target_node_key"):
        raise DomainError("RETURN_TARGET_INVALID", "只有退回决定可以指定退回目标", 400)
    seat = db.get(ApprovalSeat, detail["seat_id"])
    if seat.id != payload["seat_id"] or seat.version != payload["seat_version"]:
        raise DomainError("VERSION_CONFLICT", "审批席位已变化", 409)
    seat.status, seat.version = payload["decision"], seat.version + 1
    proxy = detail.get("proxy_delegation")
    if proxy and actor_type == "HUMAN":
        actor_type = "HUMAN_PROXY"
        delegation_id = proxy["id"]
    user_snapshot = {"name": user.display_name, "username": user.username, "department": user.department, "actor_type": actor_type}
    if proxy:
        user_snapshot["principal_user"] = proxy["principal_user"]
        user_snapshot["proxy_reason"] = proxy["reason"]
    if delegation_id:
        user_snapshot["delegation_id"] = delegation_id
    decision_context = {"return_target": return_target} if return_target else {}
    db.add(ApprovalAction(instance_id=instance.id, seat_id=seat.id, user_id=user.id,
                         user_snapshot=user_snapshot,
                         decision=payload["decision"], comment=payload["comment"],
                         snapshot_hash=instance.snapshot_hash, decision_context=decision_context))
    definition = db.get(WorkflowDefinition, instance.definition_id)
    node = definition.config["nodes"][instance.stage_index]
    db.flush()
    peers = list(db.scalars(select(ApprovalSeat).where(ApprovalSeat.instance_id == instance.id, ApprovalSeat.stage_index == instance.stage_index)))
    if payload["decision"] in {"REJECT", "RETURN"}:
        instance.status = "REJECTED" if payload["decision"] == "REJECT" else "RETURNED"
        req.status = instance.status
        if isinstance(req,BusinessSubject):
            from domain_packs.mold.erp.core.domains import release_reservation
            release_reservation(db,req)
        for peer in peers:
            if peer.status in {"PENDING", "WAITING_COUNTERSIGN", "WAITING_PREDECESSOR"}: peer.status = "CANCELLED"
    else:
        _activate_after_approval(db, instance, seat)
        db.flush()
        peers = list(db.scalars(select(ApprovalSeat).where(
            ApprovalSeat.instance_id == instance.id,
            ApprovalSeat.stage_index == instance.stage_index,
        )))
    if payload["decision"] == "APPROVE" and _stage_is_approved(node, peers):
        for peer in peers:
            if peer.status in {"PENDING", "WAITING_COUNTERSIGN", "WAITING_PREDECESSOR"}: peer.status = "CANCELLED"
        target = bpm.route_target(definition.config, instance.stage_index, instance.snapshot)
        instance.engine_state = bpm.advance_engine(instance.engine_state, node["key"], target if 'routes' in node else None)
        instance.stage_index = bpm.current_stage(instance.engine_state, definition.config)
        record(db, user, 'approval.routed', instance.id, {'from': node['key'], 'to': target, 'snapshot_hash': instance.snapshot_hash})
        if instance.stage_index == len(definition.config["nodes"]):
            instance.status, req.status = "COMPLETED", "APPROVED"
            if isinstance(req,BusinessSubject):
                from domain_packs.mold.erp.core.domains import apply
                try:
                    with db.begin_nested():apply(db,user,req)
                except DomainError as error:
                    req.status='APPLY_BLOCKED';instance.incident=error.code
            else:
                from domain_packs.mold.erp.procurement.procurement import create_execution_order
                create_execution_order(db,req)
        else: enter_stage(db, instance, definition, req)
    instance.version += 1
    detail = {"decision": payload["decision"], "business_status": req.status, "actor_type": actor_type}
    if return_target:
        detail["return_target"] = return_target
    if proxy:
        detail["principal_user_id"] = proxy["principal_user"]["id"]
        detail["actor_user_id"] = user.id
    if delegation_id:
        detail["delegation_id"] = delegation_id
    record(db, user, "approval.decided", instance.id, detail, [req.created_by])
    return {"instance_id": instance.id, "status": instance.status, "business_status": req.status}


def decide(db, user, payload, agent_permission_mode="ask"):
    result = _decide(db, user, payload)
    auto_approved = process_agent_auto_approvals(db, payload["instance_id"], agent_permission_mode)
    return {**result, "agent_auto_approved": auto_approved}


def create_intent(db, user, action, resource_id, payload):
    if action == "approval.decide":
        instance = db.get(ApprovalInstance, resource_id)
        if not instance: raise DomainError("NOT_FOUND", "审批不存在", 404)
        detail = approval_detail(db, user, instance)
        if payload["decision"] not in detail["allowed_actions"]: raise DomainError("RULE_BLOCKED", "当前任务不允许此决定", 409)
        if payload["decision"] == "RETURN":
            options = {item["key"]: item for item in detail["return_options"]}
            target = payload.get("return_target_node_key")
            if not target and len(options) == 1:
                target = next(iter(options))
            if target not in options:
                raise DomainError("RETURN_TARGET_INVALID", "请选择当前模板允许的退回目标", 409)
            payload["return_target_node_key"] = target
        elif payload.get("return_target_node_key"):
            raise DomainError("RETURN_TARGET_INVALID", "只有退回决定可以指定退回目标", 400)
        else:
            payload.pop("return_target_node_key", None)
    elif action == "approval.seat.transfer":
        instance = db.get(ApprovalInstance, resource_id)
        if not instance: raise DomainError("NOT_FOUND", "审批不存在", 404)
        detail = approval_detail(db, user, instance)
        if (not detail["transfer_allowed"] or payload["target_user_id"] not in
                {item["id"] for item in detail["transfer_options"]}):
            raise DomainError("TRANSFER_TARGET_INVALID", "当前审批不能转交给该人员", 409)
    elif action == "approval.seat.add_sign":
        instance = db.get(ApprovalInstance, resource_id)
        if not instance: raise DomainError("NOT_FOUND", "审批不存在", 404)
        detail = approval_detail(db, user, instance)
        if (not detail["add_sign_allowed"] or payload["timing"] not in detail["add_sign_timings"]
                or payload["target_user_id"] not in {item["id"] for item in detail["add_sign_options"]}):
            raise DomainError("ADD_SIGN_TARGET_INVALID", "当前审批不能按所选方式加签给该人员", 409)
    elif action == "purchase.submit":
        req = db.get(PurchaseRequest, resource_id)
        if not req: raise DomainError("NOT_FOUND", "申请不存在", 404)
        request_access(db, user, req, "purchase.submit")
        from domain_packs.mold.erp.core.workflow_selection import require_template
        require_template(db, user, req, payload['definition_id'], payload.get('material_review_id'))
    elif action == 'business.submit':
        from domain_packs.mold.erp.core.domains import authorize
        subject=db.get(BusinessSubject,resource_id)
        if not subject:raise DomainError('NOT_FOUND','单据不存在',404)
        authorize(db,user,subject,'submit')
        from domain_packs.mold.erp.core.workflow_selection import require_template
        require_template(db, user, subject, payload['definition_id'], payload.get('material_review_id'))
    elif (handler := handler_for_action(action)) is not None:
        handler.implementation().validate_intent(db,user,payload)
    elif action.startswith('domain.'):
        from domain_packs.mold.erp.core.domain_commands import validate_command
        validate_command(db,user,action[7:],resource_id,payload)
    else: raise DomainError("ACTION_UNKNOWN", "未登记的人工动作")
    challenge = secrets.token_urlsafe(32)
    intent = HumanIntent(user_id=user.id, action=action, resource_id=resource_id, payload=payload,
                         payload_hash=bpm.content_hash(payload), challenge_hash=digest(challenge), expires_at=now()+timedelta(minutes=5))
    db.add(intent); db.flush()
    return {"id": intent.id, "challenge": challenge, "payload": payload, "expires_at": intent.expires_at.isoformat()}


def confirm_intent(db, user, intent_id, challenge, agent_permission_mode="ask"):
    intent = db.scalar(select(HumanIntent).where(HumanIntent.id == intent_id, HumanIntent.user_id == user.id).with_for_update())
    if not intent or not secrets.compare_digest(intent.challenge_hash, digest(challenge)):
        raise DomainError("CONFIRMATION_INVALID", "确认凭证无效", 403)
    if intent.receipt is not None: return intent.receipt
    if aware(intent.expires_at) <= now(): raise DomainError("CONFIRMATION_EXPIRED", "请重新核对并确认", 409)
    if intent.payload_hash != bpm.content_hash(intent.payload): raise DomainError("CONFIRMATION_INVALID", "确认内容不一致", 409)
    if intent.action == "approval.decide": result = decide(db, user, intent.payload, agent_permission_mode=agent_permission_mode)
    elif intent.action == "approval.seat.transfer": result = transfer_approval_seat(db, user, intent.payload)
    elif intent.action == "approval.seat.add_sign": result = add_sign_approval_seat(db, user, intent.payload)
    elif intent.action=='purchase.submit': result = submit_request(db, user, intent.resource_id, **intent.payload, agent_permission_mode=agent_permission_mode)
    elif intent.action=='business.submit': result=submit_subject(db,user,intent.resource_id,**intent.payload,agent_permission_mode=agent_permission_mode)
    elif (handler := handler_for_action(intent.action)) is not None:
        result=handler.implementation().confirm(db,user,intent.payload)
    elif intent.action.startswith('domain.'):
        from domain_packs.mold.erp.core.domain_commands import execute_command
        result=execute_command(db,user,intent.action[7:],intent.resource_id,intent.payload)
    else:raise DomainError('ACTION_UNKNOWN','未登记的人工动作')
    intent.receipt = result
    record(db, user, "human.confirmed", intent.id, {"action": intent.action, "payload_hash": intent.payload_hash})
    return result


def load_subject(db,instance,lock=False):
    models = {"purchase_request": PurchaseRequest, "business_subject": BusinessSubject}
    model = models.get(instance.resource_type)
    if model is None or instance.resource_type not in resource_contract().APPROVAL_RESOURCE_TYPES:
        raise DomainError("RESOURCE_TYPE_UNKNOWN", "审批资源类型未在当前业务包登记")
    q=select(model).where(model.id==instance.resource_id)
    return db.scalar(q.with_for_update() if lock else q)


def submit_subject(db,user,subject_id,revision,definition_id,material_review_id=None,agent_permission_mode="ask"):
    from domain_packs.mold import domains
    subject=db.scalar(select(BusinessSubject).where(BusinessSubject.id==subject_id).with_for_update())
    if not subject:raise DomainError('NOT_FOUND','业务单据不存在',404)
    domains.authorize(db,user,subject,'submit')
    if subject.revision!=revision:raise DomainError('VERSION_CONFLICT','业务资料已变化',409)
    if subject.status not in {'DRAFT','REJECTED','RETURNED'}:raise DomainError('INVALID_STATE','当前状态不能提交',409)
    from domain_packs.mold.erp.core.workflow_selection import require_template
    definition = require_template(db, user, subject, definition_id, material_review_id)
    domains.before_submit(db,user,subject)
    if subject.status!='DRAFT':subject.revision+=1
    subject.status='SUBMITTED';subject.round_no+=1
    detail=domains.typed_detail(db,subject)
    snapshot={**domains.values(subject),'detail':detail,'lines':[],
              'amount':detail.get('amount'),'currency':detail.get('currency'),
              'submitted_at':now().isoformat(),'submitter':{'id':user.id,'username':user.username,'name':user.display_name,'department':user.department}}
    material = bind_material_snapshot(db, user, 'business_subject', subject.id, subject.revision, definition, material_review_id)
    if material:
        snapshot['material_data']=material.pop('material_data')
        snapshot['material_binding']=material
    instance=ApprovalInstance(resource_type="business_subject", resource_id=subject.id,
                              definition_id=definition.id,revision=subject.revision,
                              round_no=subject.round_no,snapshot=snapshot,snapshot_hash=bpm.content_hash(snapshot),
                              engine_state=bpm.start_engine(definition.bpmn_xml))
    db.add(instance);db.flush();enter_stage(db,instance,definition,subject)
    auto_approved=process_agent_auto_approvals(db,instance.id,agent_permission_mode)
    record(db,user,'business.submitted',subject.id,{'instance_id':instance.id})
    return {'subject_id':subject.id,'instance_id':instance.id,'status':subject.status,'agent_auto_approved':auto_approved}
