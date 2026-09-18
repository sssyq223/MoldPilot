from contextlib import asynccontextmanager
from datetime import timedelta, datetime
from functools import lru_cache
import json
import re
import secrets
from fastapi import FastAPI, APIRouter, Depends, Request, Response, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select, func, text, delete, literal
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from .db import get_db, SessionLocal, now, aware
from .config import (settings, model_settings, public_model_config, save_model_config,
                     create_model_profile, update_model_profile, activate_model_profile,
                     delete_model_profile)
from . import models as m, schemas as s, authorization as auth
from .security import current_user, login, public_user, hasher, normalize_username, digest
from .errors import DomainError
from .events import record
from .run_events import publish_run_update, subscribe_run_updates
from .domain_pack import manifest as load_domain_manifest
from agent_core.domain_pack import component

active_manifest = load_domain_manifest()
app = FastAPI(title=active_manifest.APP_TITLE, version="0.1.0")
domain_router = APIRouter()


@lru_cache
def _business():
    """Load the active pack's transaction service only when a route needs it."""
    return component("business")


@lru_cache
def _bpm():
    """Load the business workflow compiler only for workflow operations."""
    from . import bpm
    return bpm
from .organization_api import router as organization_router
app.include_router(organization_router)
from .workflow_categories import router as category_router, require_category
app.include_router(category_router)
from .material_templates import router as material_template_router,bind_contract
app.include_router(material_template_router)
from .files import router as file_router
app.include_router(file_router)
from .proposal_api import router as proposal_router
app.include_router(proposal_router)

_conversation_flags_checked = False


def compact_conversation_title(prompt: str) -> str:
    return active_manifest.conversation_title(prompt)


def ensure_conversation_flags(db):
    """Ensure conversation flags exist on the PostgreSQL baseline."""
    global _conversation_flags_checked
    if _conversation_flags_checked:
        return
    dialect = db.bind.dialect.name
    if dialect != "postgresql":
        raise RuntimeError("The runtime requires PostgreSQL; SQLite compatibility branches are not allowed.")
    db.execute(text("ALTER TABLE ai_conversation ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT false"))
    db.execute(text("ALTER TABLE ai_conversation ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT false"))
    db.commit()
    _conversation_flags_checked = True


def avatar_url_for(db, user_id: str) -> str:
    profile = db.get(m.UserProfile, user_id)
    return profile.avatar_url if profile else ""


def public_user_with_profile(db, user):
    return {**public_user(user), "avatar_url": avatar_url_for(db, user.id)}


def proposal_decisions(db, user_id, run, steps):
    decisions = dict((run.checkpoint or {}).get("proposal_decisions") or {})
    proposal_ids = [step.id for step in steps if isinstance(step.result, dict) and step.result.get("proposal")]
    if proposal_ids:
        confirmed = db.scalars(select(m.HumanIntent.resource_id).where(
            m.HumanIntent.user_id == user_id,
            m.HumanIntent.resource_id.in_(proposal_ids),
            m.HumanIntent.receipt.is_not(None),
        ))
        for step_id in confirmed:
            decisions.setdefault(step_id, "approved")
    return decisions


