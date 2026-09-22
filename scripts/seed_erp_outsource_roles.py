"""Ensure ERP outsource organization roles, departments, and optional test accounts.

Creates AssignmentGroup ROLE/DEPARTMENT rows used by admin login isolation, and
optionally seeds five non-admin accounts with the matching Grant bundles.

Does not register Tools/Skills yet — outsource Skill encapsulation comes later.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.authorization import PERMISSIONS
from app.db import make_engine
from app.models import AssignmentGroup, AssignmentMember, Grant, User
from app.security import hasher, normalize_username
from domain_packs.mold.erp.procurement.erp_outsource_roles import (
    ERP_OUTSOURCE_DEPARTMENTS,
    ERP_OUTSOURCE_ROLES,
)
from sqlalchemy.orm import sessionmaker


DEFAULT_PASSWORD = "OutsourceRole-2026!"


def _database_url(env_file: str, explicit_url: str) -> str:
    if explicit_url:
        return explicit_url
    if os.environ.get("AGENT_DATABASE_URL"):
        return os.environ["AGENT_DATABASE_URL"]
    if os.environ.get("MOLD_DATABASE_URL"):
        return os.environ["MOLD_DATABASE_URL"]
    values = dotenv_values(env_file)
    return values.get("AGENT_DATABASE_URL") or values.get("MOLD_DATABASE_URL") or ""


def _require_postgresql(url: str) -> None:
    parsed = urlsplit(url)
    if not url:
        raise SystemExit("PostgreSQL DSN is required. Set AGENT_DATABASE_URL or pass --database-url.")
    if parsed.scheme.startswith("sqlite"):
        raise SystemExit("Refusing SQLite for organization role seed.")
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


def _ensure_member(db, group: AssignmentGroup, user: User, *, is_head: bool = False) -> None:
    existing = db.get(AssignmentMember, {"group_id": group.id, "user_id": user.id})
    if existing is None:
        db.add(AssignmentMember(group_id=group.id, user_id=user.id, is_head=is_head))
    else:
        existing.is_head = is_head


def _ensure_grant(db, *, user: User, permission: str, granted_by: User, reason: str) -> None:
    if permission not in PERMISSIONS:
        raise SystemExit(f"Permission not registered in catalog: {permission}")
    row = db.scalar(
        select(Grant).where(
            Grant.user_id == user.id,
            Grant.permission == permission,
            Grant.active.is_(True),
            Grant.effect == "ALLOW",
        ).limit(1)
    )
    if row is None:
        db.add(
            Grant(
                user_id=user.id,
                permission=permission,
                effect="ALLOW",
                scope={"all": True},
                fields=list(PERMISSIONS[permission]),
                reason=reason,
                granted_by=granted_by.id,
            )
        )
        user.security_version += 1
        return
    # Keep an existing ALLOW grant; refresh fields if the catalog changed.
    row.fields = list(PERMISSIONS[permission])
    if row.scope != {"all": True}:
        # Preserve tighter scopes already configured by an administrator.
        return


def seed(db, *, create_users: bool, password: str) -> dict:
    admin = db.scalar(select(User).where(User.super_admin.is_(True), User.active.is_(True)).limit(1))
    if admin is None:
        raise SystemExit("No active super admin found; bootstrap an administrator first.")

    departments = {
        name: _ensure_group(db, "DEPARTMENT", name) for name in ERP_OUTSOURCE_DEPARTMENTS
    }
    roles = {
        role["role_name"]: _ensure_group(db, "ROLE", role["role_name"])
        for role in ERP_OUTSOURCE_ROLES
    }

    users: dict[str, str] = {}
    if create_users:
        if len(password) < 12:
            raise SystemExit("Seed password must contain at least 12 characters.")
        for role in ERP_OUTSOURCE_ROLES:
            username = normalize_username(role["username"])
            user = db.scalar(select(User).where(User.username == username))
            if user is None:
                user = User(
                    username=username,
                    display_name=role["display_name"],
                    department=role["department_name"],
                    password_hash=hasher.hash(password),
                    super_admin=False,
                    active=True,
                )
                db.add(user)
                db.flush()
            else:
                user.display_name = role["display_name"]
                user.department = role["department_name"]
                user.active = True
                user.password_hash = hasher.hash(password)

            department = departments[role["department_name"]]
            role_group = roles[role["role_name"]]
            _ensure_member(db, department, user, is_head=False)
            _ensure_member(db, role_group, user, is_head=False)

            for permission in role["permissions"]:
                _ensure_grant(
                    db,
                    user=user,
                    permission=permission,
                    granted_by=admin,
                    reason=f"委外角色种子：{role['role_name']}",
                )
            users[role["key"]] = user.username

    db.flush()
    return {
        "departments": sorted(departments),
        "roles": sorted(roles),
        "users": users,
        "permissions": sorted(
            {permission for role in ERP_OUTSOURCE_ROLES for permission in role["permissions"]}
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--database-url", default="")
    parser.add_argument(
        "--create-users",
        action="store_true",
        help="Also create five synthetic login accounts and Grant bundles.",
    )
    parser.add_argument(
        "--password",
        default=DEFAULT_PASSWORD,
        help=f"Password for seeded accounts (default {DEFAULT_PASSWORD}).",
    )
    args = parser.parse_args()
    url = _database_url(args.env_file, args.database_url)
    _require_postgresql(url)
    engine = make_engine(url)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with Session.begin() as db:
        result = seed(db, create_users=args.create_users, password=args.password)
    print("ERP outsource organization roles ensured.")
    print("departments:", ", ".join(result["departments"]))
    print("roles:", ", ".join(result["roles"]))
    print("permission codes:", ", ".join(result["permissions"]))
    if result["users"]:
        print("users:", ", ".join(f"{key}={name}" for key, name in result["users"].items()))
        print(f"shared password: {args.password}")
    else:
        print("users: skipped (pass --create-users to seed login accounts)")


if __name__ == "__main__":
    main()
