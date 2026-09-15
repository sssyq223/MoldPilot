from contextlib import asynccontextmanager
from datetime import timedelta
import secrets
from fastapi import FastAPI, Depends, Request, Response, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, func, text, delete, literal
from sqlalchemy.exc import IntegrityError
from .db import get_db, SessionLocal, now
from .config import settings
from . import models as m, schemas as s, authorization as auth, business, bpm
from .security import current_user, login, public_user, hasher, normalize_username, digest
from .errors import DomainError
from .events import record

app = FastAPI(title="模具工作台 · 独立 Agent", version="0.1.0")
from .organization_api import router as organization_router
app.include_router(organization_router)
from .workflow_categories import router as category_router, require_category
app.include_router(category_router)
from .material_templates import router as material_template_router,bind_contract
app.include_router(material_template_router)
from .contacts import router as contact_router
app.include_router(contact_router)
from .files import router as file_router
app.include_router(file_router)


@app.exception_handler(DomainError)
async def domain_error(request, error):
    return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=error.status)


@app.exception_handler(IntegrityError)
async def integrity_error(request, error):
    return JSONResponse({"error": {"code": "CONFLICT", "message": "记录重复或关联数据已变化"}}, status_code=409)


@app.middleware("http")
async def headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.get("/api/health")
def health(db=Depends(get_db)):
    zone = db.scalar(text("SHOW TIME ZONE")) if db.bind.dialect.name=='postgresql' else 'Asia/Shanghai'
    return {"status": "ok" if zone == "Asia/Shanghai" else "degraded", "timezone": zone, "version": "0.1.0"}


@app.post("/api/auth/login")
def sign_in(data: s.LoginInput, request: Request, response: Response, db=Depends(get_db)):
    if request.headers.get("origin") not in {None, settings().origin}:
        raise DomainError("ORIGIN_DENIED", "请求来源不受信任", 403)
    user, token, csrf = login(db, data.username, data.password)
    record(db, user, "auth.login", user.id)
    db.commit()
    response.set_cookie("mold_session", token, httponly=True, secure=settings().cookie_secure, samesite="strict", max_age=28800)
    response.set_cookie("mold_csrf", csrf, httponly=False, secure=settings().cookie_secure, samesite="strict", max_age=28800)
    return {"user": public_user(user), "csrf": csrf}


@app.post("/api/auth/logout")
def sign_out(request: Request, response: Response, user=Depends(current_user), db=Depends(get_db)):
    db.delete(request.state.session); db.commit()
    response.delete_cookie("mold_session"); response.delete_cookie("mold_csrf")
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user), db=Depends(get_db)):
    permissions = list(auth.PERMISSIONS) if user.super_admin else [p for p in auth.PERMISSIONS if any(g.effect == "ALLOW" for g in auth.grants_for(db, user, p))]
    return {"user": {**public_user(user), "authorization_hash": auth.fingerprint(db, user)},
            "permissions": permissions, "llm_configured": settings().llm_enabled,
            "model": settings().active_model if settings().llm_enabled else None}


@app.get("/api/catalog")
def catalog(user=Depends(current_user)):
    return {"permissions": auth.PERMISSIONS, "categories": [{"id": "hardware", "name": "五金"}, {"id": "raw_material", "name": "原材"}, {"id": "outsource", "name": "委外"}]}


@app.get("/api/users")
def users(user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "user.manage")
    return [public_user(u) for u in db.scalars(select(m.User).order_by(m.User.created_at))]


@app.post("/api/users")
def create_user(data: s.UserInput, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "user.manage")
    new = m.User(username=normalize_username(data.username), display_name=data.display_name, department=data.department, password_hash=hasher.hash(data.password))
    db.add(new); db.flush(); record(db, user, "user.created", new.id); db.commit()
    return public_user(new)


