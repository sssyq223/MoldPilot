"""Project-role selector plugged into the business-neutral BPM assignment core."""
from types import SimpleNamespace
from sqlalchemy import delete, select

from agent_core.errors import DomainError
from domain_packs.mold import models as m
from domain_packs.mold.authorization import PERMISSIONS, predicate
from domain_packs.mold.ports.events import record


PROJECT_ROLES = {
    "PROJECT_OWNER": ("项目负责人", "项目总体交付责任人"),
    "DESIGN_OWNER": ("设计负责人", "设计、BOM 与工艺路线责任人"),
    "PURCHASE_OWNER": ("采购负责人", "项目采购协调责任人"),
    "MANUFACTURING_OWNER": ("制造负责人", "制造进度与现场协同责任人"),
    "ASSEMBLY_OWNER": ("装配负责人", "装配齐套与完工责任人"),
    "TRIAL_OWNER": ("试模负责人", "试模排期与问题闭环责任人"),
    "QUALITY_OWNER": ("质量负责人", "质量检验与整改复核责任人"),
    "FINANCE_OWNER": ("财务负责人", "项目收付款节点责任人"),
}


def validate_role_keys(role_keys):
    if (
        not isinstance(role_keys, list)
        or not role_keys
        or len(role_keys) > len(PROJECT_ROLES)
        or len(role_keys) != len(set(role_keys))
        or any(key not in PROJECT_ROLES for key in role_keys)
    ):
        raise DomainError("INVALID_WORKFLOW", "项目角色须从业务包已发布的角色目录中选择且不能重复")


