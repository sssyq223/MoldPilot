from contextlib import asynccontextmanager
from datetime import timedelta, datetime
import json
import re
import secrets
from fastapi import FastAPI, Depends, Request, Response, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, func, text, delete, literal
from sqlalchemy.exc import IntegrityError
from .db import get_db, SessionLocal, now, aware
from .config import settings, model_settings, public_model_config, save_model_config
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
from .erp_design_upload import router as erp_design_upload_router
app.include_router(erp_design_upload_router)

_conversation_flags_checked = False
_user_profiles_checked = False


def compact_conversation_title(prompt: str) -> str:
    text = re.sub(r"\s+", " ", (prompt or "").strip())
    code = next((m.group(0) for m in re.finditer(r"\b[A-Z][A-Z0-9]+-[A-Z0-9-]+\b", text)), "")
    topic_rules = [
        (("权限", "审计", "通知", "附件", "来源治理"), "权限治理核对"),
        (("财务", "收付款", "回款", "付款", "开票", "发票"), "财务节点核对"),
        (("客户验收", "出厂", "出库", "物流", "签收", "交付"), "交付物流核对"),
        (("中标", "客户分类", "承接", "拒单"), "中标接收核对"),
        (("报价", "成本", "工艺", "采购价格"), "报价评估核对"),
        (("合同", "销售合同", "整套委外合同"), "合同上下文核对"),
        (("正式开工", "开工"), "开工条件核对"),
        (("项目计划", "节点", "逾期"), "项目计划核对"),
        (("设计", "BOM", "路线", "图纸"), "设计BOM核对"),
        (("制造", "质检", "检验", "报工"), "制造质检核对"),
        (("装配", "试模"), "装配试模核对"),
        (("采购订单", "采购价格", "采购"), "采购上下文核对"),
        (("项目业务档案", "业务档案"), "项目档案核对"),
        (("暂停", "恢复"), "暂停恢复核对"),
        (("终止", "关闭", "结项"), "项目关闭核对"),
    ]
    topic = next((name for keys, name in topic_rules if any(key in text for key in keys)), "")
    if code and topic:
        return f"{code} {topic}"[:80]
    if code:
        return f"{code} 查询"[:80]
    cleaned = re.sub(r"^(请|帮我|查询|核对|查看|分析)\s*", "", text)
    cleaned = re.sub(r"(请调用|调用).*$", "", cleaned).strip(" ，。；;")
    return (cleaned[:28] + "…") if len(cleaned) > 28 else (cleaned or "新对话")


def ensure_conversation_flags(db):
    """Local dev fixtures may predate pinned/archived conversation columns."""
    global _conversation_flags_checked
    if _conversation_flags_checked:
        return
    dialect = db.bind.dialect.name
    if dialect == "sqlite":
        columns = {row[1] for row in db.execute(text("PRAGMA table_info(ai_conversation)"))}
        if "pinned" not in columns:
            db.execute(text("ALTER TABLE ai_conversation ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT 0"))
        if "archived" not in columns:
            db.execute(text("ALTER TABLE ai_conversation ADD COLUMN archived BOOLEAN NOT NULL DEFAULT 0"))
        db.commit()
    elif dialect == "postgresql":
        db.execute(text("ALTER TABLE ai_conversation ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT false"))
        db.execute(text("ALTER TABLE ai_conversation ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT false"))
        db.commit()
    _conversation_flags_checked = True


def ensure_user_profiles(db):
    """Local dev fixtures may predate the optional user profile table."""
    global _user_profiles_checked
    if _user_profiles_checked:
        return
    dialect = db.bind.dialect.name
    if dialect == "sqlite":
        db.execute(text("""CREATE TABLE IF NOT EXISTS app_user_profile (
            user_id VARCHAR(36) PRIMARY KEY,
            avatar_url TEXT NOT NULL DEFAULT '',
            updated_at DATETIME
        )"""))
    elif dialect == "postgresql":
        db.execute(text("""CREATE TABLE IF NOT EXISTS app_user_profile (
            user_id VARCHAR(36) PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
            avatar_url TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ
        )"""))
    db.commit()
    _user_profiles_checked = True


def avatar_url_for(db, user_id: str) -> str:
    ensure_user_profiles(db)
    return db.execute(text("SELECT avatar_url FROM app_user_profile WHERE user_id = :user_id"), {"user_id": user_id}).scalar() or ""


