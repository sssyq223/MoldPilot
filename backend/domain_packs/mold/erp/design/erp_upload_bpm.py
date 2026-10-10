"""MoldPilot BPM envelope for ERP design-upload sessions.

The ERP upload parser creates a temporary ERP session so that MoldPilot can
show the authoritative preview.  The ERP purchase request is created only by
the final MoldPilot approval decision.  This module owns the small local
approval envelope and keeps the ERP payload immutable inside its evidence
snapshot.
"""
from __future__ import annotations

from datetime import datetime, timezone
import secrets

from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.erp.core import business
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.events import record


UPLOAD_PROJECT_CODE = "ERP-DESIGN-UPLOAD"


def _active_user(db, user_id: str | None):
    user = db.get(m.User, user_id) if user_id else None
    if not user or not user.active:
        return None
    return user


def ensure_project(db, actor, project_id: str | None = None):
    """Return the requested project or a stable integration project.

    A caller may pass a real MoldPilot project.  The fallback is deliberately
    deterministic so a design-upload session without a project can still be
    routed through BPM; its role members are the authenticated operator until
    an administrator replaces them with the migrated design and purchasing
    personnel.
    """
    project = db.get(m.Project, project_id) if project_id else None
    if project_id and not project:
        raise DomainError("PROJECT_NOT_FOUND", "指定的 MoldPilot 项目不存在", 404)
    if project and project.status in {"CLOSED", "TERMINATED"}:
        raise DomainError("PROJECT_BLOCKED", "指定的 MoldPilot 项目已关闭，不能提交设计上传审批", 409)
    if project:
        return project

    project = db.scalar(select(m.Project).where(m.Project.code == UPLOAD_PROJECT_CODE))
    if not project:
        design_owner = db.scalar(select(m.User).where(
            m.User.active.is_(True), m.User.department == "设计部"
        ).order_by(m.User.display_name, m.User.id)) or actor
        purchase_owner = db.scalar(select(m.User).where(
            m.User.active.is_(True), m.User.department == "采购部"
        ).order_by(m.User.display_name, m.User.id)) or actor
        project_owner = db.scalar(select(m.User).where(
            m.User.active.is_(True), m.User.department == "管理部门"
        ).order_by(m.User.display_name, m.User.id)) or actor
        project = m.Project(code=UPLOAD_PROJECT_CODE, name="ERP设计上传联动项目", status="ACTIVE")
        db.add(project)
        db.flush()
        db.add(m.ProjectProfile(project_id=project.id, owner_user_id=project_owner.id))
        db.add(m.ProjectRoleConfig(project_id=project.id, version=1))
        for role_key, member in (("PROJECT_OWNER", project_owner),
                                 ("DESIGN_OWNER", design_owner),
                                 ("PURCHASE_OWNER", purchase_owner)):
            db.add(m.ProjectRoleMember(project_id=project.id, role_key=role_key, user_id=member.id))
        db.flush()
    return project


def _role_user(db, project_id: str, role_key: str, fallback):
    member = db.scalar(select(m.ProjectRoleMember).where(
        m.ProjectRoleMember.project_id == project_id,
        m.ProjectRoleMember.role_key == role_key,
    ).order_by(m.ProjectRoleMember.created_at, m.ProjectRoleMember.id))
    return _active_user(db, member.user_id if member else None) or fallback


def submit(db, actor, *, session_id: int, sheet_type: str, mold_code: str | None,
           preview_rows: list[dict], design_order_type: str, expected_date: str,
           purchase_reason: str | None, remark: str | None, urgency_level: str,
           allow_duplicate: bool, project_id: str | None = None,
           file_ids: list[str] | None = None):
    project = ensure_project(db, actor, project_id)
    design_type = "MOLD_CHANGE" if design_order_type == "repair_other" else "NEW_MOLD"
    process_key = "design_modify_model_approval" if design_type == "MOLD_CHANGE" else "design_new_model_approval"
    definition = db.scalar(select(m.WorkflowDefinition).where(
        m.WorkflowDefinition.process_key == process_key,
        m.WorkflowDefinition.status == "PUBLISHED",
    ).order_by(m.WorkflowDefinition.version.desc()))
    if not definition:
        raise DomainError("WORKFLOW_NOT_CONFIGURED", f"MoldPilot 未发布 {process_key} 审批模板", 409)
    reviewer = _role_user(db, project.id, "DESIGN_OWNER", actor)
    payload = {
        "integration_type": "erp_design_upload",
        "session_id": int(session_id),
        "sheet_type": sheet_type,
        "mold_code": mold_code,
        "preview_rows": preview_rows,
        "design_order_type": design_order_type,
        "expected_date": expected_date,
        "purchase_reason": purchase_reason,
        "remark": remark,
        "urgency_level": urgency_level,
        "allow_duplicate": bool(allow_duplicate),
        "project_id": project.id,
        "submitted_by": actor.id,
    }
    subject = m.BusinessSubject(
        kind="design_route",
        number="ERP-DESIGN-" + secrets.token_hex(5).upper(),
        project_id=project.id,
        created_by=actor.id,
        remark=(remark or purchase_reason or "ERP设计上传审批").strip(),
    )
    db.add(subject)
    db.flush()
    db.add(m.DesignDetail(
        subject_id=subject.id,
        design_type=design_type,
        drawing_revision=f"ERP_UPLOAD_SESSION_{session_id}",
        drawing_evidence=(remark or purchase_reason or "ERP设计清单上传").strip(),
        reviewer_id=reviewer.id,
        source_system="management-system",
        source_resource_type="design_upload_session",
        source_resource_id=str(session_id),
        source_resource_version=str(session_id),
        source_as_of=datetime.now(timezone.utc),
        source_summary={
            "session_id": int(session_id), "sheet_type": sheet_type,
            "mold_code": mold_code, "row_count": len(preview_rows),
            "expected_date": expected_date, "design_order_type": design_order_type,
        },
        source_snapshot=payload,
    ))
    db.flush()
    # Preserve the originating chat attachment as immutable approval evidence.
    # The tool gateway supplies only files bound to the current run; direct
    # callers may omit file_ids when no chat attachment is available.
    if file_ids:
        from domain_packs.mold.erp.design import design_documents
        from domain_packs.mold.ports.files import uploaded_file
        blobs = [uploaded_file(db, actor, file_id) for file_id in file_ids]
        design_documents.link_initial(db, actor, subject, blobs)
    submitted = business.submit_subject(db, actor, subject.id, subject.revision, definition.id)
    record(db, actor, "erp_design_upload.approval_submitted", str(session_id), {
        "subject_id": subject.id, "instance_id": submitted["instance_id"],
        "project_id": project.id, "process_key": process_key,
        "design_type": design_type,
    }, recipients=[reviewer.id] if reviewer.id != actor.id else None)
    return {
        "status": "AWAITING_APPROVAL",
        "sessionId": int(session_id),
        "subjectId": subject.id,
        "instanceId": submitted["instance_id"],
        "processKey": process_key,
        "processName": definition.name,
        "currentNode": definition.config["nodes"][0]["name"],
        "message": f"已提交 MoldPilot 审批：{definition.name}。审批完成后才写入 ERP。",
    }


def source_payload(detail: m.DesignDetail):
    value = detail.source_snapshot if isinstance(detail.source_snapshot, dict) else {}
    return value if value.get("integration_type") == "erp_design_upload" else None