def _project(db, project_id):
    project = db.get(m.Project, project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    return project


def _members(db, project_id):
    rows = list(db.scalars(select(m.ProjectRoleMember).where(
        m.ProjectRoleMember.project_id == project_id,
    ).order_by(m.ProjectRoleMember.role_key, m.ProjectRoleMember.user_id)))
    # Existing projects keep their authoritative ProjectProfile owner until the
    # first explicit project-role save writes the synchronized membership row.
    if not any(row.role_key == "PROJECT_OWNER" for row in rows):
        profile = db.get(m.ProjectProfile, project_id)
        if profile:
            rows.append(SimpleNamespace(
                project_id=project_id,
                role_key="PROJECT_OWNER",
                user_id=profile.owner_user_id,
            ))
    return rows


def catalog(db, user):
    query = select(m.Project).order_by(m.Project.code)
    if not user.super_admin:
        query = query.where(predicate(db, user, "project.read", {"project_id": m.Project.id}))
    projects = [
        {"id": project.id, "code": project.code, "name": project.name, "status": project.status}
        for project in db.scalars(query.limit(500))
    ]
    categories = sorted({
        value
        for value in (
            *db.scalars(select(m.Material.category).distinct()),
            *db.scalars(select(m.BusinessSubject.category).where(m.BusinessSubject.category.is_not(None)).distinct()),
        )
        if value
    })
    warehouse_query = select(m.Warehouse).where(m.Warehouse.active.is_(True)).order_by(m.Warehouse.code)
    if not user.super_admin:
        warehouse_query = warehouse_query.where(
            predicate(db, user, "warehouse.read", {"warehouse_id": m.Warehouse.id})
        )
    warehouses = [
        {"id": warehouse.id, "code": warehouse.code, "name": warehouse.name}
        for warehouse in db.scalars(warehouse_query.limit(500))
    ]
    suppliers = [
        {"id": supplier.id, "code": supplier.code, "name": supplier.name}
        for supplier in db.scalars(select(m.Supplier).order_by(m.Supplier.code).limit(500))
    ]
    return {
        "kind": "project",
        "label": "项目角色",
        "scope_label": "指定项目",
        "context_key": "project_id",
        "roles": [
            {
                "key": key,
                "name": value[0],
                "description": value[1],
                "max_members": 1 if key == "PROJECT_OWNER" else None,
            }
            for key, value in PROJECT_ROLES.items()
        ],
        "scopes": projects,
        "capabilities": [{"key": key} for key in sorted(PERMISSIONS)],
        "responsibility_dimensions": [
            {"key": "project_id", "name": "当前项目", "default": True, "values": projects},
            {"key": "category", "name": "业务品类", "default": False, "values": [
                {"id": category, "code": category, "name": category}
                for category in categories
            ]},
            {"key": "warehouse_id", "name": "仓库", "default": False, "values": warehouses},
            {"key": "supplier_id", "name": "供应商", "default": False, "values": suppliers},
        ],
    }


def binding_data(db, project_id):
    project = _project(db, project_id)
    config = db.get(m.ProjectRoleConfig, project_id)
    return {
        "scope": {"id": project.id, "code": project.code, "name": project.name},
        "version": config.version if config else 0,
        "entries": [
            {"role_key": row.role_key, "user_id": row.user_id}
            for row in _members(db, project_id)
        ],
    }


def save_bindings(db, user, project_id, entries, expected_version, reason):
    project = db.scalar(select(m.Project).where(m.Project.id == project_id).with_for_update())
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    config = db.scalar(select(m.ProjectRoleConfig).where(
        m.ProjectRoleConfig.project_id == project_id,
    ).with_for_update())
    version = config.version if config else 0
    if version != expected_version:
        raise DomainError("VERSION_CONFLICT", "项目角色配置已变化，请刷新后重试", 409)
    pairs = [(entry["role_key"], entry["user_id"]) for entry in entries]
    if len(pairs) != len(set(pairs)) or len(pairs) > 200:
        raise DomainError("INVALID_INPUT", "项目角色成员不能重复且每个项目最多配置200项")
    if any(role_key not in PROJECT_ROLES for role_key, _ in pairs):
        raise DomainError("INVALID_INPUT", "项目角色不在业务包已发布目录中")
    user_ids = {user_id for _, user_id in pairs}
    people = list(db.scalars(select(m.User).where(m.User.id.in_(user_ids)).order_by(m.User.id))) if user_ids else []
    if user_ids != {person.id for person in people} or any(not person.active for person in people):
        raise DomainError("ASSIGNMENT_BLOCKED", "项目角色成员不存在或已停用")
    owners = [user_id for role_key, user_id in pairs if role_key == "PROJECT_OWNER"]
    if len(owners) > 1:
        raise DomainError("INVALID_INPUT", "一个项目只能配置一位项目负责人")
    profile = db.get(m.ProjectProfile, project_id)
    if profile and len(owners) != 1:
        raise DomainError("INVALID_INPUT", "已有项目档案必须保留一位项目负责人")

    before = binding_data(db, project_id)
    previous_ids = set(db.scalars(select(m.ProjectRoleMember.user_id).where(
        m.ProjectRoleMember.project_id == project_id,
    )))
    db.execute(delete(m.ProjectRoleMember).where(m.ProjectRoleMember.project_id == project_id))
    if config:
        config.version += 1
    else:
        config = m.ProjectRoleConfig(project_id=project_id, version=1)
        db.add(config)
    db.add_all(m.ProjectRoleMember(project_id=project_id, role_key=role_key, user_id=user_id)
               for role_key, user_id in pairs)
    if profile and owners:
        profile.owner_user_id = owners[0]
    affected = previous_ids | user_ids
    for person in db.scalars(select(m.User).where(m.User.id.in_(affected)).with_for_update()):
        person.security_version += 1
    db.flush()
    after = binding_data(db, project_id)
    record(db, user, "project.role.changed", project_id, {
        "reason": reason,
        "before": before,
        "after": after,
    }, list(affected))
    return after


def resolve(db, role_keys, context):
    validate_role_keys(role_keys)
    project_id = context.get("project_id") if isinstance(context, dict) else None
    if not isinstance(project_id, str) or not project_id:
        raise DomainError("ASSIGNMENT_CONTEXT_REQUIRED", "项目角色选人必须提供明确项目")
    project = _project(db, project_id)
    config = db.get(m.ProjectRoleConfig, project_id)
    rows = _members(db, project_id)
    selected = sorted({row.user_id for row in rows if row.role_key in role_keys})
    sources = [{
        "id": f"{project_id}:{role_key}",
        "kind": "DOMAIN_ROLE",
        "name": PROJECT_ROLES[role_key][0],
        "version": config.version if config else 0,
        "scope": {"id": project.id, "code": project.code, "name": project.name},
    } for role_key in role_keys]
    return selected, sources


def publish_candidates(db, role_keys):
    validate_role_keys(role_keys)
    selected = set()
    sources = []
    for role_key in role_keys:
        ids = set(db.scalars(select(m.ProjectRoleMember.user_id).join(
            m.User, m.User.id == m.ProjectRoleMember.user_id,
        ).where(
            m.ProjectRoleMember.role_key == role_key,
            m.User.active.is_(True),
        )))
        if role_key == "PROJECT_OWNER":
            ids.update(db.scalars(select(m.ProjectProfile.owner_user_id).join(
                m.User, m.User.id == m.ProjectProfile.owner_user_id,
            ).where(m.User.active.is_(True))))
        if not ids:
            raise DomainError(
                "ASSIGNMENT_BLOCKED",
                f"项目角色“{PROJECT_ROLES[role_key][0]}”尚未在任何项目配置有效人员",
            )
        selected.update(ids)
        sources.append({
            "id": role_key,
            "kind": "DOMAIN_ROLE",
            "name": PROJECT_ROLES[role_key][0],
            "version": 0,
        })
    return sorted(selected), sources
