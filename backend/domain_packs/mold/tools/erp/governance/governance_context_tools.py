"""Read-only governance evidence for permissions, audit, notifications, files and ERP source state."""
from collections import defaultdict
from pydantic import Field, model_validator
from sqlalchemy import select, or_
from fastapi.encoders import jsonable_encoder

from domain_packs.mold import models as m
from domain_packs.mold.authorization import PERMISSIONS, access, predicate, fingerprint
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel


class GovernanceContextInput(StrictModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    identifier: str | None = Field(default=None, min_length=1, max_length=200, description="项目编号/名称、模具号、联络单号或业务引用。")
    target_user_id: str | None = Field(default=None, min_length=1, max_length=36, description="可选；审计某个用户在该项目上的有效授权。默认审计当前用户。")

    @model_validator(mode="after")
    def one_locator(self):
        if bool(self.project_id) == bool(self.identifier):
            raise ValueError("项目标识和业务编号须且只能填写一项")
        if self.identifier:
            self.identifier = self.identifier.strip()
            if not self.identifier:
                raise ValueError("业务编号不能为空")
        return self


def _strength(value, needle):
    if value is None:
        return 0
    value = str(value).casefold()
    needle = str(needle).casefold()
    return 100 if value == needle else 50 if needle in value else 0


def _project_card(db, viewer, project, matched_by=()):
    fields = access(db, viewer, "project.read", {"project_id": project.id}).fields
    data = {"id": project.id, "code": project.code, "name": project.name, "status": project.status}
    if "*" not in fields:
        data = {k: v for k, v in data.items() if k in fields}
    data["matched_by"] = sorted(set(matched_by))
    return data


def _resolve_project(db, viewer, data: GovernanceContextInput):
    visible = list(
        db.scalars(
            select(m.Project)
            .where(predicate(db, viewer, "project.read", {"project_id": m.Project.id}))
            .order_by(m.Project.code)
            .limit(501)
        )
    )
    truncated = len(visible) > 500
    visible = visible[:500]
    by_id = {project.id: project for project in visible}
    if data.project_id:
        project = by_id.get(data.project_id)
        return project, ([] if project else None), truncated

    scores = defaultdict(int)
    reasons = defaultdict(list)

    def add(project_id, value, label):
        if project_id not in by_id:
            return
        score = _strength(value, data.identifier)
        if score:
            scores[project_id] = max(scores[project_id], score)
            reasons[project_id].append(label)

    for project in visible:
        add(project.id, project.id, "项目ID")
        add(project.id, project.code, "项目编号")
        add(project.id, project.name, "项目名称")
    if by_id:
        for link, mold in db.execute(
            select(m.ProjectMold, m.Mold).join(m.Mold, m.Mold.id == m.ProjectMold.mold_id).where(m.ProjectMold.project_id.in_(list(by_id)))
        ):
            add(link.project_id, mold.internal_number, "模具号")
            add(link.project_id, mold.name, "模具名称")
        from domain_packs.mold.erp.change.contacts import permitted

        for case in db.scalars(select(m.ContactCase).where(m.ContactCase.project_id.in_(list(by_id))).limit(501)):
            if not permitted(db, viewer, "read", case):
                continue
            for value, label in (
                (case.id, "联络单ID"),
                (case.title, "联络标题"),
                (case.customer_ref, "客户引用"),
                (case.mold_number, "联络模具号"),
                (case.product_ref, "产品/料品号"),
            ):
                add(case.project_id, value, label)
        for subject in db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id.in_(list(by_id))).limit(501)):
            from domain_packs.mold.erp.core.domains import authorize

            try:
                authorize(db, viewer, subject, "read")
            except DomainError:
                continue
            add(subject.project_id, subject.id, "业务记录ID")
            add(subject.project_id, subject.number, "业务单号")

    if not scores:
        return None, [], truncated
    best = max(scores.values())
    ids = [project_id for project_id, score in scores.items() if score == best]
    if len(ids) != 1:
        return None, [_project_card(db, viewer, by_id[project_id], reasons[project_id]) for project_id in ids[:20]], truncated
    return by_id[ids[0]], reasons[ids[0]], truncated