def public_user_with_profile(db, user):
    return {**public_user(user), "avatar_url": avatar_url_for(db, user.id)}


def run_trace(run, steps):
    """Project the model checkpoint into a visible ReAct-style transcript.

    The chain is derived from persisted model messages and tool observations;
    it intentionally does not invent hidden reasoning.
    """
    step_by_id = {step.id: step for step in steps}
    messages = run.checkpoint.get("messages", []) if isinstance(run.checkpoint, dict) else []
    tool_result_call_ids = {msg.get("tool_call_id") for msg in messages if msg.get("role") == "tool" and msg.get("tool_call_id")}
    trace = []
    for msg in messages:
        role = msg.get("role")
        if role == "assistant":
            text = (msg.get("content") or "").strip()
            if text:
                trace.append({"type": "message", "text": text})
            for call in msg.get("tool_calls") or []:
                call_id = call.get("id")
                if call_id in tool_result_call_ids:
                    continue
                name = (call.get("function") or {}).get("name") or "业务工具"
                trace.append({"type": "tool_pending", "tool": name, "call_id": call_id})
        elif role == "tool":
            try:
                payload = json.loads(msg.get("content") or "{}")
            except ValueError:
                payload = {}
            step = step_by_id.get(payload.get("evidence_id"))
            if step:
                trace.append({"type": "tool", "id": step.id, "tool": step.tool, **step.result})
            else:
                trace.append({"type": "tool", "tool": "业务工具", "data": []})
    result = run.result if isinstance(run.result, dict) else {}
    if result:
        trace.append({"type": "final", "summary": result.get("summary"), "message": result.get("message"),
                      "suggestions": result.get("suggestions", []), "error_code": result.get("error_code")})
    return trace


def run_duration_seconds(run, steps):
    if run.status in {"QUEUED", "RUNNING"}:
        return max(0, int((now() - aware(run.created_at)).total_seconds()))
    checkpoint = run.checkpoint if isinstance(run.checkpoint, dict) else {}
    completed_at = checkpoint.get("completed_at")
    end = None
    if isinstance(completed_at, str):
        try:
            end = aware(datetime.fromisoformat(completed_at.replace("Z", "+00:00")))
        except ValueError:
            end = None
    if end is None and steps:
        end = max(aware(step.created_at) for step in steps)
    if end is None:
        model_elapsed_ms = checkpoint.get("model_elapsed_ms", 0)
        if isinstance(model_elapsed_ms, (int, float)) and model_elapsed_ms > 0:
            return max(1, int(round(model_elapsed_ms / 1000)))
        end = aware(run.created_at)
    return max(0, int((end - aware(run.created_at)).total_seconds()))


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
    model_config = model_settings()
    return {"user": {**public_user_with_profile(db, user), "authorization_hash": auth.fingerprint(db, user)},
            "permissions": permissions, "llm_configured": model_config.llm_enabled,
            "model": model_config.active_model if model_config.llm_enabled else None,
            "model_limits": {"context_window": model_config.llm_context_window,
                             "max_output_tokens": model_config.llm_max_output_tokens}}


@app.put("/api/me/avatar")
def update_my_avatar(data: s.AvatarInput, user=Depends(current_user), db=Depends(get_db)):
    avatar = data.avatar_url.strip()
    if avatar and not re.match(r"^data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$", avatar):
        raise DomainError("AVATAR_INVALID", "头像必须是 PNG、JPG 或 WebP 图片", 400)
    ensure_user_profiles(db)
    db.execute(text("DELETE FROM app_user_profile WHERE user_id = :user_id"), {"user_id": user.id})
    if avatar:
        db.execute(text("INSERT INTO app_user_profile (user_id, avatar_url, updated_at) VALUES (:user_id, :avatar_url, :updated_at)"),
                   {"user_id": user.id, "avatar_url": avatar, "updated_at": now()})
    record(db, user, "user.avatar.updated", user.id, {"has_avatar": bool(avatar)})
    db.commit()
    return public_user_with_profile(db, user)


@app.get("/api/model-config")
def model_config(user=Depends(current_user)):
    if not user.super_admin:
        raise DomainError("FORBIDDEN", "需要超级管理员", 403)
    return public_model_config()


@app.put("/api/model-config")
def update_model_config(data: s.ModelConfigInput, user=Depends(current_user)):
    if not user.super_admin:
        raise DomainError("FORBIDDEN", "需要超级管理员", 403)
    try:
        return save_model_config(data.model_dump())
    except ValueError as exc:
        raise DomainError("MODEL_CONFIG_INVALID", str(exc), 400)


