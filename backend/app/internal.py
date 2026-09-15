import secrets
from datetime import timedelta
from fastapi import Depends, Request
from sqlalchemy import select, or_, and_
from .db import get_db, now, aware
from .config import settings
from .errors import DomainError
from .models import Run, User, Step
from . import tool_gateway as tools
from .bpm import content_hash
from .authorization import fingerprint


def worker_auth(request: Request):
    expected = settings().worker_secret
    supplied = request.headers.get("authorization", "")
    if not expected or not secrets.compare_digest(supplied, "Bearer "+expected):
        raise DomainError("WORKER_UNAUTHENTICATED", "工作进程凭据无效", 401)


def fence(db, run_id, epoch):
    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if not run or run.status != "RUNNING" or run.lease_epoch != epoch or aware(run.lease_until) <= now():
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
        return {"evidence_id": prior.id, **prior.result}
    result = tools.execute(db, user, data["key"], data["arguments"], run=run)
    step = Step(run_id=run.id, sequence=sequence, tool=data["key"], request_hash=h, result=result)
    db.add(step); db.flush(); db.commit()
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


def install(app):
    from .mcp_api import install_mcp
    install_mcp(app,worker_auth,fence,execute_step)
    @app.post("/internal/runs/claim", dependencies=[Depends(worker_auth)])
    def claim(db=Depends(get_db)):
        if not settings().llm_enabled: return {"run": None}
        run = db.scalar(select(Run).where(or_(Run.status == "QUEUED", and_(Run.status == "RUNNING", Run.lease_until < now()))).order_by(Run.created_at).with_for_update(skip_locked=True).limit(1))
        if not run: return {"run": None}
        user = db.get(User, run.user_id)
        if not user or not user.active or user.security_version != run.security_version:
            run.status = "FAILED"; run.result = {"message": "权限已变化，请重新发起"}; db.commit(); return {"run": None}
        authorization_hash = fingerprint(db, user)
        if run.checkpoint and run.checkpoint.get("authorization_hash") != authorization_hash:
            run.status = "FAILED"; run.result = {"message": "授权范围或有效期已变化，请重新发起"}; db.commit(); return {"run": None}
        run.checkpoint = {**run.checkpoint, "authorization_hash": authorization_hash}
        run.status, run.lease_epoch, run.lease_until = "RUNNING", run.lease_epoch+1, now()+timedelta(seconds=120)
        from .files import run_files
        context = {"recent_requests":recent_requests(db,user,run),"files":run_files(db,user,run),"id": run.id, "epoch": run.lease_epoch, "prompt": run.prompt,
                   "tools": [tools.tool_schema(k) for k in tools.available_tools(db, user)], "skills": tools.skill_context(db, user), **run.checkpoint}
        db.commit(); return {"run": context}

    @app.post("/internal/runs/{run_id}/check", dependencies=[Depends(worker_auth)])
    def check(run_id: str, data: dict, db=Depends(get_db)):
        fence(db, run_id, data["epoch"]); db.commit(); return {"ok": True}

    @app.post("/internal/runs/{run_id}/tools", dependencies=[Depends(worker_auth)])
    def tool(run_id: str, data: dict, db=Depends(get_db)):
        return execute_step(db,run_id,data)

    @app.post("/internal/runs/{run_id}/checkpoint", dependencies=[Depends(worker_auth)])
    def checkpoint(run_id: str, data: dict, db=Depends(get_db)):
        run, _ = fence(db, run_id, data["epoch"])
        run.checkpoint = {**data["checkpoint"], "authorization_hash": run.checkpoint["authorization_hash"]}
        db.commit(); return {"ok": True}

    @app.post("/internal/runs/{run_id}/finish", dependencies=[Depends(worker_auth)])
    def finish(run_id: str, data: dict, db=Depends(get_db)):
        run, user = fence(db, run_id, data["epoch"])
        result = data["result"]
        steps = list(db.scalars(select(Step).where(Step.run_id == run.id)))
        if not set(result.get("evidence_ids", [])) <= {step.id for step in steps}:
            raise DomainError("EVIDENCE_INVALID", "结果证据不属于本次任务")
        run.result = {**result, "evidence": [{"id": step.id, "tool": step.tool, **step.result} for step in steps]}
        run.status = "SUCCEEDED"; run.lease_until = None; db.commit(); return {"ok": True}

    @app.post("/internal/runs/{run_id}/fail", dependencies=[Depends(worker_auth)])
    def fail(run_id: str, data: dict, db=Depends(get_db)):
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run and run.status == "RUNNING" and run.lease_epoch == data["epoch"]:
            code = str(data.get("code", "EXECUTION_FAILED"))[:80]
            message = {"TOOL_BUSINESS_REJECTED":"业务校验未通过，本次未执行。请核对联络单对象和必需资料后重新发起。", "MODEL_CONNECT_TIMEOUT": "模型连接超时，本次任务未完成，请稍后重新发起。",
                       'MODEL_LOCAL_UNAVAILABLE':'本地模型服务未启动或地址不可达，请检查本地模型服务。',
                       "MODEL_READ_TIMEOUT": "模型响应超时，本次任务未完成，请稍后重新发起。",
                       "MODEL_AUTH_FAILED": "模型服务认证失败，请联系管理员核对模型配置。",
                       "MODEL_RATE_LIMITED": "模型服务暂时繁忙，本次任务未完成，请稍后重新发起。",
                       "MODEL_OUTPUT_TRUNCATED": "模型回复不完整，本次任务未完成，请缩小问题范围后重试。"}.get(code, "任务执行未完成，可以核对配置和执行记录后重试")
            run.status = "FAILED"; run.result = {"message": message, "error_code": code}
            run.lease_until = None; db.commit()
        return {"ok": True}
