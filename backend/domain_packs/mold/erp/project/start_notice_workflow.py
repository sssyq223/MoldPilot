"""正式开工通知的版本、部门回执和项目部决定门禁。"""

from sqlalchemy import select

from agent_core.host_ports import host_ports
from domain_packs.mold import models as m
from domain_packs.mold.erp.commercial.bid_start_workflow import (
    require_intake_confirmed,
    summarize_department_gate,
)
from domain_packs.mold.authorization import require
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.events import record


REQUIRED_DEPARTMENTS = (
    "DESIGN",
    "PURCHASE",
    "MANUFACTURING",
    "ASSEMBLY",
    "FINANCE",
)


def legacy_internal_start_reference(subject):
    """Expose the old start fact for compatibility, never as a new-flow gate."""
    if not subject:
        return None
    return {
        "subject_id": subject.id,
        "status": subject.status,
        "revision": subject.revision,
        "compatibility_only": True,
    }


def require_start_prerequisites(start_conditions, has_quote_acceptance):
    if not start_conditions.get("complete"):
        raise DomainError(
            "START_CONDITIONS_MISSING",
            "客户正式开工条件尚不完整：" + "；".join(start_conditions.get("blockers") or []),
            409,
        )
    if not has_quote_acceptance:
        raise DomainError("QUOTE_ACCEPTANCE_REQUIRED", "正式开工前必须存在有效报价承接决定", 409)


def validate_start_notice_creation(match, *, project_version):
    from domain_packs.mold.erp.commercial.bid_start_workflow import _value
    if _value(match, "status") != "INTAKE_CONFIRMED":
        raise DomainError("BID_INTAKE_CONFIRMATION_REQUIRED", "中标接收版本尚未人工确认", 409)
    require_intake_confirmed(match)
    if _value(match, "project_version") != project_version:
        raise DomainError("VERSION_CONFLICT", "项目资料已变化，请重新生成开工通知", 409)