def _target_user(db, viewer, target_user_id):
    if not target_user_id:
        return viewer
    target = db.get(m.User, target_user_id)
    if not target:
        raise DomainError("NOT_FOUND", "目标用户不存在", 404)
    return target


def _active_grants(db, user):
    current = now()
    return list(
        db.scalars(
            select(m.Grant)
            .where(
                m.Grant.user_id == user.id,
                m.Grant.active.is_(True),
                or_(m.Grant.valid_from.is_(None), m.Grant.valid_from <= current),
                or_(m.Grant.valid_to.is_(None), m.Grant.valid_to > current),
            )
            .order_by(m.Grant.permission, m.Grant.id)
        )
    )


def _grant_summary(db, user):
    grants = _active_grants(db, user)
    capabilities = list(
        db.scalars(select(m.Capability).where(m.Capability.user_id == user.id, m.Capability.enabled.is_(True)).order_by(m.Capability.kind, m.Capability.key))
    )
    return {
        "user": {"id": user.id, "username": user.username, "display_name": user.display_name, "active": user.active, "super_admin": user.super_admin, "security_version": user.security_version},
        "authorization_hash": fingerprint(db, user),
        "active_grants": [
            {
                "id": grant.id,
                "permission": grant.permission,
                "effect": grant.effect,
                "scope": grant.scope,
                "fields": sorted(grant.fields),
                "reason": grant.reason,
                "granted_by": grant.granted_by,
                "valid_from": grant.valid_from,
                "valid_to": grant.valid_to,
            }
            for grant in grants
        ],
        "enabled_capabilities": [{"kind": item.kind, "key": item.key} for item in capabilities],
    }


def _project_categories(db, project_id):
    categories = set(
        value
        for value, in db.execute(select(m.BusinessSubject.category).where(m.BusinessSubject.project_id == project_id, m.BusinessSubject.category.is_not(None)).distinct())
        if value
    )
    categories.update(
        value
        for value, in db.execute(select(m.ContactCase.category).where(m.ContactCase.project_id == project_id, m.ContactCase.category.is_not(None)).distinct())
        if value
    )
    return sorted(categories)


def _permission_matrix(db, user, project_id):
    read_permissions = sorted(permission for permission in PERMISSIONS if permission.endswith(".read") or permission in {"file.upload", "audit.read"})
    categories = _project_categories(db, project_id)
    rows = []
    for permission in read_permissions:
        base_obj = {} if permission == "file.upload" else {"project_id": project_id}
        base = access(db, user, permission, base_obj)
        category_rows = []
        if categories and permission != "file.upload":
            for category in categories:
                scoped = access(db, user, permission, {"project_id": project_id, "category": category})
                if scoped.allowed:
                    category_rows.append({"category": category, "allowed": True, "fields": sorted(scoped.fields)})
        rows.append({"permission": permission, "allowed": base.allowed, "fields": sorted(base.fields), "category_scopes": category_rows})
    return rows


