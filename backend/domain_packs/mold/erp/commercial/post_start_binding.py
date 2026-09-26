"""项目部最终决定后的合同和核算清单绑定门禁。"""

from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.authorization import require
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.events import record


ACCEPTED_DECISIONS = {"PROJECT_ACCEPTED", "FULL_OUTSOURCE_ACCEPTED"}
TARGET_TYPES = {"SALES_CONTRACT"}


def validate_target_reference(target, *, target_type, project_id, target_version):
    if target_type != "SALES_CONTRACT" or not isinstance(target, dict):
        raise DomainError("BINDING_TARGET_INVALID", "正式绑定对象不是销售合同")
    if target.get("kind") not in {"sales_contract", "full_outsource_contract"}:
        raise DomainError("BINDING_TARGET_INVALID", "目标业务事实不是合同")
    if target.get("project_id") != project_id:
        raise DomainError("BINDING_PROJECT_CONFLICT", "合同不属于开工通知项目", 409)
    if target.get("revision") != target_version:
        raise DomainError("VERSION_CONFLICT", "合同版本已变化，请重新核对", 409)


def require_binding_decision(decision, *, notice_version, target_version):
    """绑定必须引用生效决定及双方当前版本。"""
    if not isinstance(decision, dict) or decision.get("decision") not in ACCEPTED_DECISIONS:
        raise DomainError("START_DECISION_REQUIRED", "项目部尚未作出允许正式关联的最终决定", 409)
    if decision.get("start_notice_version") != notice_version:
        raise DomainError("VERSION_CONFLICT", "开工通知版本已变化，请重新核对", 409)
    if target_version < 1:
        raise DomainError("TARGET_VERSION_INVALID", "待绑定对象版本无效")


def bind_post_start(db, user, *, decision_id, start_notice_id, target_type,
                    target_id, target_version, mold_rows, operation_id):
    decision = db.scalar(select(m.ProjectStartDecision).where(
        m.ProjectStartDecision.id == str(decision_id),
    ).with_for_update())
    notice = db.get(m.StartNotice, str(start_notice_id))
    if not decision or not notice:
        raise DomainError("NOT_FOUND", "项目决定或开工通知不存在", 404)
    if decision.start_notice_id != notice.id or decision.start_notice_version != notice.version:
        raise DomainError("VERSION_CONFLICT", "项目决定与开工通知版本不一致", 409)
    # 幂等回放也必须先通过项目读取权限，不能凭历史操作号越权读取绑定结果。
    require(db, user, "project.read", {"project_id": notice.project_id})
    existing_event = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == "post_start.binding",
        m.AuditEvent.resource_id == str(target_id),
        m.AuditEvent.detail["operation_id"].as_string() == operation_id,
    ).limit(1))
    if existing_event:
        return dict(existing_event.detail or {})
    require_binding_decision(
        {"decision": decision.decision, "start_notice_version": decision.start_notice_version},
        notice_version=notice.version, target_version=target_version,
    )
    target = db.get(m.BusinessSubject, str(target_id))
    target_view = (
        {"kind": target.kind, "project_id": target.project_id, "revision": target.revision}
        if target else target
    )
    validate_target_reference(
        target_view, target_type=target_type, project_id=notice.project_id,
        target_version=target_version,
    )
    valid_mold_ids = set(db.scalars(select(m.ProjectMold.mold_id).where(
        m.ProjectMold.project_id == notice.project_id,
    )))
    if not mold_rows or any(row.get("mold_id") not in valid_mold_ids for row in mold_rows):
        raise DomainError("MOLD_BINDING_REQUIRED", "合同绑定必须引用当前项目已确认的内部模具", 409)
    snapshot = build_binding_snapshot(
        decision_id=decision.id, start_notice_id=notice.id,
        start_notice_version=notice.version, target_type=target_type,
        target_id=target_id, target_version=target_version, mold_rows=mold_rows,
    )
    target_fingerprint = None
    duplicate = db.scalar(select(m.PostStartBinding).where(
        m.PostStartBinding.target_type == target_type,
        m.PostStartBinding.target_id == str(target_id),
        m.PostStartBinding.target_version == target_version,
    ))
    if duplicate:
        raise DomainError("BINDING_ALREADY_EXISTS", "该合同版本已经正式绑定", 409)
    row = m.PostStartBinding(
        project_decision_id=decision.id, start_notice_id=notice.id,
        start_notice_version=notice.version, target_type=target_type,
        target_id=str(target_id), target_version=target_version,
        target_fingerprint=target_fingerprint,
        mold_snapshot=snapshot["mold_rows"], bound_by=user.id, bound_at=now(),
    )
    db.add(row)
    db.flush()
    detail = {**snapshot, "target_fingerprint": target_fingerprint,
              "binding_id": row.id, "operation_id": operation_id}
    record(db, user, "post_start.binding", str(target_id), detail)
    db.flush()
    return detail


def build_binding_snapshot(*, decision_id, start_notice_id, start_notice_version,
                           target_type, target_id, target_version, mold_rows):
    if target_type not in TARGET_TYPES:
        raise DomainError("BINDING_TARGET_INVALID", "正式绑定对象类型不受支持")
    if not target_id or not decision_id or not start_notice_id:
        raise DomainError("BINDING_REFERENCE_REQUIRED", "正式绑定缺少稳定对象引用")
    rows = []
    for row in mold_rows or []:
        if not isinstance(row, dict) or not row.get("mold_id") or not row.get("line_no"):
            raise DomainError("MOLD_BINDING_REQUIRED", "正式绑定的模具行缺少稳定模具引用")
        rows.append({"mold_id": row["mold_id"], "line_no": row["line_no"]})
    return {
        "decision_id": decision_id,
        "start_notice_id": start_notice_id,
        "start_notice_version": start_notice_version,
        "target_type": target_type,
        "target_id": target_id,
        "target_version": target_version,
        "mold_rows": rows,
    }