def run_trace(run, steps, decisions=None):
    """Project the model checkpoint into a visible ReAct-style transcript.

    The chain is derived from persisted model messages and tool observations;
    it intentionally does not invent hidden reasoning.
    """
    step_by_id = {step.id: step for step in steps}
    decisions = decisions or {}
    messages = run.checkpoint.get("messages", []) if isinstance(run.checkpoint, dict) else []
    prior_finals = (run.checkpoint or {}).get("prior_finals", [])

    def prior_text(prior):
        text = prior.get("summary") or prior.get("message") or ""
        suggestions = prior.get("suggestions") or []
        if suggestions:
            text += "\n" + "\n".join(f"- {item}" for item in suggestions)
        return text.strip()

    prior_texts = {
        text for prior in prior_finals if isinstance(prior, dict)
        if (text := prior_text(prior))
    }
    tool_result_call_ids = {msg.get("tool_call_id") for msg in messages if msg.get("role") == "tool" and msg.get("tool_call_id")}
    tool_names_by_call = {}
    trace = []
    assistant_turn = 0
    assistant_texts = set()
    for msg in messages:
        role = msg.get("role")
        if role == "assistant":
            message_key = f"assistant:{assistant_turn}"
            assistant_turn += 1
            text = (msg.get("content") or "").strip()
            if text:
                assistant_texts.add(text)
                trace.append({"type": "message", "text": text, "message_key": message_key,
                              **({"historical": True} if text in prior_texts else {})})
            for call in msg.get("tool_calls") or []:
                call_id = call.get("id")
                tool_names_by_call[call_id] = (call.get("function") or {}).get("name") or "业务工具"
                if call_id in tool_result_call_ids:
                    continue
                name = (call.get("function") or {}).get("name") or "业务工具"
                trace.append({"type": "tool_pending" if run.status in {"QUEUED", "RUNNING"} else "tool_interrupted",
                              "tool": name, "call_id": call_id, "run_status": run.status})
        elif role == "tool":
            try:
                payload = json.loads(msg.get("content") or "{}")
            except ValueError:
                payload = {}
            step = step_by_id.get(payload.get("evidence_id"))
            if step:
                trace.append({"type": "tool", "id": step.id, "tool": step.tool, **step.result,
                              "proposal_decision": decisions.get(step.id)})
            elif isinstance(payload.get("tool_error"), dict):
                error = payload["tool_error"]
                trace.append({"type": "tool_error",
                              "tool": tool_names_by_call.get(msg.get("tool_call_id"), "业务工具"),
                              "code": error.get("code") or "TOOL_REJECTED",
                              "message": error.get("message") or "工具未接受本次请求"})
            elif payload.get("source") == "harness" and isinstance(payload.get("activated"), list):
                trace.append({"type": "tool_search", "tool": "ToolSearch", "query": payload.get("query", ""),
                              "activated": payload.get("activated", []), "matches": payload.get("matches", []),
                              "message": payload.get("message", ""), "as_of": payload.get("as_of")})
            elif payload.get("source") == "trusted_host" and payload.get("event") == "proposal_resolved":
                trace.append({
                    "type": "proposal_resolution",
                    "decision": payload.get("decision"),
                    "proposal_step_id": payload.get("proposal_step_id"),
                    "receipt": payload.get("authoritative_receipt"),
                })
            else:
                trace.append({"type": "tool", "tool": "业务工具", "data": [], "as_of": payload.get("as_of")})
    streaming = (run.checkpoint or {}).get("streaming_model_message")
    if run.status in {"QUEUED", "RUNNING"} and isinstance(streaming, dict):
        text = (streaming.get("content") or "").strip()
        if text:
            # This snapshot becomes the next persisted assistant message.  Its
            # identity must not depend on the trace array position because tool
            # observations are inserted ahead of it while a ReAct run advances.
            trace.append({"type": "message", "text": text, "streaming": True,
                          "message_key": f"assistant:{assistant_turn}"})
        for call in streaming.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            name = function.get("name") or "业务工具"
            trace.append({"type": "tool_pending", "tool": name,
                          "call_id": call.get("id"), "run_status": run.status,
                          "streaming": True})
    for prior in prior_finals:
        if isinstance(prior, dict):
            text = prior_text(prior)
            if text.strip() and text.strip() not in assistant_texts:
                trace.append({
                    "type": "message",
                    "text": text.strip(),
                    "historical": True,
                    "message_key": f"assistant:{assistant_turn}",
                })
                assistant_turn += 1
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