def _visible_business_resources(db, viewer, project_id):
    from domain_packs.mold.erp.change.contacts import permitted
    from domain_packs.mold.erp.core.domains import authorize

    resource_ids = {project_id}
    subjects = []
    for subject in db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id == project_id).order_by(m.BusinessSubject.created_at.desc()).limit(200)):
        try:
            authorize(db, viewer, subject, "read")
        except DomainError:
            continue
        resource_ids.add(subject.id)
        subjects.append({"id": subject.id, "kind": subject.kind, "number": subject.number, "status": subject.status, "category": subject.category})
    cases = []
    attachments = []
    for case in db.scalars(select(m.ContactCase).where(m.ContactCase.project_id == project_id).order_by(m.ContactCase.created_at.desc()).limit(100)):
        if not permitted(db, viewer, "read", case):
            continue
        resource_ids.add(case.id)
        cases.append({"id": case.id, "title": case.title, "category": case.category, "revision": case.revision, "status": "CLOSED" if case.closed_at else "OPEN"})
        rows = list(
            db.execute(
                select(m.ContactAttachment, m.FileObject)
                .join(m.FileObject, m.FileObject.id == m.ContactAttachment.file_id)
                .where(m.ContactAttachment.case_id == case.id)
                .order_by(m.ContactAttachment.created_at.desc())
                .limit(50)
            )
        )
        latest = {}
        for link, _ in rows:
            latest[link.document_id] = max(latest.get(link.document_id, 0), link.version)
        for link, blob in rows:
            resource_ids.add(link.id)
            resource_ids.add(blob.id)
            uploader = db.get(m.User, blob.owner_id)
            linker = db.get(m.User, link.created_by)
            attachments.append(
                {
                    "id": link.id,
                    "case_id": case.id,
                    "document_id": link.document_id,
                    "title": link.title,
                    "version": link.version,
                    "previous_id": link.previous_id,
                    "is_current": latest[link.document_id] == link.version,
                    "linked_at": link.created_at,
                    "linked_by": linker.display_name if linker else link.created_by,
                    "file": {
                        "id": blob.id,
                        "filename": blob.filename,
                        "media_type": blob.media_type,
                        "size": blob.size,
                        "sha256": blob.sha256,
                        "uploaded_by": uploader.display_name if uploader else blob.owner_id,
                        "uploaded_at": blob.created_at,
                        "storage_backend": blob.backend,
                        "storage_versioned": bool(blob.storage_version),
                    },
                }
            )
    return resource_ids, subjects, cases, attachments


def _audit_trail(db, resource_ids):
    return [
        {"id": row.id, "action": row.action, "resource_id": row.resource_id, "user_id": row.user_id, "created_at": row.created_at, "detail": row.detail}
        for row in db.scalars(select(m.AuditEvent).where(m.AuditEvent.resource_id.in_(list(resource_ids))).order_by(m.AuditEvent.created_at.desc(), m.AuditEvent.id).limit(100))
    ]


def _notification_status(db, resource_ids):
    rows = []
    for event in db.scalars(select(m.Outbox).where(m.Outbox.resource_id.in_(list(resource_ids))).order_by(m.Outbox.created_at.desc(), m.Outbox.id).limit(100)):
        notifications = list(db.scalars(select(m.Notification).where(m.Notification.event_id == event.id).order_by(m.Notification.created_at.desc(), m.Notification.id)))
        rows.append(
            {
                "event_id": event.id,
                "kind": event.kind,
                "resource_id": event.resource_id,
                "created_at": event.created_at,
                "published_at": event.published_at,
                "attempts": event.attempts,
                "last_error": event.last_error,
                "dead_at": event.dead_at,
                "delivered": db.scalar(select(m.Inbox.id).where(m.Inbox.event_id == event.id)) is not None,
                "recipient_count": len(set(event.payload.get("recipients", []) if isinstance(event.payload, dict) else [])),
                "notification_count": len(notifications),
                "unread_count": sum(1 for item in notifications if not item.read),
            }
        )
    return rows


