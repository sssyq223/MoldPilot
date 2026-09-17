"""Business-neutral proposal status and confirmation-intent endpoints."""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from . import models as m
from .bpm import content_hash
from .db import get_db
from .errors import DomainError
from .proposal_registry import require_tool_handler
from .security import current_user
from .agent_resume import queue_after_proposal_decision


router = APIRouter(prefix="/api/proposals", tags=["agent-proposals"])


def proposal_source(db, user, step_id: str):
    step = db.get(m.Step, step_id)
    if not step:
        raise DomainError("NOT_FOUND", "操作建议不存在或无权访问", 404)
    handler = require_tool_handler(step.tool)
    return handler, handler.implementation().source(db, user, step_id)


@router.get("/{step_id}")
def proposal_status(step_id: str, user=Depends(current_user), db=Depends(get_db)):
    step = db.get(m.Step, step_id)
    run = db.get(m.Run, step.run_id) if step else None
    if not step or not run or run.user_id != user.id:
        raise DomainError("NOT_FOUND", "操作建议不存在或无权访问", 404)
    handler = require_tool_handler(step.tool)
    intent = db.scalar(select(m.HumanIntent).where(
        m.HumanIntent.user_id == user.id,
        m.HumanIntent.action == handler.action,
        m.HumanIntent.resource_id == step_id,
        m.HumanIntent.receipt.is_not(None),
    ).order_by(m.HumanIntent.created_at.desc()))
    return {"receipt": intent.receipt if intent else None}


@router.post("/{step_id}/intent")
def proposal_intent(step_id: str, user=Depends(current_user), db=Depends(get_db)):
    from .business import create_intent

    handler, proposal = proposal_source(db, user, step_id)
    payload = {"step_id": step_id, "proposal_hash": content_hash(proposal)}
    result = create_intent(db, user, handler.action, step_id, payload)
    result["display"] = proposal["display"]
    result["confirmation_policy"] = proposal.get("confirmation_policy")
    db.commit()
    return result


@router.post("/{step_id}/dismiss")
def dismiss_proposal(step_id: str, user=Depends(current_user), db=Depends(get_db)):
    proposal_source(db, user, step_id)
    queue_after_proposal_decision(db, user, step_id, "dismissed")
    db.commit()
    return {"status": "dismissed"}