def conversation_runs_payload(db, user, conversation_id: str):
    """Build the only client-visible run projection for HTTP and live events."""
    from .files import run_files
    current_hash = auth.fingerprint(db, user)
    result = []
    for r in db.scalars(select(m.Run).where(
            m.Run.conversation_id == conversation_id,
            m.Run.user_id == user.id,
    ).order_by(m.Run.created_at)):
        checkpoint = r.checkpoint if isinstance(r.checkpoint, dict) else {}
        authorization_hash = checkpoint.get("authorization_hash")
        visible = r.security_version == user.security_version and (
            not authorization_hash or authorization_hash == current_hash
        )
        steps = list(db.scalars(select(m.Step).where(
            m.Step.run_id == r.id
        ).order_by(m.Step.sequence))) if visible else []
        decisions = proposal_decisions(db, user.id, r, steps) if visible else {}
        result.append({
            "id": r.id,
            "prompt": r.prompt,
            "status": r.status,
            "created_at": r.created_at,
            "agent_permission_mode": checkpoint.get("agent_permission_mode", "ask"),
            "duration_seconds": run_duration_seconds(r, steps) if visible else 0,
            "files": run_files(db, user, r) if visible else [],
            "result": r.result if visible else {"message": "权限已变化，请重新发起查询"},
            "trace": run_trace(r, steps, decisions) if visible else [],
            "context_usage": checkpoint.get("context_usage") if visible else None,
            "progress": {
                "turn": checkpoint.get("turn", 0),
                "phase": checkpoint.get("phase"),
                "model_elapsed_ms": checkpoint.get("model_elapsed_ms", 0),
                "context_usage": checkpoint.get("context_usage"),
                "elapsed_seconds": max(0, int((now() - aware(r.created_at)).total_seconds()))
                    if r.status in {"QUEUED", "RUNNING"} else None,
                "tools": [{"id": step.id, "name": step.tool} for step in steps],
            } if visible else None,
        })
    return result


def _sse_event(name: str, data) -> str:
    payload = json.dumps(jsonable_encoder(data), ensure_ascii=False, separators=(",", ":"))
    return f"event: {name}\ndata: {payload}\n\n"


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


@app.get("/api/product")
def product_metadata():
    return active_manifest.PUBLIC_METADATA


@app.post("/api/auth/login")
def sign_in(data: s.LoginInput, request: Request, response: Response, db=Depends(get_db)):
    if request.headers.get("origin") not in {None, settings().origin}:
        raise DomainError("ORIGIN_DENIED", "请求来源不受信任", 403)
    user, token, csrf = login(db, data.username, data.password)
    record(db, user, "auth.login", user.id)
    db.commit()
    response.set_cookie("agent_session", token, httponly=True, secure=settings().cookie_secure, samesite="strict", max_age=28800)
    response.set_cookie("agent_csrf", csrf, httponly=False, secure=settings().cookie_secure, samesite="strict", max_age=28800)
    return {"user": public_user(user), "csrf": csrf}


@app.post("/api/auth/logout")
def sign_out(request: Request, response: Response, user=Depends(current_user), db=Depends(get_db)):
    db.delete(request.state.session); db.commit()
    response.delete_cookie("agent_session"); response.delete_cookie("agent_csrf")
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
    profile = db.get(m.UserProfile, user.id)
    if profile:
        db.delete(profile)
    if avatar:
        db.add(m.UserProfile(user_id=user.id, avatar_url=avatar, updated_at=now()))
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


@app.post("/api/model-profiles")
def create_model_profile_api(data: s.ModelProfileInput, user=Depends(current_user)):
    if not user.super_admin:
        raise DomainError("FORBIDDEN", "需要超级管理员", 403)
    try:
        return create_model_profile(data.model_dump())
    except ValueError as exc:
        raise DomainError("MODEL_CONFIG_INVALID", str(exc), 400)


@app.put("/api/model-profiles/{profile_id}")
def update_model_profile_api(profile_id: str, data: s.ModelProfileInput,
                             user=Depends(current_user)):
    if not user.super_admin:
        raise DomainError("FORBIDDEN", "需要超级管理员", 403)
    try:
        return update_model_profile(profile_id, data.model_dump())
    except KeyError:
        raise DomainError("MODEL_PROFILE_NOT_FOUND", "模型配置不存在", 404)
    except ValueError as exc:
        raise DomainError("MODEL_CONFIG_INVALID", str(exc), 400)


