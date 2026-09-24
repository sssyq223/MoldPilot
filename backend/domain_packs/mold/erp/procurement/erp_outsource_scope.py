"""Resolve outsource query/execute scope from Grant, not Skill text."""
from __future__ import annotations

from sqlalchemy import select

from agent_core.models import AssignmentGroup, AssignmentMember
from domain_packs.mold import models as m
from domain_packs.mold.authorization import grants_for
from domain_packs.mold.erp.procurement.erp_outsource_db import fetch_one
from domain_packs.mold.ports.errors import DomainError

MANAGER_NODE = "采购主管"
GM_NODE = "总经理"
OUTSOURCE_BUYER_SCOPE_SQL = """
SELECT scope.id
FROM purchase_buyer_scope scope
JOIN sys_user_role user_role ON user_role.user_id = scope.user_id
JOIN sys_role role ON role.role_id = user_role.role_id
WHERE scope.user_id = %(erp_user_id)s
  AND lower(btrim(scope.material_category)) = 'outsource'
  AND coalesce(scope.enabled, true) = true
  AND coalesce(scope.is_deleted, 0) = 0
  AND (scope.valid_from IS NULL OR scope.valid_from <= CURRENT_TIMESTAMP)
  AND (scope.valid_to IS NULL OR scope.valid_to >= CURRENT_TIMESTAMP)
  AND role.role_key = 'purchase_user'
  AND role.status = '0'
  AND role.del_flag = '0'
LIMIT 1
"""


def has_allow(db, user, permission: str) -> bool:
    if getattr(user, "super_admin", False):
        return True
    return any(grant.effect == "ALLOW" for grant in grants_for(db, user, permission))


def require_allow(db, user, permission: str, message: str) -> None:
    if not has_allow(db, user, permission):
        raise DomainError("FORBIDDEN", message, 403)


def require_outsource_buyer_scope(db, user) -> None:
    """Require the same active outsource category scope used by ERP writes."""
    if getattr(user, "super_admin", False):
        return
    identity = db.get(m.ERPIdentity, user.id)
    raw_user_id = str(getattr(identity, "erp_user_id", "") or "").strip()
    # purchase_buyer_scope.user_id is BIGINT; a non-numeric ERP identity can
    # never be in scope, so fail closed instead of sending text to the DB.
    erp_user_id = int(raw_user_id) if raw_user_id.isdigit() else None
    if erp_user_id is None or fetch_one(OUTSOURCE_BUYER_SCOPE_SQL, {"erp_user_id": erp_user_id}) is None:
        raise DomainError(
            "FORBIDDEN",
            "当前 ERP 账号不在有效的委外采购责任域，不能办理采购委外",
            403,
        )


def supplier_codes_for(db, user) -> list[str] | None:
    """ERP partner codes allowed for the current processor account.

    None means unrestricted (super admin or all-scope). An empty list means
    the account is a processor but has no supplier_id bound.
    """
    if getattr(user, "super_admin", False):
        return None
    codes: list[str] = []
    seen: set[str] = set()
    for grant in grants_for(db, user, "erp_outsource_processor.read"):
        if grant.effect != "ALLOW":
            continue
        if grant.scope == {"all": True}:
            return None
        for supplier_id in grant.scope.get("supplier_id") or []:
            supplier = db.get(m.Supplier, supplier_id)
            code = str(getattr(supplier, "code", "") or "").strip()
            normalized = code.casefold()
            if code and normalized not in seen:
                seen.add(normalized)
                codes.append(code)
    # Supplier names and login names are deliberately excluded. ERP rows carry
    # the stable partner_code and scope checks must use exact identifiers.
    return codes


def approval_node_tokens(db, user) -> list[str] | None:
    """Which outsource-approval nodes the login may see.

    None means unrestricted. A list filters task.node_name by substring.
    """
    if getattr(user, "super_admin", False):
        return None
    names = set(
        db.scalars(
            select(AssignmentGroup.name).join(
                AssignmentMember, AssignmentMember.group_id == AssignmentGroup.id
            ).where(
                AssignmentMember.user_id == user.id,
                AssignmentGroup.kind == "ROLE",
                AssignmentGroup.active.is_(True),
            )
        )
    )
    tokens: list[str] = []
    if "委外采购主管" in names:
        tokens.append(MANAGER_NODE)
    if "总经理" in names:
        tokens.append(GM_NODE)
    # A non-admin approval account without a mapped role must see no nodes.
    # None is reserved for the super-admin unrestricted scope.
    return tokens