def create_start_notice(db, user, match_id, *, expected_row_version, effective_date,
                        expected_contract_date=None, operation_id):
    """从已确认中标接收版本创建一个可供部门承接的 DRAFT。"""

    match = db.scalar(select(m.BidNoticeMatch).where(
        m.BidNoticeMatch.id == str(match_id),
    ).with_for_update())
    if not match:
        raise DomainError("NOT_FOUND", "中标项目匹配记录不存在", 404)
    host_ports().uploaded_file(db, user, match.source_file_id)
    # 幂等回放也必须先通过项目读取权限，不能凭历史操作号越权读取开工通知。
    if not match.project_id:
        raise DomainError("PROJECT_REQUIRED", "中标通知尚未匹配项目", 409)
    require(db, user, "project.read", {"project_id": match.project_id})
    existing_event = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == "start_notice.created",
        m.AuditEvent.resource_id == match.id,
        m.AuditEvent.detail["operation_id"].as_string() == operation_id,
    ).limit(1))
    if existing_event:
        return dict(existing_event.detail or {})
    if match.row_version != expected_row_version:
        raise DomainError("STALE_VERSION", "中标项目匹配记录已变化，请重新读取", 409)
    validate_start_notice_creation(match, project_version=match.project_version)
    project = db.get(m.Project, match.project_id)
    if not project:
        raise DomainError("PROJECT_NOT_FOUND", "项目不存在", 404)
    require(db, user, "project.read", {"project_id": project.id})
    if project.row_version != match.project_version:
        raise DomainError("VERSION_CONFLICT", "项目资料已变化，请重新匹配中标通知", 409)
    from domain_packs.mold.tools.erp.commercial.bid_intake_tools import start_condition_snapshot
    start_conditions = start_condition_snapshot(db, user, project.id)
    if start_conditions.get("current_revision_id") != match.bid_intake_revision_id:
        raise DomainError("BID_INTAKE_VERSION_CONFLICT", "中标接收资料已变化，请重新查询当前版本", 409)
    acceptance = db.scalar(select(m.BusinessSubject).join(
        m.BusinessDecisionDetail, m.BusinessDecisionDetail.subject_id == m.BusinessSubject.id,
    ).where(
        m.BusinessSubject.project_id == project.id,
        m.BusinessSubject.kind == "quote_acceptance",
        m.BusinessSubject.status == "EFFECTIVE",
        m.BusinessDecisionDetail.decision == "ACCEPT",
    ).order_by(m.BusinessSubject.created_at.desc(), m.BusinessSubject.id).limit(1))
    require_start_prerequisites(start_conditions, bool(acceptance))
    acceptance_detail = db.get(m.BusinessDecisionDetail, acceptance.id)
    from domain_packs.mold.erp.project import start_materials
    material = start_materials.build(
        db, project, match.bid_intake_revision_id, effective_date,
        expected_contract_date, contract_visibility=True,
    )
    profile = db.get(m.ProjectProfile, project.id)
    legacy_subject = db.scalar(select(m.BusinessSubject).where(
        m.BusinessSubject.project_id == project.id,
        m.BusinessSubject.kind == "internal_start",
        m.BusinessSubject.status == "EFFECTIVE",
    ).order_by(m.BusinessSubject.created_at.desc(), m.BusinessSubject.id).limit(1))
    mold_ids = list(db.scalars(select(m.ProjectMold.mold_id).where(
        m.ProjectMold.project_id == project.id,
    ).order_by(m.ProjectMold.mold_id)))
    snapshot = build_material_snapshot({
        "project_id": project.id,
        "project_version": project.row_version,
        "bid_intake_revision_id": match.bid_intake_revision_id,
        "customer_id": profile.customer_id if profile else None,
        "mold_ids": mold_ids,
    })
    snapshot["effective_date"] = effective_date.isoformat()
    snapshot["expected_contract_date"] = expected_contract_date.isoformat() if expected_contract_date else None
    snapshot["execution_mode"] = acceptance_detail.execution_mode if acceptance_detail else None
    snapshot["quote_acceptance_subject_id"] = acceptance.id
    snapshot["legacy_internal_start"] = legacy_internal_start_reference(legacy_subject)
    snapshot["start_material"] = material
    latest = db.scalar(select(m.StartNotice).where(
        m.StartNotice.bid_match_id == match.id,
    ).order_by(m.StartNotice.version.desc()).limit(1))
    version = latest.version + 1 if latest else 1
    notice = m.StartNotice(
        bid_match_id=match.id,
        project_id=project.id,
        bid_intake_revision_id=match.bid_intake_revision_id,
        version=version,
        status="DRAFT",
        material_snapshot=snapshot,
        created_by=user.id,
    )
    db.add(notice)
    db.flush()
    labels = {
        "DESIGN": "设计", "PURCHASE": "采购", "MANUFACTURING": "生产制造",
        "ASSEMBLY": "装配", "FINANCE": "财务",
    }
    role_keys = {
        "DESIGN": "DESIGN_OWNER", "PURCHASE": "PURCHASE_OWNER",
        "MANUFACTURING": "MANUFACTURING_OWNER", "ASSEMBLY": "ASSEMBLY_OWNER",
        "FINANCE": "FINANCE_OWNER",
    }
    for department_key in REQUIRED_DEPARTMENTS:
        recipients = [{
            "user_id": person.id,
            "name": person.display_name,
            "department": person.department or "未设置部门",
        } for person, _ in db.execute(
            select(m.User, m.ProjectRoleMember).join(
                m.ProjectRoleMember, m.ProjectRoleMember.user_id == m.User.id,
            ).where(
                m.ProjectRoleMember.project_id == project.id,
                m.ProjectRoleMember.role_key == role_keys[department_key],
                m.User.active.is_(True),
            ).order_by(m.User.display_name, m.User.id)
        )]
        event = None
        if recipients:
            event = record(
                db, user, "start_notice.department_handoff", notice.id,
                {
                    "start_notice_id": notice.id,
                    "start_notice_version": notice.version,
                    "project_id": project.id,
                    "department_key": department_key,
                    "department_name": labels[department_key],
                    "recipient_snapshot": recipients,
                },
                [recipient["user_id"] for recipient in recipients],
            )
            db.flush()
        db.add(m.StartNoticeDepartmentAck(
            start_notice_id=notice.id,
            start_notice_version=notice.version,
            department_key=department_key,
            department_name=labels[department_key],
            status="PENDING",
            handoff_status="QUEUED" if event else "UNASSIGNED",
            recipient_snapshot=recipients,
            event_id=event.id if event else None,
            row_version=1,
        ))
    detail = {
        "start_notice_id": notice.id,
        "bid_match_id": match.id,
        "project_id": project.id,
        "project_version": project.row_version,
        "version": notice.version,
        "status": notice.status,
        "effective_date": effective_date.isoformat(),
        "operation_id": operation_id,
    }
    record(db, user, "start_notice.created", match.id, detail)
    db.flush()
    return detail