@app.post("/api/model-profiles/{profile_id}/activate")
def activate_model_profile_api(profile_id: str, user=Depends(current_user)):
    if not user.super_admin:
        raise DomainError("FORBIDDEN", "需要超级管理员", 403)
    try:
        return activate_model_profile(profile_id)
    except KeyError:
        raise DomainError("MODEL_PROFILE_NOT_FOUND", "模型配置不存在", 404)


@app.delete("/api/model-profiles/{profile_id}")
def delete_model_profile_api(profile_id: str, user=Depends(current_user)):
    if not user.super_admin:
        raise DomainError("FORBIDDEN", "需要超级管理员", 403)
    try:
        return delete_model_profile(profile_id)
    except KeyError:
        raise DomainError("MODEL_PROFILE_NOT_FOUND", "模型配置不存在", 404)
    except ValueError as exc:
        raise DomainError("MODEL_CONFIG_INVALID", str(exc), 400)


@app.get("/api/users")
def users(user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "user.manage")
    return [public_user_with_profile(db, u) for u in db.scalars(select(m.User).order_by(m.User.created_at))]


@app.post("/api/users")
def create_user(data: s.UserInput, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "user.manage")
    new = m.User(username=normalize_username(data.username), display_name=data.display_name, department=data.department, password_hash=hasher.hash(data.password))
    department = db.scalar(select(m.AssignmentGroup).where(
        m.AssignmentGroup.kind == "DEPARTMENT",
        m.AssignmentGroup.name == data.department,
        m.AssignmentGroup.active.is_(True)
    ))
    if not department:
        raise DomainError("INVALID_INPUT", "请先新增并选择有效部门")
    db.add(new); db.flush()
    db.add(m.AssignmentMember(group_id=department.id, user_id=new.id, is_head=False))
    record(db, user, "user.created", new.id, {"department_id": department.id})
    db.commit()
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


