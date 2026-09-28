"""Human-confirmed ERP project/mold handoff.

The ERP remains the source of truth for the mold identity.  This flow only
captures a bounded ERP evidence snapshot and creates the Agent-side
``mold``/``project_mold`` relation after an explicit human confirmation.
"""

from datetime import datetime

from pydantic import Field, ValidationError
from sqlalchemy import select
from fastapi import APIRouter, Depends

from domain_packs.mold import models as m
from domain_packs.mold.erp.project import mold_handoff
from domain_packs.mold.authorization import access, require, fingerprint
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.db import get_db, now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.ports.security import current_user
from agent_core.events import record


class MoldHandoffProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36,
        description="查询结果中的 Agent 项目 ID。")
    project_version: int = Field(ge=1,
        description="查询结果中的 Agent 项目 row_version。")
    erp_project_code: str = Field(min_length=1, max_length=80,
        description="ERP 查询结果中的精确项目号，不能使用相似项目号。")
    erp_project_source_ref: str | None = Field(default=None, max_length=300,
        description="ERP 项目候选的来源引用；未提供时由已注册只读端点和项目号生成。")
    erp_mold_code: str = Field(min_length=1, max_length=80,
        description="ERP 查询结果中的精确模具号。")
    erp_source_ref: str = Field(min_length=1, max_length=300,
        description="ERP 查询结果中的 source_ref。")
    internal_number: str = Field(min_length=1, max_length=80,
        description="本次交接要建立的 Agent 内部模具号，必须与 ERP mold_code 完全一致。")
    mold_name: str = Field(min_length=1, max_length=150,
        description="人工核对后的 Agent 模具名称。")
    evidence: str = Field(min_length=1, max_length=4000,
        description="ERP 项目/模具核对依据或内部交接说明。")


def schema():
    return MoldHandoffProposalInput.model_json_schema()


def parse(arguments):
    try:
        return MoldHandoffProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError(
            "INVALID_TOOL_INPUT",
            "ERP 模具交接参数不完整或不符合要求：" + error.errors()[0]["msg"],
        ) from None


def _project(db, user, data):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    scope = {"project_id": project.id}
    require(db, user, "project.read", scope)
    require(db, user, "project.dossier.read", scope)
    require(db, user, "internal_start.read", scope)
    require(db, user, "internal_start.create", scope)
    if project.row_version != data.project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备", 409)
    return project


def _fresh_candidate(db, user, project, data):
    evidence = mold_handoff.query(
        db, user, project, erp_project_code=data.erp_project_code
    )
    if evidence.get("handoff_state") == "LOCAL_ASSOCIATION_PRESENT":
        raise DomainError(
            "MOLD_HANDOFF_EXISTS",
            "当前项目已经存在本地模具关联，请先查询现有关联来源",
            409,
        )
    if evidence.get("handoff_state") != "ERP_CANDIDATE_REQUIRES_HANDOFF":
        state = evidence.get("handoff_state") or "UNKNOWN"
        raise DomainError(
            "MOLD_HANDOFF_NOT_UNIQUE",
            "ERP 当前没有可供人工交接的唯一项目模具候选（" + state + "）",
            409,
        )
    exact = [
        row for row in evidence.get("exact_project_records", [])
        if str(row.get("project_code") or "").strip() == data.erp_project_code
        and str(row.get("mold_code") or "").strip() == data.erp_mold_code
        and str(row.get("source_ref") or "").strip() == data.erp_source_ref
    ]
    if len(exact) != 1:
        raise DomainError(
            "MOLD_HANDOFF_EVIDENCE_CHANGED",
            "ERP 候选已变化或来源引用不一致，请重新查询后准备交接",
            409,
        )
    candidate = exact[0]
    expected_project_ref = _canonical_project_source_ref(data.erp_project_code)
    if data.erp_project_source_ref and data.erp_project_source_ref != expected_project_ref:
        raise DomainError(
            "MOLD_HANDOFF_EVIDENCE_CHANGED",
            "ERP 项目来源引用与当前只读候选不一致，请重新查询后准备交接",
            409,
        )
    if data.internal_number != str(candidate.get("mold_code") or "").strip():
        raise DomainError(
            "MOLD_HANDOFF_NUMBER_MISMATCH",
            "Agent 内部模具号必须与 ERP mold_code 完全一致",
            409,
        )
    if not evidence.get("as_of"):
        raise DomainError(
            "MOLD_HANDOFF_SOURCE_TIME_MISSING",
            "ERP 候选缺少读取时点，不能形成可追溯交接快照",
            409,
        )
    return evidence, candidate


def _canonical_project_source_ref(erp_project_code):
    """Build the source reference for the registered ERP project lookup."""
    return "scheduling/api/business/molds/:" + str(erp_project_code).strip()


def _project_source_ref(data):
    return _canonical_project_source_ref(data.erp_project_code)