@app.get("/api/catalog")
def catalog(user=Depends(current_user)):
    return {"permissions": auth.PERMISSIONS, "categories": [{"id": "hardware", "name": "五金"}, {"id": "raw_material", "name": "原材"}, {"id": "outsource", "name": "委外"}]}


@app.get("/api/users")
def users(user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "user.manage")
    return [public_user_with_profile(db, u) for u in db.scalars(select(m.User).order_by(m.User.created_at))]


@app.post("/api/users")
def create_user(data: s.UserInput, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "user.manage")
    new = m.User(username=normalize_username(data.username), display_name=data.display_name, department=data.department, password_hash=hasher.hash(data.password))
    db.add(new); db.flush(); record(db, user, "user.created", new.id); db.commit()
    return public_user_with_profile(db, new)


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


def delegation_data(row: m.AgentApprovalDelegation):
    return {"id": row.id, "process_key": row.process_key, "node_key": row.node_key, "decision": row.decision,
            "active": row.active and row.revoked_at is None, "reason": row.reason,
            "valid_from": row.valid_from.isoformat() if row.valid_from else None,
            "valid_to": row.valid_to.isoformat() if row.valid_to else None,
            "created_at": row.created_at.isoformat(), "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
            "revoke_reason": row.revoke_reason}


def agent_approval_node_options(db):
    rows = db.scalars(select(m.WorkflowDefinition).where(
        m.WorkflowDefinition.status == "PUBLISHED").order_by(m.WorkflowDefinition.process_key, m.WorkflowDefinition.version.desc()))
    options, seen = [], set()
    for definition in rows:
        for node in definition.config.get("nodes", []):
            if not node.get("agent_auto_approval"):
                continue
            key = (definition.process_key, node["key"])
            if key in seen:
                continue
            seen.add(key)
            options.append({"process_key": definition.process_key, "process_name": definition.name,
                            "definition_id": definition.id, "version": definition.version,
                            "business_type": definition.config.get("business_type"),
                            "node_key": node["key"], "node_name": node.get("name", node["key"]),
                            "has_auto_policy": bool(node.get("agent_auto_policy"))})
    return options


def require_agent_approval_node(db, process_key, node_key):
    if not any(option["process_key"] == process_key and option["node_key"] == node_key for option in agent_approval_node_options(db)):
        raise DomainError("AGENT_APPROVAL_NODE_DISABLED", "该流程节点未发布或未允许 Agent 自动审批，不能授权", 400)


@app.get("/api/agent-approval-delegations/options")
def approval_delegation_options(user=Depends(current_user), db=Depends(get_db)):
    return agent_approval_node_options(db)


@app.get("/api/agent-approval-delegations")
def approval_delegations(user=Depends(current_user), db=Depends(get_db)):
    rows = db.scalars(select(m.AgentApprovalDelegation).where(
        m.AgentApprovalDelegation.user_id == user.id).order_by(m.AgentApprovalDelegation.created_at.desc(), m.AgentApprovalDelegation.id))
    return [delegation_data(row) for row in rows]


@app.post("/api/agent-approval-delegations")
def create_approval_delegation(data: s.AgentApprovalDelegationInput, user=Depends(current_user), db=Depends(get_db)):
    if data.valid_from and data.valid_to and data.valid_to <= data.valid_from:
        raise DomainError("DATE_INVALID", "自动审批委托结束时间必须晚于开始时间", 400)
    if data.valid_to and data.valid_to <= now():
        raise DomainError("DATE_INVALID", "自动审批委托结束时间必须晚于当前时间", 400)
    require_agent_approval_node(db, data.process_key, data.node_key)
    row = db.scalar(select(m.AgentApprovalDelegation).where(
        m.AgentApprovalDelegation.user_id == user.id,
        m.AgentApprovalDelegation.process_key == data.process_key,
        m.AgentApprovalDelegation.node_key == data.node_key,
        m.AgentApprovalDelegation.decision == data.decision).with_for_update())
    if not row:
        row = m.AgentApprovalDelegation(user_id=user.id, process_key=data.process_key, node_key=data.node_key,
                                        decision=data.decision, reason=data.reason, valid_from=data.valid_from,
                                        valid_to=data.valid_to, created_by=user.id)
        db.add(row)
    else:
        row.active = True; row.reason = data.reason; row.valid_from = data.valid_from; row.valid_to = data.valid_to
        row.revoked_at = None; row.revoked_by = None; row.revoke_reason = None
    user.security_version += 1
    db.flush()
    record(db, user, "agent.approval_delegation.enabled", row.id,
           {"process_key": row.process_key, "node_key": row.node_key, "decision": row.decision})
    db.commit()
    return delegation_data(row)