def definition_data(d):
    return {"id": d.id, "process_key": d.process_key, "version": d.version, "name": d.name,
            "status": d.status, "business_type": d.config['business_type'], "config": d.config, "category_id":d.category_id,
            "bpmn_xml": d.bpmn_xml, "package_hash": d.package_hash,'material_template_id':d.material_template_id,
            "edit_hash": _bpm().content_hash({'name': d.name, 'config': d.config, 'category_id':d.category_id,'material_template_id':d.material_template_id}),
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
    _bpm().validate(data.config)
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
    return _bpm().simulate(bind_contract(db,data.config,data.material_template_id), data.snapshot)


@app.get('/api/workflows/available')
def available_workflows(resource_type: str, resource_id: str, user=Depends(current_user), db=Depends(get_db)):
    return component("workflow_policy").available_workflows(
        db, user, resource_type, resource_id
    )


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
    _bpm().validate(data.config)
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
    _bpm().validate(d.config)
    require_category(db,d.config,d.category_id)
    from .assignments import check_publish
    check_publish(db,d.config)
    d.bpmn_xml = _bpm().compile_bpmn(d.config)
    _bpm().start_engine(d.bpmn_xml)
    d.package_hash = _bpm().content_hash({"config": d.config, "xml": d.bpmn_xml,'material_template_id':d.material_template_id})
    d.status = "PUBLISHED"; record(db, user, "workflow.published", d.id, {"hash": d.package_hash}); db.commit()
    return {"status": d.status, "hash": d.package_hash}


@app.get("/api/approvals")
def approvals(user=Depends(current_user), db=Depends(get_db)):
    q = select(m.ApprovalInstance).join(m.ApprovalSeat).where(m.ApprovalSeat.user_id == user.id, m.ApprovalSeat.status == "PENDING").distinct()
    results = []
    for instance in db.scalars(q.limit(100)):
        try: results.append(_business().approval_detail(db, user, instance))
        except DomainError: continue
    return results


@app.get("/api/approvals/{instance_id}")
def approval(instance_id: str, user=Depends(current_user), db=Depends(get_db)):
    instance = db.get(m.ApprovalInstance, instance_id)
    if not instance: raise DomainError("NOT_FOUND", "审批不存在", 404)
    return _business().approval_detail(db, user, instance)


@app.post("/api/approvals/decision-intent")
def decision_intent(data: s.DecisionInput, user=Depends(current_user), db=Depends(get_db)):
    result = _business().create_intent(db, user, "approval.decide", data.instance_id, data.model_dump())
    db.commit(); return result


@app.post("/api/approval-seat-transfers/intent")
def approval_seat_transfer_intent(data: s.ApprovalSeatTransferInput, user=Depends(current_user), db=Depends(get_db)):
    result = _business().create_intent(db, user, "approval.seat.transfer", data.instance_id, data.model_dump())
    db.commit(); return result


@app.post("/api/approval-seat-additions/intent")
def approval_seat_add_sign_intent(data: s.ApprovalSeatAddSignInput, user=Depends(current_user), db=Depends(get_db)):
    result = _business().create_intent(db, user, "approval.seat.add_sign", data.instance_id, data.model_dump())
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
    intent = db.scalar(select(m.HumanIntent).where(
        m.HumanIntent.id == intent_id, m.HumanIntent.user_id == user.id))
    result = _business().confirm_intent(db, user, intent_id, data.challenge)
    resumed_run = None
    if intent:
        from .agent_resume import queue_after_proposal_decision
        step = db.get(m.Step, intent.resource_id)
        if step:
            candidate = db.get(m.Run, step.run_id)
            if queue_after_proposal_decision(db, user, step.id, "approved", result):
                resumed_run = candidate
    db.commit()
    if resumed_run:
        publish_run_update(resumed_run.conversation_id, resumed_run.id, resumed_run.status)
    return result


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
def audit(offset: int = Query(0, ge=0), limit: int = Query(8, ge=1, le=50), user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "audit.read")
    total = db.scalar(select(func.count()).select_from(m.AuditEvent)) or 0
    events = list(db.scalars(select(m.AuditEvent).order_by(m.AuditEvent.created_at.desc()).offset(offset).limit(limit)))
    user_ids = {value for event in events for value in (event.user_id, event.resource_id) if value}
    users_by_id = {row.id: row for row in db.scalars(select(m.User).where(m.User.id.in_(user_ids)))} if user_ids else {}
    def summary(event):
        actor = users_by_id.get(event.user_id)
        target = users_by_id.get(event.resource_id)
        if event.action == "auth.login":
            return f"{(actor or target).display_name if (actor or target) else '用户'} 登录工作台"
        if event.action in {"user.created", "permission.changed", "permission.revoked", "capability.changed", "user.avatar.updated"} and target:
            return f"目标用户：{target.display_name}（{target.username}）"
        detail = event.detail or {}
        if "reason" in detail:
            return f"原因：{detail['reason']}"
        if "kind" in detail:
            return f"类型：{detail['kind']}"
        return ""
    items = [{"id": a.id, "action": a.action, "resource_id": a.resource_id, "created_at": a.created_at.isoformat(),
              "detail": a.detail, "actor_name": users_by_id[a.user_id].display_name if a.user_id in users_by_id else "系统",
              "summary": summary(a)}
             for a in events]
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@app.get("/api/conversations")
def conversations(archived: bool = Query(False), user=Depends(current_user), db=Depends(get_db)):
    ensure_conversation_flags(db)
    rows = list(db.scalars(select(m.Conversation).where(m.Conversation.user_id == user.id, m.Conversation.archived == archived)
                           .order_by(m.Conversation.pinned.desc(), m.Conversation.created_at.desc()).limit(100)))
    result = []
    for c in rows:
        waiting = False
        conversation_runs = db.scalars(select(m.Run).where(
            m.Run.conversation_id == c.id, m.Run.user_id == user.id).order_by(m.Run.created_at.desc()))
        for run in conversation_runs:
            steps = list(db.scalars(select(m.Step).where(m.Step.run_id == run.id).order_by(m.Step.sequence)))
            decisions = proposal_decisions(db, user.id, run, steps)
            if run.status not in {"QUEUED", "RUNNING"} and any(isinstance(step.result, dict) and step.result.get("proposal") and step.id not in decisions for step in steps):
                waiting = True
                break
        result.append({"id": c.id, "title": c.title, "pinned": c.pinned, "archived": c.archived,
                       "status": "WAITING_APPROVAL" if waiting else None,
                       "created_at": c.created_at.isoformat()})
    return result


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
    publish_run_update(run.conversation_id, run.id, run.status)
    return {"id": run.id, "conversation_id": conversation.id, "status": run.status, "agent_permission_mode": permission_mode}


@app.get("/api/conversations/{conversation_id}/runs")
def runs(conversation_id: str, user=Depends(current_user), db=Depends(get_db)):
    return conversation_runs_payload(db, user, conversation_id)


@app.get("/api/conversations/{conversation_id}/runs/events")
def run_events(conversation_id: str, request: Request, user=Depends(current_user), db=Depends(get_db)):
    conversation = db.scalar(select(m.Conversation).where(
        m.Conversation.id == conversation_id,
        m.Conversation.user_id == user.id,
    ))
    if not conversation:
        raise DomainError("NOT_FOUND", "会话不存在", 404)

    user_id = user.id
    session_expires_at = request.state.session.expires_at
    stream_factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    async def stream():
        initial_sent = False
        async for signal in subscribe_run_updates(conversation_id):
            if await request.is_disconnected():
                return
            if aware(session_expires_at) <= now():
                yield _sse_event("authorization", {"status": "expired"})
                return
            kind = signal.get("type")
            if kind == "heartbeat":
                yield ": keep-alive\n\n"
                continue
            if kind in {"ready", "update", "unavailable"}:
                with stream_factory() as stream_db:
                    live_user = stream_db.get(m.User, user_id)
                    live_conversation = stream_db.scalar(select(m.Conversation.id).where(
                        m.Conversation.id == conversation_id,
                        m.Conversation.user_id == user_id,
                    ))
                    if not live_user or not live_user.active or not live_conversation:
                        yield _sse_event("authorization", {"status": "revoked"})
                        return
                    payload = conversation_runs_payload(stream_db, live_user, conversation_id)
                if not initial_sent or kind == "update":
                    initial_sent = True
                    yield _sse_event("runs", payload)
                if kind == "unavailable":
                    yield _sse_event("transport", {"status": "redis_unavailable"})
                    return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/runs/{run_id}/cancel")
def cancel(run_id: str, user=Depends(current_user), db=Depends(get_db)):
    run = db.scalar(select(m.Run).where(m.Run.id == run_id, m.Run.user_id == user.id).with_for_update())
    if not run: raise DomainError("NOT_FOUND", "任务不存在", 404)
    if run.status not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
        run.status = "CANCELLED"; run.lease_epoch += 1
        run.checkpoint = {**(run.checkpoint or {}), "completed_at": now().isoformat()}
    db.commit()
    publish_run_update(run.conversation_id, run.id, run.status)
    return {"status": run.status}


@app.get("/api/capabilities")
def capabilities(user=Depends(current_user), db=Depends(get_db)):
    from .tool_gateway import TOOLS, SKILLS, available_tools, capability_descriptor, skill_context
    return {
        "tools": [capability_descriptor("TOOL", k, TOOLS[k]) for k in available_tools(db, user)],
        "skills": [{**capability_descriptor("SKILL", s["key"], SKILLS[s["key"]]), "version": s["version"], "agent_description": s["agent_description"]} for s in skill_context(db, user)],
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
active_manifest.install(app, domain_router)