def _existing_mapping(db, project, data):
    mapping = db.scalar(
        select(m.ProjectERPMapping).where(
            m.ProjectERPMapping.project_id == project.id,
            m.ProjectERPMapping.status == "CONFIRMED",
        )
    )
    if mapping and mapping.erp_project_code != data.erp_project_code:
        raise DomainError(
            "PROJECT_ERP_MAPPING_CONFLICT",
            "Agent 项目已经确认了另一 ERP 项目映射，不能覆盖原映射",
            409,
        )
    other = db.scalar(
        select(m.ProjectERPMapping).where(
            m.ProjectERPMapping.erp_project_code == data.erp_project_code,
            m.ProjectERPMapping.status == "CONFIRMED",
            m.ProjectERPMapping.project_id != project.id,
        )
    )
    if other:
        raise DomainError(
            "PROJECT_ERP_MAPPING_IN_USE",
            "该 ERP 项目已经映射到其他 Agent 项目，不能重复交接",
            409,
        )
    return mapping


def _existing_conflict(db, project, data):
    mold = db.scalar(
        select(m.Mold).where(m.Mold.internal_number == data.internal_number)
    )
    if not mold:
        return None
    links = list(
        db.scalars(
            select(m.ProjectMold).where(m.ProjectMold.mold_id == mold.id)
        )
    )
    if any(link.project_id != project.id for link in links):
        raise DomainError(
            "MOLD_HANDOFF_NUMBER_IN_USE",
            "该内部模具号已关联其他项目，不能重复交接",
            409,
        )
    raise DomainError(
        "MOLD_HANDOFF_EXISTS",
        "该内部模具号已经存在 Agent 记录，请查询后继续",
        409,
    )


def preview(db, user, data: MoldHandoffProposalInput):
    project = _project(db, user, data)
    mapping = _existing_mapping(db, project, data)
    evidence, candidate = _fresh_candidate(db, user, project, data)
    _existing_conflict(db, project, data)
    fields = access(db, user, "project.read", {"project_id": project.id}).fields
    project_name = project.name if "name" in fields else project.code
    display = {
        "操作": "ERP 项目模具人工交接",
        "Agent 项目": project.code + " · " + project_name,
        "项目版本": project.row_version,
        "ERP 项目号": data.erp_project_code,
        "ERP 项目来源引用": _project_source_ref(data),
        "项目映射": "已确认" if mapping else "本次确认后建立",
        "ERP 模具号": data.erp_mold_code,
        "ERP 来源引用": data.erp_source_ref,
        "Agent 内部模具号": data.internal_number,
        "Agent 模具名称": data.mold_name,
        "ERP 候选关键事实": {
            key: candidate.get(key)
            for key in (
                "project_code",
                "mold_code",
                "customer",
                "due",
                "part_count",
                "order_count",
                "overall_progress",
            )
            if candidate.get(key) is not None
        },
        "交接依据": data.evidence,
        "确认后效果": (
            "仅在 Agent 建立项目 ERP 映射、Mold 与 ProjectMold 关联并保存 ERP 来源审计；"
            "不提交 Agent BPM、不回写 ERP，不代表正式开工或基线计划已生效。"
        ),
        "说明": (
            "本人确认后只在 Agent 建立 Mold 与 ProjectMold 关联并保存 ERP "
            "来源快照；不创建、修改或回写 ERP 模具、项目或执行进度。"
        ),
    }
    return project, evidence, candidate, display


def execute_tool(db, user, key, arguments, run=None):
    if key != "prepare_project_mold_handoff":
        raise DomainError("TOOL_UNKNOWN", "工具未实现", 403)
    data = parse(arguments)
    _, _, _, display = preview(db, user, data)
    from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy

    proposal = {
        "kind": "project_mold_handoff",
        "action": "mold_handoff",
        "requires_approval": False,
        "execution_contract": {
            "confirmation_required": True,
            "post_confirmation_effect": "AGENT_LOCAL_PROJECT_ERP_MAPPING_AND_MOLD_ASSOCIATION",
            "agent_writes": [
                "ProjectERPMapping",
                "Mold",
                "ProjectMold",
                "ERP 来源审计",
            ],
            "submits_agent_bpm": False,
            "writes_erp": False,
            "user_facing_summary": (
                "本人确认后只在 Agent 建立项目 ERP 映射、Mold 与 ProjectMold 关联并保存 ERP 来源审计；"
                "不提交 Agent BPM、不回写 ERP，也不代表正式开工或基线计划已生效"
            ),
            "forbidden_summary_terms": [
                "提交 Agent BPM",
                "提交审批",
                "审批已生效",
                "审批生效",
            ],
        },
        "input": data.model_dump(mode="json"),
        "display": display,
        "confirmation_policy": proposal_confirmation_policy(
            run, requires_approval=False
        ),
    }
    return {
        "data": [],
        "source": "agent_proposal",
        "as_of": now().isoformat(),
        "proposal": proposal,
        "limitations": [
            "仅准备 ERP 项目模具人工交接建议；本人确认后才建立 Agent 本地关联，不回写 ERP。",
        ],
    }


