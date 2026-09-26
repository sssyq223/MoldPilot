import json
import secrets
from datetime import timedelta
from fastapi import Depends, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, or_, and_
from .db import get_db, now, aware
from .config import settings, model_settings
from .errors import DomainError
from .models import AuditEvent, HumanIntent, Run, User, Step
from . import tool_gateway as tools
from .bpm import content_hash
from .authorization import fingerprint
from .run_events import publish_run_update
from agent_core.run_status import (LEGACY_QUEUED, LEGACY_RUNNING, RUNNING_STATUSES,
                                   SCOPED_QUEUED, SCOPED_RUNNING, public_run_status)


def worker_auth(request: Request):
    expected = settings().worker_secret
    supplied = request.headers.get("authorization", "")
    if not expected or not secrets.compare_digest(supplied, "Bearer "+expected):
        raise DomainError("WORKER_UNAUTHENTICATED", "工作进程凭据无效", 401)


def fence(db, run_id, epoch):
    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if not run or run.status not in RUNNING_STATUSES or run.lease_epoch != epoch or aware(run.lease_until) <= now():
        raise DomainError("LEASE_LOST", "任务已停止或执行租约失效", 409)
    user = db.scalar(select(User).where(User.id == run.user_id).with_for_update(read=True))
    if not user or not user.active or user.security_version != run.security_version:
        raise DomainError("AUTHORIZATION_CHANGED", "权限已变化，需要重新发起任务", 403)
    if run.checkpoint.get("authorization_hash") != fingerprint(db, user):
        raise DomainError("AUTHORIZATION_CHANGED", "授权范围或有效期已变化，需要重新发起任务", 403)
    run.lease_until = now()+timedelta(seconds=120)
    return run, user



def execute_step(db, run_id, data):
    run, user = fence(db, run_id, data["epoch"])
    sequence = data["sequence"]
    if type(sequence) is not int or not 0 <= sequence < 30: raise DomainError("BUDGET_EXCEEDED", "工具次数已达上限")
    h = content_hash({"key": data["key"], "arguments": data["arguments"]})
    prior = db.scalar(select(Step).where(Step.run_id == run.id, Step.sequence == sequence))
    if prior:
        if prior.request_hash != h: raise DomainError("STEP_CONFLICT", "同一步骤内容发生变化", 409)
        if data["key"] not in tools.available_tools(db, user):
            raise DomainError("TOOL_FORBIDDEN", "工具授权已变化", 403)
        db.commit()
        publish_run_update(run.conversation_id, run.id, public_run_status(run.status))
        return {"evidence_id": prior.id, **prior.result}
    # 持久化回执与 MCP 使用同一 JSON 表示，避免日期等原生类型仅在 HTTP 层被转换。
    result = jsonable_encoder(tools.execute(db, user, data["key"], data["arguments"], run=run))
    step = Step(run_id=run.id, sequence=sequence, tool=data["key"], request_hash=h, result=result)
    db.add(step); db.flush(); db.commit()
    publish_run_update(run.conversation_id, run.id, public_run_status(run.status))
    return {"evidence_id": step.id, **result}

def recent_requests(db,user,run):
    # Only the same user's prior input is reused, never stale tool data, approvals,
    # assistant claims or another conversation. New business facts must be queried.
    prior=db.scalars(select(Run).where(Run.user_id==user.id,Run.conversation_id==run.conversation_id,
        Run.created_at<run.created_at).order_by(Run.created_at.desc(),Run.id.desc()).limit(4))
    selected=[];remaining=6000
    for previous in prior:
        if len(previous.prompt)>remaining:break
        selected.append(previous.prompt);remaining-=len(previous.prompt)
    return list(reversed(selected))