@app.get("/api/users/{user_id}/grants")
def grants(user_id: str, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "grant.manage")
    return [{"id": g.id, "permission": g.permission, "effect": g.effect, "scope": g.scope, "fields": g.fields,
             "active": g.active, "reason": g.reason} for g in db.scalars(select(m.Grant).where(m.Grant.user_id == user_id))]


@app.post("/api/users/{user_id}/grants")
def grant_user(user_id: str, data: s.GrantInput, user=Depends(current_user), db=Depends(get_db)):
    # Delegated grant-administrator ceilings will be a separate, tested capability.
    if not user.super_admin: raise DomainError("FORBIDDEN", "当前授权配置由超级管理员办理", 403)
    target = db.scalar(select(m.User).where(m.User.id == user_id).with_for_update())
    if not target: raise DomainError("NOT_FOUND", "用户不存在", 404)
    if target.security_version != data.expected_security_version: raise DomainError("VERSION_CONFLICT", "用户权限已变化，请刷新", 409)
    auth.valid_scope(data.scope)
    if data.permission not in auth.PERMISSIONS or not data.fields or not set(data.fields) <= set(auth.PERMISSIONS[data.permission]):
        raise DomainError("INVALID_PERMISSION", "权限或字段不在已实现目录中")
    if data.valid_from and data.valid_to and data.valid_to <= data.valid_from:
        raise DomainError("INVALID_PERIOD", "授权结束时间必须晚于开始时间")
    grant = m.Grant(user_id=user_id, permission=data.permission, effect=data.effect, scope=data.scope,
                    fields=data.fields, reason=data.reason, valid_from=data.valid_from, valid_to=data.valid_to, granted_by=user.id)
    db.add(grant); target.security_version += 1
    record(db, user, "permission.changed", user_id, {"permission": data.permission, "reason": data.reason}, [user_id]); db.commit()
    return {"security_version": target.security_version}


@app.delete("/api/users/{user_id}/grants/{grant_id}")
def revoke(user_id: str, grant_id: str, expected_security_version: int, user=Depends(current_user), db=Depends(get_db)):
    if not user.super_admin: raise DomainError("FORBIDDEN", "需要超级管理员", 403)
    target = db.scalar(select(m.User).where(m.User.id == user_id).with_for_update())
    grant = db.get(m.Grant, grant_id)
    if not target or not grant or grant.user_id != user_id: raise DomainError("NOT_FOUND", "授权不存在", 404)
    if target.security_version != expected_security_version: raise DomainError("VERSION_CONFLICT", "权限已变化", 409)
    grant.active = False; target.security_version += 1
    record(db, user, "permission.revoked", user_id, {"grant_id": grant_id}, [user_id]); db.commit()
    return {"security_version": target.security_version}


@app.get("/api/projects")
def projects(user=Depends(current_user), db=Depends(get_db)):
    p = auth.predicate(db, user, "project.read", {"project_id": m.Project.id})
    return [auth.select_fields({"id": row.id, "code": row.code, "name": row.name, "status": row.status},
                              auth.require(db, user, "project.read", {"project_id": row.id}))
            for row in db.scalars(select(m.Project).where(p).limit(100))]


@app.get("/api/materials")
def materials(project_id: str, user=Depends(current_user), db=Depends(get_db)):
    p = auth.predicate(db, user, "purchase.create", {"project_id": literal(project_id), "category": m.Material.category})
    return [{"id": m.id, "code": m.code, "name": m.name, "category": m.category, "unit": m.unit}
            for m in db.scalars(select(m.Material).where(p).limit(100))]


@app.get("/api/purchases")
def purchases(user=Depends(current_user), db=Depends(get_db)):
    return [business.request_data(db, user, req) for req in db.scalars(business.visible_requests(db, user).order_by(m.PurchaseRequest.created_at.desc()).limit(100))]


@app.post("/api/purchases")
def create_purchase(data: s.PurchaseInput, user=Depends(current_user), db=Depends(get_db)):
    req = business.create_request(db, user, data); db.commit()
    return {"id": req.id, "number": req.number, "status": req.status}