def source(db, user, step_id, *, for_read=False):
    from domain_packs.mold.tool_gateway import available_tools

    step = db.get(m.Step, step_id)
    run = db.get(m.Run, step.run_id) if step else None
    if not run or run.user_id != user.id:
        raise DomainError("NOT_FOUND", "操作建议不存在或无权访问", 404)
    if run.status not in {"RUNNING", "RUNNING_SCOPED", "SUCCEEDED"}:
        resolved = for_read and db.scalar(select(m.HumanIntent.id).where(
            m.HumanIntent.user_id == user.id,
            m.HumanIntent.action == "mold_handoff.execute",
            m.HumanIntent.resource_id == step_id,
            m.HumanIntent.receipt.is_not(None),
        ).limit(1))
        if not resolved:
            raise DomainError("PROPOSAL_STOPPED", "任务已停止，请重新准备操作", 409)
    if (
        run.security_version != user.security_version
        or run.checkpoint.get("authorization_hash") != fingerprint(db, user)
    ):
        raise DomainError("AUTHORIZATION_CHANGED", "授权已变化，请重新准备操作", 403)
    proposal = step.result.get("proposal")
    if (
        step.tool not in available_tools(db, user)
        or step.tool != "prepare_project_mold_handoff"
        or not proposal
    ):
        raise DomainError("TOOL_FORBIDDEN", "操作能力不可用", 403)
    return proposal


def validate_intent(db, user, payload):
    proposal = source(db, user, payload["step_id"])
    if content_hash(proposal) != payload["proposal_hash"]:
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    data = parse(proposal["input"])
    _, _, _, display = preview(db, user, data)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError(
            "VERSION_CONFLICT",
            "项目或 ERP 模具候选已变化，请重新查询后准备交接",
            409,
        )
    return proposal, data


def confirm(db, user, payload):
    proposal, data = validate_intent(db, user, payload)
    project, evidence, candidate, _ = preview(db, user, data)
    _existing_conflict(db, project, data)
    source_as_of = evidence.get("as_of")
    try:
        source_as_of = datetime.fromisoformat(
            str(source_as_of).replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as error:
        raise DomainError(
            "MOLD_HANDOFF_SOURCE_TIME_INVALID",
            "ERP 读取时点格式无效，不能保存来源快照",
            409,
        ) from error
    mapping = db.scalar(
        select(m.ProjectERPMapping).where(
            m.ProjectERPMapping.project_id == project.id,
            m.ProjectERPMapping.status == "CONFIRMED",
        )
    )
    if not mapping:
        mapping = m.ProjectERPMapping(
            project_id=project.id,
            erp_project_code=data.erp_project_code,
            source_ref=_project_source_ref(data),
            source_as_of=source_as_of,
            evidence=data.evidence,
            confirmed_by=user.id,
            confirmed_at=now(),
            status="CONFIRMED",
        )
        db.add(mapping)
        db.flush()
    mold = m.Mold(
        internal_number=data.internal_number,
        name=data.mold_name,
        status="ACTIVE",
        source_system="ERP",
        source_ref=data.erp_source_ref,
        source_as_of=source_as_of,
    )
    db.add(mold)
    db.flush()
    link = m.ProjectMold(project_id=project.id, mold_id=mold.id)
    db.add(link)
    db.flush()
    record(
        db,
        user,
        "project.mold.handoff.confirmed",
        project.id,
        {
            "mold_id": mold.id,
            "project_erp_mapping_id": mapping.id,
            "project_code": project.code,
            "erp_project_code": data.erp_project_code,
            "erp_mold_code": data.erp_mold_code,
            "source_system": "ERP",
            "source_ref": data.erp_source_ref,
            "source_as_of": source_as_of.isoformat(),
            "erp_snapshot": candidate,
            "evidence": data.evidence,
        },
    )
    return {
        "project_id": project.id,
        "project_code": project.code,
        "mold_id": mold.id,
        "internal_number": mold.internal_number,
        "project_mold_id": link.id,
        "project_erp_mapping_id": mapping.id,
        "erp_project_code": data.erp_project_code,
        "source_system": mold.source_system,
        "source_ref": mold.source_ref,
        "source_as_of": source_as_of.isoformat(),
        "action": "mold_handoff",
        "status": "CONFIRMED",
        "erp_write": False,
    }


router = APIRouter()


@router.get("/api/project-mold-handoff-proposals/{step_id}")
def proposal_status(step_id: str, user=Depends(current_user), db=Depends(get_db)):
    proposal = source(db, user, step_id, for_read=True)
    intent = db.scalar(select(m.HumanIntent).where(
        m.HumanIntent.user_id == user.id,
        m.HumanIntent.action == "mold_handoff.execute",
        m.HumanIntent.resource_id == step_id,
        m.HumanIntent.receipt.is_not(None),
    ).order_by(m.HumanIntent.created_at.desc()))
    return {"status": "CONFIRMED" if intent else "PENDING",
            "receipt": intent.receipt if intent else None,
            "proposal": proposal if not intent else None}