def conversation_history(db, user, run, current_authorization_hash, allowed_tools=None):
    """Return a bounded, authorization-filtered transcript for model context.

    User messages and attachment metadata remain useful for reference. Assistant
    business answers and confirmed actions are included only when the prior run
    was produced under the current authorization fingerprint.
    """
    from .files import run_files
    allowed_tools = set(allowed_tools or ())
    prior = list(db.scalars(select(Run).where(
        Run.user_id == user.id, Run.conversation_id == run.conversation_id,
        Run.created_at < run.created_at,
    ).order_by(Run.created_at, Run.id).limit(8)))
    transcript = []
    for previous in prior:
        checkpoint = previous.checkpoint if isinstance(previous.checkpoint, dict) else {}
        previous_hash = checkpoint.get("authorization_hash")
        visible = (previous.security_version == user.security_version
                   and previous_hash == current_authorization_hash)
        result = previous.result if visible and isinstance(previous.result, dict) else None
        assistant = None
        if result:
            assistant = {key: result[key] for key in (
                'response_kind', 'summary', 'suggestions', 'message', 'error_code',
                'proposal_decision',
            ) if key in result}
        actions = []
        if visible:
            confirmed = db.execute(select(HumanIntent, Step).join(
                Step, Step.id == HumanIntent.resource_id,
            ).where(
                Step.run_id == previous.id,
                HumanIntent.user_id == user.id,
                HumanIntent.receipt.is_not(None),
            ).order_by(HumanIntent.created_at.desc(), HumanIntent.id.desc()).limit(8))
            for intent, step in confirmed:
                if allowed_tools and step.tool not in allowed_tools:
                    continue
                receipt = intent.receipt if isinstance(intent.receipt, dict) else {}
                references = {key: value for key, value in receipt.items()
                              if (key == "id" or key.endswith("_id") or key in {
                                  "action", "status", "revision", "row_version",
                              }) and isinstance(value, (str, int, float, bool, type(None)))}
                actions.append({"step_id": step.id, "tool": step.tool, "references": references})
        transcript.append({
            "run_id": previous.id,
            "created_at": previous.created_at.isoformat(),
            "status": public_run_status(previous.status),
            "user": {"content": previous.prompt, "attachments": run_files(db, user, previous)},
            "assistant": assistant,
            "confirmed_actions": actions,
        })
    return transcript