@app.post("/api/purchases/{request_id}/submit-intent")
def submit_intent(request_id: str, data: s.SubmitInput, user=Depends(current_user), db=Depends(get_db)):
    result = business.create_intent(db, user, "purchase.submit", request_id, data.model_dump())
    db.commit(); return result


def definition_data(d):
    return {"id": d.id, "process_key": d.process_key, "version": d.version, "name": d.name,
            "status": d.status, "business_type": d.config['business_type'], "config": d.config, "category_id":d.category_id,
            "bpmn_xml": d.bpmn_xml, "package_hash": d.package_hash,'material_template_id':d.material_template_id,
            "edit_hash": bpm.content_hash({'name': d.name, 'config': d.config, 'category_id':d.category_id,'material_template_id':d.material_template_id}),
            "created_at": d.created_at}


@app.get("/api/workflows")
def workflows(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100), user=Depends(current_user), db=Depends(get_db)):
    # Published reusable templates expose their business type without exposing the designer configuration.
    q = select(m.WorkflowDefinition).order_by(m.WorkflowDefinition.process_key, m.WorkflowDefinition.version.desc())
    if not auth.access(db, user, "workflow.design", {}).allowed: q = q.where(m.WorkflowDefinition.status == "PUBLISHED")
    return [definition_data(d) if auth.access(db, user, "workflow.design", {}).allowed else {"id": d.id, "process_key": d.process_key, "version": d.version, "name": d.name, "status": d.status,
             "business_type": d.config['business_type'],
             "config": d.config if auth.access(db, user, "workflow.design", {}).allowed else None,
             "bpmn_xml": None} for d in db.scalars(q.offset(offset).limit(limit))]


@app.post("/api/workflows")
def create_workflow(data: s.DefinitionInput, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "workflow.design")
    data.config=bind_contract(db,data.config,data.material_template_id)
    bpm.validate(data.config)
    require_category(db,data.config,data.category_id)
    # Serialize version allocation per process key; concurrent drafts must not race max(version).
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'), {'key': 'workflow:'+data.process_key})
    version = (db.scalar(select(func.max(m.WorkflowDefinition.version)).where(m.WorkflowDefinition.process_key == data.process_key)) or 0)+1
    d = m.WorkflowDefinition(process_key=data.process_key, name=data.name, version=version, config=data.config, category_id=data.category_id,material_template_id=data.material_template_id)
    db.add(d); db.flush(); record(db, user, "workflow.draft.created", d.id); db.commit()
    return {"id": d.id, "version": d.version}


@app.post('/api/workflows/simulate')
def simulate_workflow(data: s.WorkflowSimulationInput, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, 'workflow.design')
    # Simulation uses caller-provided test data only; it never loads business records or creates tasks.
    return bpm.simulate(bind_contract(db,data.config,data.material_template_id), data.snapshot)


@app.get('/api/workflows/available')
def available_workflows(resource_type: str, resource_id: str, user=Depends(current_user), db=Depends(get_db)):
    from .workflow_selection import available, metadata
    if resource_type not in {'purchase_request', 'business_subject'}:
        raise DomainError('RESOURCE_TYPE_INVALID', '审批业务对象类型无效')
    resource = db.get(m.PurchaseRequest if resource_type == 'purchase_request' else m.BusinessSubject, resource_id)
    if not resource: raise DomainError('NOT_FOUND', '业务对象不存在或无权访问', 404)
    return [metadata(d,db) for d in available(db, user, resource)]


@app.get('/api/workflows/history/{process_key}')
def workflow_history(process_key: str, before_version: int | None = Query(None, ge=1), user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, 'workflow.design')
    q = select(m.WorkflowDefinition).where(m.WorkflowDefinition.process_key == process_key)
    if before_version is not None: q = q.where(m.WorkflowDefinition.version < before_version)
    rows = list(db.scalars(q.order_by(m.WorkflowDefinition.version.desc()).limit(51)))
    return {'items': [definition_data(d) for d in rows[:50]],
            'next_before': rows[49].version if len(rows) > 50 else None}


