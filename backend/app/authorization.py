"""Complete grants stay intact: OR of AND groups, never Cartesian scope unions."""
from dataclasses import dataclass
from sqlalchemy import select, and_, or_, false, true
from .db import now
from .models import Grant, User, Capability
from .errors import DomainError

PERMISSIONS = {
    "project.read": ["id", "code", "name", "status"],
    "purchase.read": ["id", "number", "project_id", "material_id", "material_name", "category", "quantity", "unit", "due_date", "remark", "status", "created_at", "revision", "created_by"],
    "purchase.create": ["project_id", "material_id", "quantity", "due_date", "remark"],
    "purchase.submit": ["*"], "purchase.approve": ["*"],
    "workflow.design": ["*"], "workflow.publish": ["*"],
    "user.manage": ["*"], "grant.manage": ["*"], "audit.read": ["*"],
}
PERMISSIONS['file.upload']=['*']
DIMENSIONS = {"project_id", "category", "warehouse_id"}
from .domain_schemas import PERMISSIONS as DOMAIN_PERMISSIONS
PERMISSIONS.update(DOMAIN_PERMISSIONS)
PERMISSIONS.update({f'contact.{action}':['*'] for action in ('read','create','coordinate','assign','respond','record','attach','plan','review','close','set_reviewer','cancel_task')})


@dataclass(frozen=True)
class Access:
    allowed: bool
    fields: frozenset[str]


def valid_scope(scope):
    if scope == {"all": True}: return
    if not scope or not set(scope).issubset(DIMENSIONS):
        raise DomainError("INVALID_SCOPE", "请选择明确的数据范围；全量授权必须显式选择")
    for values in scope.values():
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v for v in values):
            raise DomainError("INVALID_SCOPE", "每个范围维度必须包含有效标识")


def grants_for(db, user, permission):
    return list(db.scalars(select(Grant).where(
        Grant.user_id == user.id, Grant.permission == permission, Grant.active.is_(True),
        or_(Grant.valid_from.is_(None), Grant.valid_from <= now()),
        or_(Grant.valid_to.is_(None), Grant.valid_to > now()),
    )))


def matches(scope, obj):
    return scope == {"all": True} or bool(scope) and all(obj.get(k) in v for k, v in scope.items())


def access(db, user, permission, obj) -> Access:
    if not user.active or permission not in PERMISSIONS: return Access(False, frozenset())
    if user.super_admin: return Access(True, frozenset(PERMISSIONS[permission]))
    matching = [g for g in grants_for(db, user, permission) if matches(g.scope, obj)]
    if any(g.effect == "DENY" for g in matching): return Access(False, frozenset())
    allowed = [g for g in matching if g.effect == "ALLOW"]
    return Access(bool(allowed), frozenset(f for g in allowed for f in g.fields))


def require(db, user, permission, obj=None):
    result = access(db, user, permission, obj or {})
    if not result.allowed: raise DomainError("FORBIDDEN", "资源不存在或无权访问", 403)
    return result.fields


def predicate(db, user, permission, columns):
    if not user.active: return false()
    if user.super_admin: return true()
    allow, deny = [], []
    for grant in grants_for(db, user, permission):
        if grant.scope == {"all": True}: p = true()
        elif not set(grant.scope).issubset(columns):
            # Unsupported scopes fail closed. A DENY must not disappear.
            p = true() if grant.effect == "DENY" else false()
        else: p = and_(*(columns[k].in_(values) for k, values in grant.scope.items()))
        (deny if grant.effect == "DENY" else allow).append(p)
    return and_(or_(false(), *allow), ~or_(false(), *deny))


def select_fields(data, allowed):
    return data if "*" in allowed else {k: v for k, v in data.items() if k in allowed}


def fingerprint(db, user):
    """Time-sensitive authorization fence, including grant activation and expiry."""
    from .bpm import content_hash
    current = now()
    grants = list(db.scalars(select(Grant).where(
        Grant.user_id == user.id, Grant.active.is_(True),
        or_(Grant.valid_from.is_(None), Grant.valid_from <= current),
        or_(Grant.valid_to.is_(None), Grant.valid_to > current)).order_by(Grant.id)))
    capabilities = list(db.scalars(select(Capability).where(
        Capability.user_id == user.id, Capability.enabled.is_(True)).order_by(Capability.id)))
    return content_hash({"user": user.id, "version": user.security_version, "active": user.active,
                         "super_admin": user.super_admin,
                         "grants": [{"id": g.id, "permission": g.permission, "effect": g.effect,
                                     "scope": g.scope, "fields": sorted(g.fields)} for g in grants],
                         "capabilities": [(c.kind, c.key) for c in capabilities]})