def install(app):
    from .mcp_api import install_mcp
    install_mcp(app,worker_auth,fence,execute_step)
    @app.post("/internal/runs/claim", dependencies=[Depends(worker_auth)])
    def claim(db=Depends(get_db)):
        if not model_settings().llm_enabled:
            return {"run": None}
        worker_scope = settings().worker_scope
        scoped_for_this_worker = Run.checkpoint["worker_scope"].as_string() == worker_scope
        run = db.scalar(select(Run).where(or_(
            Run.status == LEGACY_QUEUED,
            and_(Run.status == SCOPED_QUEUED, scoped_for_this_worker),
            and_(Run.status == LEGACY_RUNNING, Run.lease_until < now()),
            and_(Run.status == SCOPED_RUNNING, scoped_for_this_worker, Run.lease_until < now()),
        )).order_by(Run.created_at).with_for_update(skip_locked=True).limit(1))
        if not run: return {"run": None}
        user = db.get(User, run.user_id)
        if not user or not user.active or user.security_version != run.security_version:
            run.status = "FAILED"; run.result = {"message": "权限已变化，请重新发起"}; db.commit()
            publish_run_update(run.conversation_id, run.id, public_run_status(run.status))
            return {"run": None}
        from .run_model_selection import selection_for_run, runtime_for_selection
        from agent_core.model_adapter import ModelError
        try:
            selection = selection_for_run(db, user, run)
            runtime_for_selection(selection)
        except (ModelError, DomainError) as error:
            run.status = 'FAILED'
            run.result = {'message':'任务绑定的模型配置已变化或不可用，请重新选择后发起',
                          'error_code':error.code if isinstance(error, DomainError) else str(error)}
            run.lease_until = None
            db.commit()
            publish_run_update(run.conversation_id, run.id, run.status)
            return {'run':None}
        authorization_hash = fingerprint(db, user)
        checkpoint = run.checkpoint if isinstance(run.checkpoint, dict) else {}
        checkpoint = {**checkpoint, 'model_selection': selection}
        if not checkpoint.get("run_trigger"):
            creation = db.scalar(select(AuditEvent).where(
                AuditEvent.action == "agent.run.created",
                AuditEvent.resource_id == run.id,
            ).order_by(AuditEvent.created_at.desc()).limit(1))
            detail = creation.detail if creation and isinstance(creation.detail, dict) else {}
            trigger = detail.get("run_trigger")
            if trigger in {"USER", "ATTACHMENT_UPLOAD"}:
                checkpoint = {**checkpoint, "run_trigger": trigger}
        existing_authorization_hash = checkpoint.get("authorization_hash")
        if existing_authorization_hash and existing_authorization_hash != authorization_hash:
            run.status = "FAILED"; run.result = {"message": "授权范围或有效期已变化，请重新发起"}; db.commit()
            publish_run_update(run.conversation_id, run.id, public_run_status(run.status))
            return {"run": None}
        scoped = run.status in {SCOPED_QUEUED, SCOPED_RUNNING}
        run.checkpoint = {
            **checkpoint,
            "agent_permission_mode": checkpoint.get("agent_permission_mode", "ask"),
            "authorization_hash": authorization_hash,
            **({"worker_scope": worker_scope} if scoped else {}),
        }
        run.status = SCOPED_RUNNING if scoped else LEGACY_RUNNING
        run.lease_epoch, run.lease_until = run.lease_epoch+1, now()+timedelta(seconds=120)
        from .files import conversation_files, run_files
        available_tools = tools.available_tools(db, user)
        context = {
            **run.checkpoint,
            "recent_requests": recent_requests(db, user, run),
            "conversation_history": conversation_history(
                db, user, run, authorization_hash, allowed_tools=available_tools,
            ),
            "conversation_files": jsonable_encoder(conversation_files(run.conversation_id, user, db)[:10]),
            "files": run_files(db, user, run),
            "id": run.id, "epoch": run.lease_epoch, "prompt": run.prompt,
            "tools": [tools.tool_schema(k) for k in available_tools],
            "skills": tools.skill_context(db, user),
        }
        db.commit()
        publish_run_update(run.conversation_id, run.id, public_run_status(run.status))
        return {"run": context}

    @app.post("/internal/runs/{run_id}/check", dependencies=[Depends(worker_auth)])
    def check(run_id: str, data: dict, db=Depends(get_db)):
        fence(db, run_id, data["epoch"]); db.commit(); return {"ok": True}

    @app.post("/internal/runs/{run_id}/tools", dependencies=[Depends(worker_auth)])
    def tool(run_id: str, data: dict, db=Depends(get_db)):
        return execute_step(db,run_id,data)

    @app.post("/internal/runs/{run_id}/checkpoint", dependencies=[Depends(worker_auth)])
    def checkpoint(run_id: str, data: dict, db=Depends(get_db)):
        run, _ = fence(db, run_id, data["epoch"])
        previous = run.checkpoint if isinstance(run.checkpoint, dict) else {}
        # Proposal decisions and the preceding final answer are host-owned
        # conversation state.  The generic harness replaces its own execution
        # checkpoint on every streamed update and must not erase them while a
        # confirmed proposal is resumed for the final receipt response.
        if not previous.get("run_trigger"):
            creation = db.scalar(select(AuditEvent).where(
                AuditEvent.action == "agent.run.created",
                AuditEvent.resource_id == run.id,
            ).order_by(AuditEvent.created_at.desc()).limit(1))
            detail = creation.detail if creation and isinstance(creation.detail, dict) else {}
            trigger = detail.get("run_trigger")
            if trigger in {"USER", "ATTACHMENT_UPLOAD"}:
                previous = {**previous, "run_trigger": trigger}
        host_state = {
            key: previous[key]
            for key in (
                "proposal_decisions", "proposal_resolution", "prior_finals",
                "run_trigger", "model_selection", "post_proposal_continuation",
                "worker_scope",
            )
            if key in previous
        }
        run.checkpoint = {
            **data["checkpoint"],
            **host_state,
            "agent_permission_mode": data["checkpoint"].get(
                "agent_permission_mode", previous.get("agent_permission_mode", "ask")
            ),
            "authorization_hash": previous["authorization_hash"],
        }
        db.commit()
        publish_run_update(run.conversation_id, run.id, public_run_status(run.status))
        return {"ok": True}

    @app.post("/internal/runs/{run_id}/finish", dependencies=[Depends(worker_auth)])
    def finish(run_id: str, data: dict, db=Depends(get_db)):
        run, user = fence(db, run_id, data["epoch"])
        result = data["result"]
        steps = list(db.scalars(select(Step).where(Step.run_id == run.id)))
        if not set(result.get("evidence_ids", [])) <= {step.id for step in steps}:
            raise DomainError("EVIDENCE_INVALID", "结果证据不属于本次任务")
        run.result = {**result, "evidence": [{"id": step.id, "tool": step.tool, **step.result} for step in steps]}
        checkpoint = run.checkpoint if isinstance(run.checkpoint, dict) else {}
        if not checkpoint.get("run_trigger"):
            creation = db.scalar(select(AuditEvent).where(
                AuditEvent.action == "agent.run.created",
                AuditEvent.resource_id == run.id,
            ).order_by(AuditEvent.created_at.desc()).limit(1))
            detail = creation.detail if creation and isinstance(creation.detail, dict) else {}
            trigger = detail.get("run_trigger")
            if trigger in {"USER", "ATTACHMENT_UPLOAD"}:
                checkpoint = {**checkpoint, "run_trigger": trigger}
        run.checkpoint = {**checkpoint, "completed_at": now().isoformat()}
        run.status = "SUCCEEDED"; run.lease_until = None; db.commit()
        publish_run_update(run.conversation_id, run.id, public_run_status(run.status))
        return {"ok": True}

    @app.post("/internal/runs/{run_id}/fail", dependencies=[Depends(worker_auth)])
    def fail(run_id: str, data: dict, db=Depends(get_db)):
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run and run.status in RUNNING_STATUSES and run.lease_epoch == data["epoch"]:
            code = str(data.get("code", "EXECUTION_FAILED"))[:80]
            detail = " ".join(str(data.get("detail") or "").split())[:1000]
            message = detail or {"TOOL_BUSINESS_REJECTED":"业务校验未通过，本次未执行。请核对当前业务对象和必需资料后重新发起。", "MODEL_CONNECT_TIMEOUT": "模型连接超时，本次任务未完成，请稍后重新发起。",
                        'MODEL_LOCAL_UNAVAILABLE':'本地模型服务未启动或地址不可达，请检查本地模型服务。',
                        "MODEL_READ_TIMEOUT": "模型响应超时，本次任务未完成，请稍后重新发起。",
                        "MODEL_NETWORK_ERROR": "模型服务连接中断，本次任务未完成。系统已自动重试一次；请稍后重新发起或核对模型服务连接。",
                        "MODEL_AUTH_FAILED": "模型服务认证失败，请联系管理员核对模型配置。",
                       "MODEL_RATE_LIMITED": "模型服务暂时繁忙，本次任务未完成，请稍后重新发起。",
                       "MODEL_OUTPUT_TRUNCATED": "模型回复不完整，本次任务未完成，请缩小问题范围后重试。",
                       "CONTEXT_BUDGET_EXCEEDED": "模型请求超过当前配置的安全上下文预算。请检查模型窗口配置及工具返回内容；附件大小不一定是原因。"}.get(code, "任务执行未完成，可以核对配置和执行记录后重试")
            run.status = "FAILED"; run.result = {"message": message, "error_code": code}
            run.checkpoint = {**(run.checkpoint or {}), "completed_at": now().isoformat()}
            run.lease_until = None; db.commit()
            publish_run_update(run.conversation_id, run.id, public_run_status(run.status))
        return {"ok": True}