@app.get('/api/workflows/{definition_id}')
def workflow_detail(definition_id: str, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, 'workflow.design')
    d = db.get(m.WorkflowDefinition, definition_id)
    if not d: raise DomainError('NOT_FOUND', '模板不存在', 404)
    return {**definition_data(d), 'instance_count': db.scalar(select(func.count()).select_from(m.ApprovalInstance).where(m.ApprovalInstance.definition_id == d.id))}


@app.put('/api/workflows/{definition_id}')
def edit_workflow(definition_id: str, data: s.DefinitionEditInput, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, 'workflow.design')
    d = db.scalar(select(m.WorkflowDefinition).where(m.WorkflowDefinition.id == definition_id).with_for_update())
    if not d: raise DomainError('NOT_FOUND', '模板不存在', 404)
    if d.status != 'DRAFT': raise DomainError('PUBLISHED_IMMUTABLE', '已发布版本不能覆盖，请另存为新版本草稿', 409)
    if data.expected_hash != definition_data(d)['edit_hash']:
        raise DomainError('VERSION_CONFLICT', '草稿已被修改，请重新打开后编辑', 409)
    material_id=data.material_template_id if 'material_template_id' in data.model_fields_set else d.material_template_id
    data.config=bind_contract(db,data.config,material_id)
    bpm.validate(data.config)
    require_category(db,data.config,data.category_id)
    d.name = data.name; d.config = data.config; d.category_id=data.category_id; d.material_template_id=material_id
    record(db, user, 'workflow.draft.updated', d.id, {'previous_hash': data.expected_hash})
    db.commit()
    return definition_data(d)


@app.post("/api/workflows/{definition_id}/publish")
def publish(definition_id: str, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "workflow.publish")
    d = db.scalar(select(m.WorkflowDefinition).where(m.WorkflowDefinition.id == definition_id).with_for_update())
    if not d: raise DomainError("NOT_FOUND", "模板不存在", 404)
    if d.status == "PUBLISHED": return {"status": d.status, "hash": d.package_hash}
    bind_contract(db,d.config,d.material_template_id)
    bpm.validate(d.config)
    require_category(db,d.config,d.category_id)
    from .assignments import check_publish
    check_publish(db,d.config)
    d.bpmn_xml = bpm.compile_bpmn(d.config)
    bpm.start_engine(d.bpmn_xml)
    d.package_hash = bpm.content_hash({"config": d.config, "xml": d.bpmn_xml,'material_template_id':d.material_template_id})
    d.status = "PUBLISHED"; record(db, user, "workflow.published", d.id, {"hash": d.package_hash}); db.commit()
    return {"status": d.status, "hash": d.package_hash}


@app.get("/api/approvals")
def approvals(user=Depends(current_user), db=Depends(get_db)):
    q = select(m.ApprovalInstance).join(m.ApprovalSeat).where(m.ApprovalSeat.user_id == user.id, m.ApprovalSeat.status == "PENDING").distinct()
    results = []
    for instance in db.scalars(q.limit(100)):
        try: results.append(business.approval_detail(db, user, instance))
        except DomainError: continue
    return results


@app.get("/api/approvals/{instance_id}")
def approval(instance_id: str, user=Depends(current_user), db=Depends(get_db)):
    instance = db.get(m.ApprovalInstance, instance_id)
    if not instance: raise DomainError("NOT_FOUND", "审批不存在", 404)
    return business.approval_detail(db, user, instance)


@app.post("/api/approvals/decision-intent")
def decision_intent(data: s.DecisionInput, user=Depends(current_user), db=Depends(get_db)):
    result = business.create_intent(db, user, "approval.decide", data.instance_id, data.model_dump())
    db.commit(); return result