def validate_department_ack(ack, *, expected_row_version, status, evidence):
    from domain_packs.mold.erp.commercial.bid_start_workflow import _value
    if _value(ack, "row_version") != expected_row_version:
        raise DomainError("STALE_VERSION", "部门回执已变化，请重新读取", 409)
    if status not in {"ACCEPTED", "RETURNED", "NEED_INFO"}:
        raise DomainError("DEPARTMENT_STATUS_INVALID", "部门回执状态不受支持")
    if not isinstance(evidence, str) or not evidence.strip():
        raise DomainError("DEPARTMENT_EVIDENCE_REQUIRED", "部门回执必须填写核对依据")


def record_department_ack(db, user, notice_id, *, department_key, expected_row_version,
                          status, evidence, operation_id):
    """记录一个部门自己的回执；通知送达不改变这里的业务状态。"""
    notice = db.scalar(select(m.StartNotice).where(
        m.StartNotice.id == str(notice_id),
    ).with_for_update())
    if not notice:
        raise DomainError("NOT_FOUND", "开工通知不存在", 404)
    host_match = db.get(m.BidNoticeMatch, notice.bid_match_id)
    if not host_match:
        raise DomainError("SOURCE_MISSING", "开工通知缺少中标来源", 409)
    host_ports().uploaded_file(db, user, host_match.source_file_id)
    ack = db.scalar(select(m.StartNoticeDepartmentAck).where(
        m.StartNoticeDepartmentAck.start_notice_id == notice.id,
        m.StartNoticeDepartmentAck.start_notice_version == notice.version,
        m.StartNoticeDepartmentAck.department_key == department_key,
    ).with_for_update())
    if not ack:
        raise DomainError("DEPARTMENT_NOT_REQUIRED", "当前开工通知没有该部门回执", 409)
    # 幂等回放也必须先通过部门负责人身份校验，不能凭历史操作号越权代填回执。
    if not user.super_admin:
        role_by_department = {
            "DESIGN": "DESIGN_OWNER", "PURCHASE": "PURCHASE_OWNER",
            "MANUFACTURING": "MANUFACTURING_OWNER", "ASSEMBLY": "ASSEMBLY_OWNER",
            "FINANCE": "FINANCE_OWNER",
        }
        if department_key not in role_by_department:
            raise DomainError("DEPARTMENT_STATUS_INVALID", "部门回执状态不受支持")
        allowed = db.scalar(select(m.ProjectRoleMember).where(
            m.ProjectRoleMember.project_id == notice.project_id,
            m.ProjectRoleMember.role_key == role_by_department[department_key],
            m.ProjectRoleMember.user_id == user.id,
        ))
        if not allowed:
            raise DomainError("FORBIDDEN", "当前人员不是该部门回执负责人", 403)
    existing = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == "start_notice.department_ack",
        m.AuditEvent.resource_id == ack.id,
        m.AuditEvent.detail["operation_id"].as_string() == operation_id,
    ).limit(1))
    if existing:
        return dict(existing.detail or {})
    if notice.status not in {"DRAFT", "DEPARTMENT_REVIEW"}:
        raise DomainError("START_NOTICE_CLOSED", "开工通知已经形成最终决定", 409)
    validate_department_ack(
        ack, expected_row_version=expected_row_version,
        status=status, evidence=evidence,
    )
    ack.status = status
    ack.evidence = evidence.strip()
    ack.row_version += 1
    ack.responded_by = user.id
    ack.responded_at = now()
    notice.status = "DEPARTMENT_REVIEW"
    detail = {
        "start_notice_id": notice.id,
        "start_notice_version": notice.version,
        "department_key": department_key,
        "status": status,
        "ack_row_version": ack.row_version,
        "operation_id": operation_id,
    }
    record(db, user, "start_notice.department_ack", ack.id, detail)
    db.flush()
    return detail