@app.post("/api/agent-approval-delegations/{delegation_id}/revoke")
def revoke_approval_delegation(delegation_id: str, data: s.AgentApprovalDelegationRevokeInput, user=Depends(current_user), db=Depends(get_db)):
    row = db.scalar(select(m.AgentApprovalDelegation).where(
        m.AgentApprovalDelegation.id == delegation_id, m.AgentApprovalDelegation.user_id == user.id).with_for_update())
    if not row: raise DomainError("NOT_FOUND", "自动审批委托不存在", 404)
    if row.revoked_at is None:
        row.active = False; row.revoked_at = now(); row.revoked_by = user.id; row.revoke_reason = data.reason
        user.security_version += 1
        record(db, user, "agent.approval_delegation.revoked", row.id,
               {"process_key": row.process_key, "node_key": row.node_key, "reason": data.reason})
    db.commit()
    return delegation_data(row)


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
def conversations(archived: bool = Query(False), user=Depends(current_user), db=Depends(get_db)):
    ensure_conversation_flags(db)
    rows = db.scalars(select(m.Conversation).where(m.Conversation.user_id == user.id, m.Conversation.archived == archived)
                      .order_by(m.Conversation.pinned.desc(), m.Conversation.created_at.desc()).limit(100))
    return [{"id": c.id, "title": c.title, "pinned": c.pinned, "archived": c.archived, "created_at": c.created_at.isoformat()} for c in rows]


@app.post("/api/conversations/{conversation_id}/pin")
def pin_conversation(conversation_id: str, user=Depends(current_user), db=Depends(get_db)):
    ensure_conversation_flags(db)
    conversation = db.scalar(select(m.Conversation).where(m.Conversation.id == conversation_id, m.Conversation.user_id == user.id))
    if not conversation: raise DomainError("NOT_FOUND", "会话不存在", 404)
    conversation.pinned = not conversation.pinned
    conversation.archived = False
    record(db, user, "agent.conversation.pinned" if conversation.pinned else "agent.conversation.unpinned", conversation.id)
    db.commit()
    return {"id": conversation.id, "title": conversation.title, "pinned": conversation.pinned, "archived": conversation.archived}


@app.post("/api/conversations/{conversation_id}/archive")
def archive_conversation(conversation_id: str, user=Depends(current_user), db=Depends(get_db)):
    ensure_conversation_flags(db)
    conversation = db.scalar(select(m.Conversation).where(m.Conversation.id == conversation_id, m.Conversation.user_id == user.id))
    if not conversation: raise DomainError("NOT_FOUND", "会话不存在", 404)
    conversation.archived = True
    conversation.pinned = False
    record(db, user, "agent.conversation.archived", conversation.id)
    db.commit()
    return {"ok": True}


@app.post("/api/conversations/{conversation_id}/unarchive")
def unarchive_conversation(conversation_id: str, user=Depends(current_user), db=Depends(get_db)):
    ensure_conversation_flags(db)
    conversation = db.scalar(select(m.Conversation).where(m.Conversation.id == conversation_id, m.Conversation.user_id == user.id))
    if not conversation: raise DomainError("NOT_FOUND", "会话不存在", 404)
    conversation.archived = False
    record(db, user, "agent.conversation.unarchived", conversation.id)
    db.commit()
    return {"id": conversation.id, "title": conversation.title, "pinned": conversation.pinned, "archived": conversation.archived, "created_at": conversation.created_at.isoformat()}