@app.post("/api/human-actions/{intent_id}/confirm")
def confirm(intent_id: str, data: s.ConfirmationInput, user=Depends(current_user), db=Depends(get_db)):
    result = business.confirm_intent(db, user, intent_id, data.challenge)
    db.commit(); return result


@app.get("/api/notifications")
def notifications(user=Depends(current_user), db=Depends(get_db)):
    from .message_worker import permitted
    return [{"id": n.id, "title": n.title, "kind":event.kind,"resource_id": n.resource_id, "read": n.read, "created_at": n.created_at.isoformat()}
            for n,event in db.execute(select(m.Notification,m.Outbox).join(m.Outbox).where(m.Notification.user_id == user.id).order_by(m.Notification.created_at.desc()).limit(100))
            if permitted(db,user,event)]


@app.post("/api/notifications/{notification_id}/read")
def read_notification(notification_id: str, user=Depends(current_user), db=Depends(get_db)):
    n = db.scalar(select(m.Notification).where(m.Notification.id == notification_id, m.Notification.user_id == user.id))
    if not n: raise DomainError("NOT_FOUND", "通知不存在", 404)
    n.read = True; db.commit(); return {"ok": True}


@app.get("/api/audit")
def audit(user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "audit.read")
    return [{"id": a.id, "action": a.action, "resource_id": a.resource_id, "created_at": a.created_at.isoformat(), "detail": a.detail}
            for a in db.scalars(select(m.AuditEvent).order_by(m.AuditEvent.created_at.desc()).limit(100))]


@app.get("/api/conversations")
def conversations(user=Depends(current_user), db=Depends(get_db)):
    return [{"id": c.id, "title": c.title} for c in db.scalars(select(m.Conversation).where(m.Conversation.user_id == user.id).order_by(m.Conversation.created_at.desc()).limit(100))]


@app.post("/api/runs")
def create_run(data: s.RunInput, user=Depends(current_user), db=Depends(get_db)):
    if data.conversation_id:
        conversation = db.scalar(select(m.Conversation).where(m.Conversation.id == data.conversation_id, m.Conversation.user_id == user.id))
        if not conversation: raise DomainError("NOT_FOUND", "会话不存在", 404)
    else:
        conversation = m.Conversation(user_id=user.id, title=data.prompt[:60]); db.add(conversation); db.flush()
    run = m.Run(user_id=user.id, conversation_id=conversation.id, security_version=user.security_version, prompt=data.prompt,
                status="QUEUED" if settings().llm_enabled else "WAITING_CONFIGURATION")
    db.add(run); db.flush()
    from .files import bind_run_files
    bind_run_files(db,user,run,data.file_ids)
    record(db, user, "agent.run.created", run.id); db.commit()
    return {"id": run.id, "conversation_id": conversation.id, "status": run.status}


@app.get("/api/conversations/{conversation_id}/runs")
def runs(conversation_id: str, user=Depends(current_user), db=Depends(get_db)):
    from .files import run_files
    current_hash = auth.fingerprint(db, user)
    result = []
    for r in db.scalars(select(m.Run).where(m.Run.conversation_id == conversation_id, m.Run.user_id == user.id).order_by(m.Run.created_at)):
        visible = r.security_version == user.security_version and (not r.checkpoint or r.checkpoint.get("authorization_hash") == current_hash)
        steps = list(db.scalars(select(m.Step).where(m.Step.run_id == r.id).order_by(m.Step.sequence))) if visible else []
        result.append({"id": r.id, "prompt": r.prompt, "status": r.status,
                       "files": run_files(db,user,r) if visible else [],
                       "result": r.result if visible else {"message": "权限已变化，请重新发起查询"},
                       "progress": {"turn": r.checkpoint.get("turn", 0),
                                    "phase": r.checkpoint.get('phase'),
                                    "model_elapsed_ms": r.checkpoint.get('model_elapsed_ms', 0),
                                    "elapsed_seconds": max(0, int((now()-r.created_at).total_seconds())) if r.status in {'QUEUED','RUNNING'} else None,
                                    "tools": [{"id": step.id, "name": step.tool} for step in steps]} if visible else None})
    return result