def decide_project_start(db, user, notice_id, *, decision, reason, operation_id):
    """由项目部作出最终决定；正式关联只接受两种 ACCEPTED 结果。"""
    notice = db.scalar(select(m.StartNotice).where(
        m.StartNotice.id == str(notice_id),
    ).with_for_update())
    if not notice:
        raise DomainError("NOT_FOUND", "开工通知不存在", 404)
    host_match = db.get(m.BidNoticeMatch, notice.bid_match_id)
    if not host_match:
        raise DomainError("SOURCE_MISSING", "开工通知缺少中标来源", 409)
    host_ports().uploaded_file(db, user, host_match.source_file_id)
    # 幂等回放也必须先通过项目负责人身份校验，不能凭历史操作号读取他人决定。
    profile = db.get(m.ProjectProfile, notice.project_id)
    if not user.super_admin and (not profile or profile.owner_user_id != user.id):
        raise DomainError("FORBIDDEN", "只有项目负责人可以作出项目部最终决定", 403)
    existing = db.scalar(select(m.ProjectStartDecision).where(
        m.ProjectStartDecision.start_notice_id == notice.id,
        m.ProjectStartDecision.start_notice_version == notice.version,
    ))
    if existing:
        return {"decision_id": existing.id, "decision": existing.decision,
                "start_notice_id": notice.id, "start_notice_version": notice.version}
    if decision not in {"PROJECT_ACCEPTED", "FULL_OUTSOURCE_ACCEPTED", "REJECTED", "RETURNED"}:
        raise DomainError("PROJECT_DECISION_INVALID", "项目部决定不受支持")
    if not isinstance(reason, str) or not reason.strip():
        raise DomainError("PROJECT_DECISION_REASON_REQUIRED", "项目部决定必须填写依据")
    acks = list(db.scalars(select(m.StartNoticeDepartmentAck).where(
        m.StartNoticeDepartmentAck.start_notice_id == notice.id,
        m.StartNoticeDepartmentAck.start_notice_version == notice.version,
    ).order_by(m.StartNoticeDepartmentAck.department_key)))
    ack_values = [{"department_key": ack.department_key, "status": ack.status,
                   "row_version": ack.row_version} for ack in acks]
    if decision in {"PROJECT_ACCEPTED", "FULL_OUTSOURCE_ACCEPTED"}:
        require_project_decision_gate(decision, ack_values)
    decision_row = m.ProjectStartDecision(
        start_notice_id=notice.id,
        start_notice_version=notice.version,
        decision=decision,
        reason=reason.strip(),
        department_snapshot=ack_values,
        decided_by=user.id,
        decided_at=now(),
    )
    db.add(decision_row)
    db.flush()
    notice.status = decision
    detail = {
        "decision_id": decision_row.id,
        "start_notice_id": notice.id,
        "start_notice_version": notice.version,
        "decision": decision,
        "operation_id": operation_id,
    }
    record(db, user, "start_notice.project_decision", decision_row.id, detail)
    db.flush()
    return detail


def build_material_snapshot(materials):
    """只保存跨对象版本引用，不把合同/PDF原文复制进开工快照。"""
    if not isinstance(materials, dict):
        return {}
    allowed = ("project_id", "project_version", "bid_intake_revision_id", "customer_id", "mold_ids")
    return {key: materials[key] for key in allowed if key in materials}


def require_project_decision_gate(decision, acks, required_keys=REQUIRED_DEPARTMENTS):
    """项目部最终决定前必须收到所有部门的 ACCEPTED 回执。"""
    if decision not in {"PROJECT_ACCEPTED", "FULL_OUTSOURCE_ACCEPTED"}:
        raise DomainError("PROJECT_DECISION_INVALID", "当前决定不是可生效的项目承接决定")
    gate = summarize_department_gate(acks, required_keys)
    if not gate["ready"]:
        raise DomainError(
            "DEPARTMENT_ACK_REQUIRED",
            "所有必要部门必须独立确认后才能形成项目部最终决定",
            409,
        )
    return None