def _source_facts(db, project, resource_ids):
    task_sources = [
        {
            "task_id": task.id,
            "case_id": task.case_id,
            "source_system": task.source_system,
            "source_ref": task.source_ref,
            "source_as_of": task.source_as_of,
            "execution_source_system": task.execution_source_system,
            "execution_source_ref": task.execution_source_ref,
            "execution_source_as_of": task.execution_source_as_of,
        }
        for task in db.scalars(select(m.ContactTask).join(m.ContactCase, m.ContactCase.id == m.ContactTask.case_id).where(m.ContactCase.project_id == project.id).order_by(m.ContactTask.created_at.desc()).limit(100))
    ]
    closure_sources = [
        {
            "item_id": item.id,
            "case_id": item.case_id,
            "item_key": item.item_key,
            "status": item.status,
            "source_system": item.source_system,
            "source_ref": item.source_ref,
            "source_as_of": item.source_as_of,
            "revision": item.revision,
        }
        for item in db.scalars(select(m.ProjectClosureItem).join(m.ProjectClosureCase, m.ProjectClosureCase.id == m.ProjectClosureItem.case_id).where(m.ProjectClosureCase.project_id == project.id).order_by(m.ProjectClosureItem.updated_at.desc()).limit(100))
    ]
    operations = []
    for operation, intent in db.execute(select(m.ERPOperation, m.HumanIntent).join(m.HumanIntent, m.HumanIntent.id == m.ERPOperation.intent_id).order_by(m.ERPOperation.created_at.desc()).limit(100)):
        if intent.resource_id not in resource_ids and project.code not in operation.native_id and project.id not in operation.native_id:
            continue
        operations.append(
            {
                "id": operation.id,
                "intent_id": operation.intent_id,
                "action": operation.action,
                "native_id": operation.native_id,
                "state": operation.state,
                "request_hash": operation.request_hash,
                "erp_user_id": operation.erp_user_id,
                "error_code": operation.error_code,
                "response_present": operation.response is not None,
                "created_at": operation.created_at,
            }
        )
    pending = [item for item in operations if item["state"] in {"DISPATCHING", "REJECTED", "UNKNOWN"}]
    return {
        "authority_strategy": "按权威来源直接查询/调用；不维护 ERP 镜像、CDC 投影或先本地后 ERP 的替代查找链路。",
        "task_sources": task_sources,
        "closure_item_sources": closure_sources,
        "erp_operations": operations,
        "pending_or_failed_source_operations": pending,
    }


def query(db, viewer, data: GovernanceContextInput, allowed_tools):
    project, reasons, truncated = _resolve_project(db, viewer, data)
    base = {
        "source": "agent_db",
        "as_of": now().isoformat(),
        "limitations": [
            "本工具只读，不新增授权、不下载附件、不导出数据、不替代正式审批。",
            "问答、页面、附件下载和工具汇总均应经过同一授权链路；看不到的类别不能被解释为业务不存在。",
            "ERP 事实只保留原生引用和操作状态，不建立本地镜像、CDC 投影或静默认定成功。",
        ],
    }
    if project is None:
        if reasons is None:
            return {**base, "resolution": "NOT_FOUND_OR_FORBIDDEN", "data": [], "limitations": base["limitations"] + ["指定项目不可见、已不存在或当前无权访问；为避免泄露不进一步区分。"]}
        if not reasons:
            return {**base, "resolution": "NOT_FOUND", "data": [], "limitations": base["limitations"] + (["项目候选检索最多检查前500个可见项目。"] if truncated else [])}
        return {**base, "resolution": "MULTIPLE_CANDIDATES", "data": reasons, "limitations": base["limitations"] + ["编号命中多个可见项目，请使用候选项目 ID 再查询。"]}

    target = _target_user(db, viewer, data.target_user_id)
    resource_ids, subjects, cases, attachments = _visible_business_resources(db, viewer, project.id)
    result = {
        "project": _project_card(db, viewer, project, reasons),
        "viewer": {"id": viewer.id, "username": viewer.username, "super_admin": viewer.super_admin},
        "target_authorization": _grant_summary(db, target),
        "permission_matrix": _permission_matrix(db, target, project.id),
        "permission_boundary": {
            "consistent_surfaces": ["conversation_tools", "pages", "attachment_preview_download", "notifications"],
            "summary_must_not_bypass_fields": True,
            "attachment_links_require_business_read_after_linking": True,
            "run_security_version_and_authorization_hash_checked": True,
        },
        "visible_resources": {"business_subjects": subjects, "contact_cases": cases},
        "audit_trail": _audit_trail(db, resource_ids),
        "notifications": _notification_status(db, resource_ids),
        "attachments": attachments,
        "source_responsibility": _source_facts(db, project, resource_ids),
    }
    return jsonable_encoder({**base, "resolution": "RESOLVED", "data": [result]})