@app.post("/api/runs")
def create_run(data: s.RunInput, user=Depends(current_user), db=Depends(get_db)):
    ensure_conversation_flags(db)
    if data.conversation_id:
        conversation = db.scalar(select(m.Conversation).where(m.Conversation.id == data.conversation_id, m.Conversation.user_id == user.id, m.Conversation.archived == False))
        if not conversation: raise DomainError("NOT_FOUND", "会话不存在", 404)
    else:
        conversation = m.Conversation(user_id=user.id, title=compact_conversation_title(data.prompt)); db.add(conversation); db.flush()
    model_config = model_settings()
    permission_mode = data.agent_permission_mode
    run = m.Run(user_id=user.id, conversation_id=conversation.id, security_version=user.security_version, prompt=data.prompt,
                status="QUEUED" if model_config.llm_enabled else "WAITING_CONFIGURATION",
                checkpoint={"agent_permission_mode": permission_mode})
    db.add(run); db.flush()
    from .files import bind_run_files
    bind_run_files(db,user,run,data.file_ids)
    record(db, user, "agent.run.created", run.id, {"agent_permission_mode": permission_mode}); db.commit()
    return {"id": run.id, "conversation_id": conversation.id, "status": run.status, "agent_permission_mode": permission_mode}


@app.get("/api/conversations/{conversation_id}/runs")
def runs(conversation_id: str, user=Depends(current_user), db=Depends(get_db)):
    from .files import run_files
    current_hash = auth.fingerprint(db, user)
    result = []
    for r in db.scalars(select(m.Run).where(m.Run.conversation_id == conversation_id, m.Run.user_id == user.id).order_by(m.Run.created_at)):
        checkpoint = r.checkpoint if isinstance(r.checkpoint, dict) else {}
        authorization_hash = checkpoint.get("authorization_hash")
        visible = r.security_version == user.security_version and (not authorization_hash or authorization_hash == current_hash)
        steps = list(db.scalars(select(m.Step).where(m.Step.run_id == r.id).order_by(m.Step.sequence))) if visible else []
        result.append({"id": r.id, "prompt": r.prompt, "status": r.status,
                       "created_at": r.created_at,
                       "agent_permission_mode": checkpoint.get("agent_permission_mode", "ask"),
                       "duration_seconds": run_duration_seconds(r, steps) if visible else 0,
                       "files": run_files(db,user,r) if visible else [],
                       "result": r.result if visible else {"message": "权限已变化，请重新发起查询"},
                       "trace": run_trace(r, steps) if visible else [],
                       "context_usage": checkpoint.get("context_usage") if visible else None,
                       "progress": {"turn": checkpoint.get("turn", 0),
                                    "phase": checkpoint.get('phase'),
                                    "model_elapsed_ms": checkpoint.get('model_elapsed_ms', 0),
                                    "context_usage": checkpoint.get("context_usage"),
                                    "elapsed_seconds": max(0, int((now()-aware(r.created_at)).total_seconds())) if r.status in {'QUEUED','RUNNING'} else None,
                                    "tools": [{"id": step.id, "name": step.tool} for step in steps]} if visible else None})
    return result


@app.post("/api/runs/{run_id}/cancel")
def cancel(run_id: str, user=Depends(current_user), db=Depends(get_db)):
    run = db.scalar(select(m.Run).where(m.Run.id == run_id, m.Run.user_id == user.id).with_for_update())
    if not run: raise DomainError("NOT_FOUND", "任务不存在", 404)
    if run.status not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
        run.status = "CANCELLED"; run.lease_epoch += 1
        run.checkpoint = {**(run.checkpoint or {}), "completed_at": now().isoformat()}
    db.commit(); return {"status": run.status}


@app.get("/api/capabilities")
def capabilities(user=Depends(current_user), db=Depends(get_db)):
    from .tool_gateway import TOOLS, SKILLS, available_tools, capability_descriptor, skill_context
    return {
        "tools": [capability_descriptor("TOOL", k, TOOLS[k]) for k in available_tools(db, user)],
        "skills": [{**capability_descriptor("SKILL", s["key"], SKILLS[s["key"]]), "version": s["version"]} for s in skill_context(db, user)],
    }


@app.get("/api/users/{user_id}/capabilities")
def user_capabilities(user_id: str, user=Depends(current_user), db=Depends(get_db)):
    from .tool_gateway import TOOLS, SKILLS, assigned, available_tools, capability_descriptor, skill_context
    if not user.super_admin: raise DomainError("FORBIDDEN", "需要超级管理员", 403)
    target = db.get(m.User, user_id)
    if not target: raise DomainError("NOT_FOUND", "用户不存在", 404)
    effective_tools = set(available_tools(db, target))
    effective_skills = {item["key"] for item in skill_context(db, target)}
    return [{**capability_descriptor(kind, key, spec),
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
from .plan_tools import router as project_plan_proposal_router
app.include_router(project_plan_proposal_router)
