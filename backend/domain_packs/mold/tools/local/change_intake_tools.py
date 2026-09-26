"""本地设变承接 Tool；查询只读，prepare 只生成 Proposal。"""
from uuid import uuid4

from pydantic import Field, ValidationError, field_validator, model_validator
from sqlalchemy import or_, select

from domain_packs.mold import models as m
from domain_packs.mold.authorization import fingerprint, require
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel


class LocalChangeQueryInput(StrictModel):
    identifier: str | None = Field(default=None, min_length=1, max_length=120)
    project_id: str | None = Field(default=None, min_length=1, max_length=36)


class LocalChangeIntakeInput(StrictModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    mold_mode: str = Field(pattern=r"^(EXISTING|NEW_EXTERNAL)$")
    original_mold_id: str | None = Field(default=None, min_length=1, max_length=36)
    customer_mold_number: str | None = Field(default=None, max_length=160)
    classification: str = Field(pattern=r"^(CUSTOMER|INTERNAL|OUTSOURCE)$")
    execution_mode: str = Field(pattern=r"^(INTERNAL|OUTSOURCE)$")
    charge_status: str = Field(pattern=r"^(CHARGED|FREE|PENDING)$")
    contract_status: str = Field(pattern=r"^(NONE|REQUIRED|AVAILABLE|PENDING)$")
    execution_scope: str = Field(min_length=1, max_length=4000)
    customer_basis: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_mold_branch(self):
        if self.mold_mode == "EXISTING" and not self.original_mold_id:
            raise ValueError("EXISTING 模具设变必须提供本地原模具")
        if self.mold_mode == "NEW_EXTERNAL" and self.original_mold_id:
            raise ValueError("首次外部模具设变不能伪造原模具关联")
        return self


class LocalChangeAssociationInput(StrictModel):
    change_id: str = Field(min_length=1, max_length=36)
    expected_revision: int = Field(ge=1)
    association_type: str = Field(pattern=r"^(PROJECT|MOLD|CONTRACT|DOCUMENT)$")
    target_id: str = Field(min_length=1, max_length=80)
    target_revision: int | None = Field(default=None, ge=1)
    evidence: str = Field(min_length=1, max_length=4000)

    @field_validator("target_id")
    @classmethod
    def validate_local_target_id(cls, value):
        if value.lower().startswith("erp:") or value.lower().startswith(("http://", "https://")) or value.upper().startswith(("ERP-", "ERP_")):
            raise ValueError("关联对象必须是本地标识")
        if any(char.isspace() for char in value):
            raise ValueError("关联对象标识不能包含空白")
        return value


class LocalChangeAcceptanceInput(StrictModel):
    change_id: str = Field(min_length=1, max_length=36)
    expected_revision: int = Field(ge=1)
    acceptance_status: str = Field(pattern=r"^(ACCEPTED|REJECTED)$")
    acceptance_basis: str = Field(min_length=1, max_length=4000)


INPUTS = {
    "query_local_change_context": LocalChangeQueryInput,
    "prepare_local_change_intake": LocalChangeIntakeInput,
    "prepare_local_change_association": LocalChangeAssociationInput,
    "prepare_local_change_acceptance": LocalChangeAcceptanceInput,
}


def schema(key):
    return INPUTS[key].model_json_schema()


def _parse(key, arguments):
    try:
        return INPUTS[key].model_validate(arguments or {})
    except (ValidationError, KeyError) as error:
        message = error.errors()[0]["msg"] if hasattr(error, "errors") else "参数无效"
        raise DomainError("INVALID_TOOL_INPUT", f"本地设变工具参数无效：{message}") from None


def _serialize(row):
    return {
        "id": row.id,
        "number": row.number,
        "project_id": row.project_id,
        "original_mold_id": row.original_mold_id,
        "mold_mode": row.mold_mode,
        "customer_mold_number": row.customer_mold_number,
        "classification": row.classification,
        "execution_mode": row.execution_mode,
        "charge_status": row.charge_status,
        "contract_status": row.contract_status,
        "execution_scope": row.execution_scope,
        "customer_basis": row.customer_basis,
        "acceptance_status": row.acceptance_status,
        "status": row.status,
        "revision": row.revision,
        "latest_snapshot": row.latest_snapshot or {},
        "derived_status": {
            "requires_manual_intake": row.mold_mode == "NEW_EXTERNAL",
            "has_open_execution_or_recheck_items": None,
            "execution_status": "NOT_QUERIED",
        },
    }


def _validate_intake_references(db, user, data):
    if data.project_id:
        project = db.get(m.Project, data.project_id)
        if not project:
            raise DomainError("NOT_FOUND", "本地项目不存在", 404)
        require(db, user, "project.read", {"project_id": project.id})
    if data.mold_mode == "EXISTING":
        mold = db.get(m.Mold, data.original_mold_id)
        if not mold:
            raise DomainError("NOT_FOUND", "本地原模具不存在", 404)
        if data.project_id and not db.scalar(select(m.ProjectMold).where(
            m.ProjectMold.project_id == data.project_id,
            m.ProjectMold.mold_id == mold.id,
        )):
            raise DomainError("MOLD_PROJECT_CONFLICT", "原模具不属于所选本地项目", 409)
    if data.mold_mode == "NEW_EXTERNAL" and not data.customer_mold_number:
        raise DomainError("CUSTOMER_MOLD_NUMBER_REQUIRED", "首次外部模具设变必须提供客户模号", 400)


def _query(db, user, data):
    if not db:
        raise DomainError("LOCAL_CONTEXT_REQUIRED", "查询本地设变上下文需要数据库会话", 500)
    if not user.active:
        raise DomainError("FORBIDDEN", "账号不可用", 403)
    require(db, user, "engineering_change.read", {"project_id": data.project_id} if data.project_id else {})
    query = select(m.LocalChangeIntake)
    if data.project_id:
        query = query.where(m.LocalChangeIntake.project_id == data.project_id)
    if data.identifier:
        query = query.where(or_(
            m.LocalChangeIntake.id == data.identifier,
            m.LocalChangeIntake.number == data.identifier,
            m.LocalChangeIntake.customer_mold_number == data.identifier,
        ))
    rows = list(db.scalars(query.order_by(m.LocalChangeIntake.created_at.desc()).limit(100)))
    data = []
    for row in rows:
        item = _serialize(row)
        item["customer_mold_history"] = [{
            "customer_mold_number": history.customer_mold_number,
            "valid_from": history.valid_from.isoformat() if history.valid_from else None,
            "valid_to": history.valid_to.isoformat() if history.valid_to else None,
            "evidence": history.evidence,
            "revision": history.revision,
        } for history in db.scalars(select(m.LocalChangeCustomerMoldHistory).where(
            m.LocalChangeCustomerMoldHistory.change_id == row.id,
        ).order_by(m.LocalChangeCustomerMoldHistory.created_at))]
        item["associations"] = [{
            "type": association.association_type,
            "target_id": association.target_id,
            "target_revision": association.target_revision,
            "evidence": association.evidence,
        } for association in db.scalars(select(m.LocalChangeAssociation).where(
            m.LocalChangeAssociation.change_id == row.id,
        ).order_by(m.LocalChangeAssociation.created_at))]
        item["derived_status"] = {
            "requires_manual_intake": row.mold_mode == "NEW_EXTERNAL",
            "has_open_execution_or_recheck_items": row.acceptance_status == "ACCEPTED",
            "accepted_is_not_closed": row.acceptance_status == "ACCEPTED",
        }
        data.append(item)
    return {
        "data": data,
        "source": "agent_db",
        "as_of": now().isoformat(),
        "limitations": ["仅返回 MoldPilot 本地设变承接记录，不读取 ERP 或外部系统。"],
    }


def _proposal(key, data, user, run=None, display=None, db=None):
    proposal = {
        "tool": key,
        "action": key.removeprefix("prepare_"),
        "input": data.model_dump(mode="json"),
        "display": display or data.model_dump(mode="json"),
        "operation_id": str(uuid4()),
        "security_version": user.security_version,
        "authorization_hash": fingerprint(db, user) if db else None,
        "requires_approval": False,
        "confirmation_policy": proposal_confirmation_policy(run, requires_approval=False),
    }
    return {
        "data": [],
        "source": "agent_proposal",
        "as_of": now().isoformat(),
        "proposal": proposal,
        "limitations": ["仅准备本地设变操作建议，尚未写入业务数据；必须由本人确认。"],
    }


def execute_tool(db, user, key, arguments, run=None):
    data = _parse(key, arguments)
    if key == "query_local_change_context":
        return _query(db, user, data)
    if db:
        require(db, user, "engineering_change.create", {"project_id": getattr(data, "project_id", None)})
        if key == "prepare_local_change_intake":
            _validate_intake_references(db, user, data)
    if key == "prepare_local_change_intake":
        display = {
            **data.model_dump(mode="json"),
            "说明": "确认后才登记本地设变承接记录；收费、合同、执行方式和客户依据分别保留。",
        }
    elif key == "prepare_local_change_association":
        display = {
            **data.model_dump(mode="json"),
            "说明": "确认后才追加本地对象关联，并复核设变版本；多候选不会自动选择。",
        }
    else:
        display = {
            **data.model_dump(mode="json"),
            "说明": "确认后才记录承接决定；接受不等于执行完成或工程联络关闭。",
        }
    return _proposal(key, data, user, run=run, display=display, db=db)


def _source_context(db, user, step_id):
    from domain_packs.mold.tool_gateway import available_tools

    step = db.get(m.Step, step_id)
    run = db.get(m.Run, step.run_id) if step else None
    if not run or run.user_id != user.id or run.status not in {"RUNNING", "SUCCEEDED"}:
        raise DomainError("NOT_FOUND", "操作建议不存在或无权访问", 404)
    proposal = step.result.get("proposal") if isinstance(step.result, dict) else None
    if not proposal or proposal.get("tool") not in available_tools(db, user):
        raise DomainError("TOOL_FORBIDDEN", "本地设变操作能力不可用", 403)
    if run.security_version != user.security_version or (
        proposal.get("authorization_hash") and proposal.get("authorization_hash") != fingerprint(db, user)
    ):
        raise DomainError("AUTHORIZATION_CHANGED", "授权已变化，请重新准备操作", 403)
    return proposal, run


def source(db, user, step_id):
    proposal, _run = _source_context(db, user, step_id)
    return proposal


def validate_intent(db, user, payload):
    proposal, run = _source_context(db, user, payload["step_id"])
    if content_hash(proposal) != payload.get("proposal_hash"):
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    data = _parse(proposal["tool"], proposal["input"])
    return proposal, data, run


def _require_local_write(db, user, project_id=None):
    if not db:
        raise DomainError("LOCAL_CONTEXT_REQUIRED", "确认本地设变操作需要数据库会话", 500)
    require(db, user, "engineering_change.create", {"project_id": project_id} if project_id else {})


def confirm(db, user, payload):
    proposal, data, _run = validate_intent(db, user, payload)
    action = proposal["action"]
    if action == "local_change_intake":
        _require_local_write(db, user, data.project_id)
        existing = db.scalar(select(m.LocalChangeIntake).where(
            m.LocalChangeIntake.created_by == user.id,
            m.LocalChangeIntake.request_key == proposal["operation_id"],
        ))
        if existing:
            return {"id": existing.id, "number": existing.number, "revision": existing.revision, "status": existing.status}
        number = f"LC-{now().strftime('%Y%m%d%H%M%S')}-{str(proposal['operation_id'])[:8]}"
        row = m.LocalChangeIntake(
            number=number,
            project_id=data.project_id,
            original_mold_id=data.original_mold_id,
            mold_mode=data.mold_mode,
            customer_mold_number=data.customer_mold_number,
            classification=data.classification,
            execution_mode=data.execution_mode,
            charge_status=data.charge_status,
            contract_status=data.contract_status,
            execution_scope=data.execution_scope,
            customer_basis=data.customer_basis,
            created_by=user.id,
            request_key=proposal["operation_id"],
            request_hash=content_hash(proposal),
            latest_snapshot=data.model_dump(mode="json"),
        )
        db.add(row)
        db.flush()
        if data.customer_mold_number:
            db.add(m.LocalChangeCustomerMoldHistory(
                change_id=row.id,
                customer_mold_number=data.customer_mold_number,
                valid_from=now(),
                evidence=data.customer_basis,
                recorded_by=user.id,
            ))
            db.flush()
        return {"id": row.id, "number": row.number, "revision": row.revision, "status": row.status}
    if action == "local_change_association":
        _require_local_write(db, user)
        row = db.get(m.LocalChangeIntake, data.change_id)
        if not row:
            raise DomainError("NOT_FOUND", "本地设变记录不存在", 404)
        if row.revision != data.expected_revision:
            raise DomainError("VERSION_CONFLICT", "设变版本已变化，请重新查询", 409)
        db.add(m.LocalChangeAssociation(
            change_id=row.id, association_type=data.association_type,
            target_id=data.target_id, target_revision=data.target_revision,
            evidence=data.evidence, created_by=user.id,
        ))
        row.revision += 1
        return {"id": row.id, "revision": row.revision, "status": row.status}
    if action == "local_change_acceptance":
        _require_local_write(db, user)
        row = db.get(m.LocalChangeIntake, data.change_id)
        if not row:
            raise DomainError("NOT_FOUND", "本地设变记录不存在", 404)
        if row.revision != data.expected_revision:
            raise DomainError("VERSION_CONFLICT", "设变版本已变化，请重新查询", 409)
        row.acceptance_status = data.acceptance_status
        row.status = "ACCEPTED" if data.acceptance_status == "ACCEPTED" else "REJECTED"
        row.latest_snapshot = {**(row.latest_snapshot or {}), "acceptance_basis": data.acceptance_basis}
        row.revision += 1
        return {"id": row.id, "revision": row.revision, "status": row.status}
    raise DomainError("ACTION_UNKNOWN", "本地设变动作未登记")
