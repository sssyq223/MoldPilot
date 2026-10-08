"""Import the ERP approval catalog into MoldPilot-owned BPM definitions.

This command is idempotent and only writes MoldPilot's workflow_definition
table.  It never reads or writes the ERP database and never calls an ERP
approval endpoint.  Imported definitions remain DRAFT until an administrator
maps the project roles and publishes them.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from domain_packs.mold import bpm  # noqa: E402
from domain_packs.mold.ports.events import record  # noqa: E402
from domain_packs.mold.models import (  # noqa: E402
    AssignmentGroup, AssignmentMember, User, WorkflowDefinition,
)
from domain_packs.mold.erp.core.erp_workflow_templates import templates  # noqa: E402


ROLE_GROUPS = {
    "DESIGN_OWNER": ("DEPARTMENT", "设计部"),
    # The imported ERP manager accounts are retained for historical data but
    # inactive; the active department group is the authoritative approver pool.
    "PURCHASE_OWNER": ("DEPARTMENT", "采购部"),
    "PROJECT_OWNER": ("ROLE", "总经理"),
    "FINANCE_OWNER": ("DEPARTMENT", "财务部"),
    "MANUFACTURING_OWNER": ("DEPARTMENT", "生产部"),
    "QUALITY_OWNER": ("DEPARTMENT", "品质部"),
}

DESIGN_FIXED_USERS = {
    "design_review": "于孟",
    "purchase_review": "张亚倩",
    "design_change_review": "于孟",
    "management_review": "李辉",
    "delivery_confirm": "郭伟",
}
DESIGN_POOL_USERS = {
    "project_owner": "李辉",
    "design_owner": "谢志华",
}


def _ensure_department_members(db, name):
    group = db.scalar(select(AssignmentGroup).where(
        AssignmentGroup.kind == "DEPARTMENT", AssignmentGroup.name == name,
    ))
    if group is None:
        group = AssignmentGroup(kind="DEPARTMENT", name=name, active=True, version=1)
        db.add(group)
        db.flush()
    users = list(db.scalars(select(User).where(User.department == name, User.active.is_(True))))
    existing_members = list(db.scalars(select(AssignmentMember).where(AssignmentMember.group_id == group.id)))
    current = set()
    for member in existing_members:
        person = db.get(User, member.user_id)
        if person is None or not person.active or person.department != name:
            db.delete(member)
        else:
            current.add(member.user_id)
    for user in users:
        if user.id not in current:
            db.add(AssignmentMember(group_id=group.id, user_id=user.id, is_head=False))
    return group


def _group_for_role(db, role_key):
    kind, name = ROLE_GROUPS[role_key]
    if kind == "DEPARTMENT":
        return _ensure_department_members(db, name)
    group = db.scalar(select(AssignmentGroup).where(
        AssignmentGroup.kind == kind, AssignmentGroup.name == name,
        AssignmentGroup.active.is_(True),
    ))
    if group is None:
        raise ValueError(f"缺少已迁移的人员组：{kind}/{name}")
    return group


def _user_for_name(db, display_name):
    user = db.scalar(select(User).where(User.username == display_name, User.active.is_(True)))
    if user is None:
        user = db.scalar(select(User).where(User.display_name == display_name, User.active.is_(True)))
    if user is None:
        raise ValueError(f"缺少已迁移的有效审批人：{display_name}")
    return user.id


def _materialize_fixed_design_users(db, process_key, node):
    if not process_key.startswith(("design_", "mold_repair_")):
        return
    if node.get("assignment_pools"):
        for pool in node["assignment_pools"]:
            name = DESIGN_POOL_USERS.get(pool["key"])
            if process_key == "design_order_approval" and pool["key"] == "design_owner":
                name = "于孟"
            if name:
                pool.pop("assignment", None)
                pool["users"] = [_user_for_name(db, name)]
    name = DESIGN_FIXED_USERS.get(node["key"])
    if name and not node.get("assignment_pools"):
        node.pop("assignment", None)
        node["users"] = [_user_for_name(db, name)]


def _materialize_assignment(db, node):
    assignment = node.get("assignment")
    if not assignment:
        return
    if assignment.get("domain_roles"):
        role = assignment.pop("domain_roles")[0]
        group = _group_for_role(db, role)
        assignment.pop("domain_roles_any", None)
        if group.kind == "ROLE":
            assignment["roles"] = [group.id]
        else:
            assignment["departments"] = [group.id]
    elif assignment.get("domain_roles_any"):
        roles = assignment.pop("domain_roles_any")
        assignment["group_names_any"] = [_group_for_role(db, role).name for role in roles]
    assignment["department_heads_only"] = False


def materialize_assignments(db, config):
    for node in config["nodes"]:
        _materialize_fixed_design_users(db, config.get("metadata", {}).get("erp_process_key", ""), node)
        if node.get("assignment_pools"):
            for pool in node["assignment_pools"]:
                if "assignment" in pool:
                    holder = {"assignment": pool["assignment"]}
                    _materialize_assignment(db, holder)
                    pool["assignment"] = holder["assignment"]
        else:
            _materialize_assignment(db, node)
    config.setdefault("metadata", {})["assignment_source"] = "moldpilot_assignment_group"


def migrate(*, dry_run=False, publish=False):
    imported = []
    with SessionLocal.begin() as db:
        publisher = db.scalar(select(User).where(User.super_admin.is_(True), User.active.is_(True)))
        if publish and publisher is None:
            raise ValueError("没有可用的 MoldPilot 超级管理员，不能发布流程")
        for item in templates():
            config = item["config"]
            materialize_assignments(db, config)
            config.setdefault("metadata", {})["migration_status"] = (
                "MIGRATED_PUBLISHED" if publish else "MIGRATED_DRAFT"
            )
            bpm.validate(config)
            xml = bpm.compile_bpmn(config)
            current = db.scalar(select(WorkflowDefinition).where(
                WorkflowDefinition.process_key == item["process_key"],
                WorkflowDefinition.version == 1,
            ))
            if current is None:
                current = WorkflowDefinition(
                    process_key=item["process_key"], version=1, name=item["name"],
                    config=config, bpmn_xml=xml, status="DRAFT",
                    package_hash=bpm.content_hash({"config": config, "xml": xml}),
                )
                db.add(current)
                action = "created"
            else:
                current.name = item["name"]
                current.config = config
                current.bpmn_xml = xml
                current.package_hash = bpm.content_hash({"config": config, "xml": xml})
                if current.status != "PUBLISHED":
                    current.status = "DRAFT"
                action = "updated"
            if publish:
                from agent_core.assignments import check_publish
                check_publish(db, config)
                current.bpmn_xml = xml
                current.package_hash = bpm.content_hash({"config": config, "xml": xml})
                current.status = "PUBLISHED"
                record(db, publisher, "workflow.published", current.id,
                       {"migration_source": item["process_key"], "package_hash": current.package_hash})
            imported.append({"process_key": item["process_key"], "action": action,
                             "status": current.status, "nodes": len(config["nodes"])})
        if dry_run:
            db.rollback()
    return imported


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--publish", action="store_true", help="通过当前 MoldPilot 超级管理员发布迁移流程")
    args = parser.parse_args()
    for item in migrate(dry_run=args.dry_run, publish=args.publish):
        print(f"{item['action']}: {item['process_key']} [{item['status']}] nodes={item['nodes']}")
