"""Create real outsource role login accounts for permission isolation demos.

People mapping (requested):
- buyer: 徐桂利
- approval: 王群
- gm: 李辉
- warehouse: 薛海峰
- quality: 赵殿烨
- processor: ERP supplier SUP000001

Password is intentionally short when --password is provided; API user-create
still requires 12+ characters, but these accounts are written directly.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.authorization import PERMISSIONS
from app.db import make_engine
from app.models import AssignmentGroup, AssignmentMember, Grant, User
from app.security import hasher, normalize_username
from domain_packs.mold import models as m
from domain_packs.mold.erp.procurement.erp_outsource_roles import (
    ERP_OUTSOURCE_DEPARTMENTS,
    ERP_OUTSOURCE_ROLES,
    role_by_key,
)


DEFAULT_PASSWORD = "123456"
SYNTHETIC_USERNAMES = {
    "outsource_buyer",
    "outsource_approver",
    "outsource_gm",
    "outsource_processor",
    "outsource_warehouse",
    "outsource_quality",
}

PEOPLE = (
    {
        "role_key": "erp_outsource_buyer",
        "username": "xuguili",
        "display_name": "徐桂利",
    },
    {
        "role_key": "erp_outsource_approval",
        "username": "wangqun",
        "display_name": "王群",
    },
    {
        "role_key": "erp_outsource_gm",
        "username": "lihui",
        "display_name": "李辉",
    },
    {
        "role_key": "erp_outsource_warehouse",
        "username": "xuehaifeng",
        "display_name": "薛海峰",
    },
    {
        "role_key": "erp_outsource_quality",
        "username": "zhaodianye",
        "display_name": "赵殿烨",
    },
    {
        "role_key": "erp_outsource_processor",
        "username": "SUP000001",
        "display_name": "SUP000001加工商",
        "supplier_code": "SUP000001",
        "supplier_name": "ERP加工商 SUP000001",
    },
)


def _database_url(env_file: str, explicit_url: str) -> str:
    if explicit_url:
        return explicit_url
    if os.environ.get("AGENT_DATABASE_URL"):
        return os.environ["AGENT_DATABASE_URL"]
    values = dotenv_values(env_file)
    return values.get("AGENT_DATABASE_URL") or values.get("MOLD_DATABASE_URL") or ""


def _require_postgresql(url: str) -> None:
    parsed = urlsplit(url)
    if not url:
        raise SystemExit("PostgreSQL DSN is required.")
    if not parsed.scheme.startswith("postgresql"):
        raise SystemExit(f"Unsupported database scheme: {parsed.scheme}")


def _ensure_group(db, kind: str, name: str) -> AssignmentGroup:
    row = db.scalar(
        select(AssignmentGroup).where(AssignmentGroup.kind == kind, AssignmentGroup.name == name)
    )
    if row is None:
        row = AssignmentGroup(kind=kind, name=name, active=True, version=1)
        db.add(row)
        db.flush()
    elif not row.active:
        row.active = True
        row.version += 1
    return row


def _ensure_member(db, group: AssignmentGroup, user: User) -> None:
    existing = db.get(AssignmentMember, {"group_id": group.id, "user_id": user.id})
    if existing is None:
        db.add(AssignmentMember(group_id=group.id, user_id=user.id, is_head=False))


def _ensure_grant(db, *, user: User, permission: str, scope: dict, granted_by: User, reason: str) -> None:
    if permission not in PERMISSIONS:
        raise SystemExit(f"Permission not registered: {permission}")
    rows = list(
        db.scalars(
            select(Grant).where(
                Grant.user_id == user.id,
                Grant.permission == permission,
                Grant.active.is_(True),
                Grant.effect == "ALLOW",
            )
        )
    )
    for row in rows:
        if row.scope == scope:
            row.fields = list(PERMISSIONS[permission])
            return
    db.add(
        Grant(
            user_id=user.id,
            permission=permission,
            effect="ALLOW",
            scope=scope,
            fields=list(PERMISSIONS[permission]),
            reason=reason,
            granted_by=granted_by.id,
        )
    )
    user.security_version += 1


def _ensure_supplier(db, code: str, name: str) -> m.Supplier:
    row = db.scalar(select(m.Supplier).where(m.Supplier.code == code))
    if row is None:
        row = m.Supplier(code=code, name=name, category="outsource", active=True)
        db.add(row)
        db.flush()
        return row
    row.name = name
    row.category = "outsource"
    row.active = True
    return row


def seed(db, *, password: str, deactivate_synthetic: bool) -> dict:
    admin = db.scalar(select(User).where(User.super_admin.is_(True), User.active.is_(True)).limit(1))
    if admin is None:
        raise SystemExit("No active super admin found; bootstrap an administrator first.")

    departments = {name: _ensure_group(db, "DEPARTMENT", name) for name in ERP_OUTSOURCE_DEPARTMENTS}
    roles = {role["role_name"]: _ensure_group(db, "ROLE", role["role_name"]) for role in ERP_OUTSOURCE_ROLES}

    created = []
    for person in PEOPLE:
        role = role_by_key(person["role_key"])
        username = normalize_username(person["username"])
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(
                username=username,
                display_name=person["display_name"],
                department=role["department_name"],
                password_hash=hasher.hash(password),
                super_admin=False,
                active=True,
            )
            db.add(user)
            db.flush()
        else:
            user.display_name = person["display_name"]
            user.department = role["department_name"]
            user.active = True
            user.password_hash = hasher.hash(password)

        _ensure_member(db, departments[role["department_name"]], user)
        _ensure_member(db, roles[role["role_name"]], user)

        scope = {"all": True}
        if person.get("supplier_code"):
            supplier = _ensure_supplier(db, person["supplier_code"], person["supplier_name"])
            # Processor grants are limited to the ERP supplier identity.
            scope = {"supplier_id": [supplier.id]}
            # project.read still needs a usable scope; keep broad project visibility
            # for locating assigned work until ERP outsource tools enforce supplier filters.
            project_scope = {"all": True}
        else:
            project_scope = scope

        for permission in role["permissions"]:
            grant_scope = project_scope if permission == "project.read" and person.get("supplier_code") else scope
            # Non-processor roles keep all-scope. Processor execute/read use supplier_id.
            if person.get("supplier_code") and permission.startswith("erp_outsource_processor."):
                grant_scope = scope
            _ensure_grant(
                db,
                user=user,
                permission=permission,
                scope=grant_scope,
                granted_by=admin,
                reason=f"委外实名账号：{person['display_name']} / {role['role_name']}",
            )

        created.append(
            {
                "username": user.username,
                "display_name": user.display_name,
                "role": role["role_name"],
                "department": user.department,
            }
        )

    deactivated = []
    if deactivate_synthetic:
        for username in sorted(SYNTHETIC_USERNAMES):
            user = db.scalar(select(User).where(User.username == username))
            if user and user.active:
                user.active = False
                user.security_version += 1
                deactivated.append(username)

    db.flush()
    return {"accounts": created, "deactivated": deactivated, "password": password}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument(
        "--keep-synthetic",
        action="store_true",
        help="Do not deactivate previous outsource_* test accounts.",
    )
    args = parser.parse_args()
    url = _database_url(args.env_file, args.database_url)
    _require_postgresql(url)
    engine = make_engine(url)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with Session.begin() as db:
        result = seed(db, password=args.password, deactivate_synthetic=not args.keep_synthetic)
    print("ERP outsource people accounts ready.")
    for account in result["accounts"]:
        print(
            f"- {account['username']} / {account['display_name']} "
            f"[{account['role']} · {account['department']}]"
        )
    print(f"password: {result['password']}")
    if result["deactivated"]:
        print("deactivated synthetic:", ", ".join(result["deactivated"]))


if __name__ == "__main__":
    main()