@app.post("/api/runs/{run_id}/cancel")
def cancel(run_id: str, user=Depends(current_user), db=Depends(get_db)):
    run = db.scalar(select(m.Run).where(m.Run.id == run_id, m.Run.user_id == user.id).with_for_update())
    if not run: raise DomainError("NOT_FOUND", "任务不存在", 404)
    if run.status not in {"SUCCEEDED", "FAILED", "CANCELLED"}: run.status = "CANCELLED"; run.lease_epoch += 1
    db.commit(); return {"status": run.status}


@app.get("/api/capabilities")
def capabilities(user=Depends(current_user), db=Depends(get_db)):
    from .tool_gateway import TOOLS, SKILLS, available_tools, skill_context
    return {"tools": [{"key": k, **TOOLS[k]} for k in available_tools(db, user)], "skills": [{"key": s["key"], "version": s["version"]} for s in skill_context(db, user)]}


@app.get("/api/users/{user_id}/capabilities")
def user_capabilities(user_id: str, user=Depends(current_user), db=Depends(get_db)):
    from .tool_gateway import TOOLS, SKILLS, assigned, available_tools, skill_context
    if not user.super_admin: raise DomainError("FORBIDDEN", "需要超级管理员", 403)
    target = db.get(m.User, user_id)
    if not target: raise DomainError("NOT_FOUND", "用户不存在", 404)
    effective_tools = set(available_tools(db, target))
    effective_skills = {item["key"] for item in skill_context(db, target)}
    return [{"kind": kind, "key": key, "name": spec.get("name", key),
             "description": spec.get("description", ""), "dependencies": spec.get("tools", []),
             "enabled": assigned(db, target, kind, key), "effective": key in effective}
            for kind, catalog, effective in [("TOOL", TOOLS, effective_tools), ("SKILL", SKILLS, effective_skills)]
            for key, spec in catalog.items()]


@app.post("/api/users/{user_id}/capabilities")
def set_capability(user_id: str, data: s.CapabilityInput, user=Depends(current_user), db=Depends(get_db)):
    from .tool_gateway import TOOLS, SKILLS
    if not user.super_admin: raise DomainError("FORBIDDEN", "需要超级管理员", 403)
    target = db.scalar(select(m.User).where(m.User.id == user_id).with_for_update())
    if not target: raise DomainError("NOT_FOUND", "用户不存在", 404)
    if target.super_admin: raise DomainError("ADMIN_CAPABILITIES_FIXED", "超级管理员拥有已登记的完整能力")
    if target.security_version != data.expected_security_version:
        raise DomainError("VERSION_CONFLICT", "用户权限已变化，请刷新", 409)
    catalog = TOOLS if data.kind == "TOOL" else SKILLS
    if data.key not in catalog: raise DomainError("CAPABILITY_UNKNOWN", "能力不在已登记目录中")
    assignment = db.scalar(select(m.Capability).where(m.Capability.user_id == user_id, m.Capability.kind == data.kind, m.Capability.key == data.key))
    if not assignment:
        assignment = m.Capability(user_id=user_id, kind=data.kind, key=data.key)
        db.add(assignment)
    assignment.enabled = data.enabled
    target.security_version += 1
    record(db, user, "capability.changed", user_id, {"kind": data.kind, "key": data.key, "enabled": data.enabled, "reason": data.reason}, [user_id])
    db.commit()
    return {"security_version": target.security_version}


from .internal import install
install(app)
from .domain_api import install as install_domains
install_domains(app)

from .contact_tools import router as contact_proposal_router
app.include_router(contact_proposal_router)
from .project_control_tools import router as project_control_proposal_router
app.include_router(project_control_proposal_router)
from .project_closure_tools import router as project_closure_proposal_router
app.include_router(project_closure_proposal_router)
