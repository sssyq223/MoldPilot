"""Generic approval assignment resolution through the host model contract."""
from itertools import product

from sqlalchemy import select

from agent_core.domain_pack import authorization_contract, component
from agent_core.errors import DomainError
from agent_core.host_ports import host_ports


def valid_ids(value, allow_empty=False):
    return (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and len(value) <= 50
        and all(isinstance(item, str) and 1 <= len(item) <= 36 for item in value)
        and len(value) == len(set(value))
    )


def valid_keys(value, allow_empty=False):
    return (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and len(value) <= 50
        and all(isinstance(item, str) and 1 <= len(item) <= 60 for item in value)
        and len(value) == len(set(value))
    )


def validate_assignment(node):
    if "assignment" not in node:
        if not valid_ids(node.get("users")):
            raise DomainError("ASSIGNMENT_BLOCKED", "必须配置有效且不重复的审批人员")
        return
    rule = node["assignment"]
    allowed_fields = {
        "roles", "departments", "department_heads_only", "domain_roles",
        "business_permissions", "responsibility_scope",
    }
    if (
        ("users" in node and node["users"] != [])
        or not isinstance(rule, dict)
        or set(rule) - allowed_fields
        or not valid_ids(rule.get("roles", []), True)
        or not valid_ids(rule.get("departments", []), True)
        or not valid_keys(rule.get("domain_roles", []), True)
        or not valid_keys(rule.get("business_permissions", []), True)
        or not valid_keys(rule.get("responsibility_scope", []), True)
        or type(rule.get("department_heads_only")) is not bool
        or not (
            rule.get("roles") or rule.get("departments") or rule.get("domain_roles")
            or rule.get("business_permissions")
        )
        or (rule["department_heads_only"] and not rule.get("departments"))
        or bool(rule.get("business_permissions")) != bool(rule.get("responsibility_scope"))
    ):
        raise DomainError(
            "INVALID_WORKFLOW",
            "人员规则须选择角色、部门、业务包领域角色或业务能力与责任域；部门负责人须指定部门，不能混用指定用户",
        )
    if rule.get("domain_roles"):
        component("workflow_assignment").validate_role_keys(rule["domain_roles"])
    if rule.get("business_permissions"):
        contract = authorization_contract()
        if any(key not in contract.PERMISSIONS for key in rule["business_permissions"]):
            raise DomainError("INVALID_WORKFLOW", "人员规则引用了业务包未发布的业务能力")
        if any(key not in contract.DIMENSIONS for key in rule["responsibility_scope"]):
            raise DomainError("INVALID_WORKFLOW", "人员规则引用了业务包未发布的责任域")


def _scope_values(value):
    if isinstance(value, str) and value:
        return [value]
    if (
        isinstance(value, list)
        and value
        and len(value) <= 50
        and all(isinstance(item, str) and item for item in value)
    ):
        return list(dict.fromkeys(value))
    return None


def _grant_matches(grant_scope, point):
    if grant_scope == {"all": True}:
        return True
    return bool(grant_scope) and all(
        key in grant_scope and value in grant_scope[key]
        for key, value in point.items()
    )


def _responsibility_points(responsibility):
    size = 1
    for values in responsibility.values():
        size *= len(values)
    if size > 500:
        raise DomainError(
            "ASSIGNMENT_SCOPE_TOO_BROAD",
            "责任域组合超过500项，请缩小本次业务材料范围",
        )
    keys = list(responsibility)
    return [dict(zip(keys, values)) for values in product(*(responsibility[key] for key in keys))]


def _capability_candidates(db, permissions, scope_keys, context, publish):
    ports = host_ports()
    models = ports.models
    if publish:
        responsibility = None
        responsibility_points = []
    else:
        responsibility = {
            key: _scope_values((context or {}).get(key))
            for key in scope_keys
        }
        if any(value is None for value in responsibility.values()):
            raise DomainError(
                "ASSIGNMENT_CONTEXT_REQUIRED",
                "业务能力选人缺少流程配置要求的责任域上下文",
            )
        responsibility_points = _responsibility_points(responsibility)
    candidates = []
    versions = []
    gaps = {}
    for user in db.scalars(select(models.User).where(models.User.active.is_(True)).order_by(models.User.id)):
        if user.super_admin:
            candidates.append(user.id)
            versions.append((user.id, user.security_version))
            continue
        qualified = True
        reasons = []
        for permission in permissions:
            grants = ports.grants_for(db, user, permission)
            allows = [grant for grant in grants if grant.effect == "ALLOW"]
            denies = [grant for grant in grants if grant.effect == "DENY"]
            if publish:
                permission_ok = bool(allows) and not any(grant.scope == {"all": True} for grant in denies)
            else:
                matching_deny = any(
                    (
                        grant.scope == {"all": True}
                        or (
                            set(grant.scope).issubset(point)
                            and _grant_matches(grant.scope, point)
                        )
                    )
                    for point in responsibility_points
                    for grant in denies
                )
                all_points_allowed = all(
                    any(_grant_matches(grant.scope, point) for grant in allows)
                    for point in responsibility_points
                )
                permission_ok = all_points_allowed and not matching_deny
                if not permission_ok:
                    if matching_deny:
                        code, message = "DENIED", "命中当前责任域的明确拒绝授权"
                    elif not allows:
                        code, message = "MISSING_PERMISSION", "缺少当前有效的允许授权"
                    else:
                        code, message = "SCOPE_MISMATCH", "允许授权未覆盖当前责任域"
                    reasons.append({
                        "permission": permission,
                        "code": code,
                        "message": message,
                    })
            if not permission_ok:
                qualified = False
        if qualified:
            candidates.append(user.id)
            versions.append((user.id, user.security_version))
        elif reasons:
            gaps[user.id] = reasons
    source_scope = (
        {"keys": list(scope_keys), "resolved": False}
        if publish else {
            "keys": list(scope_keys),
            "values": {
                key: values[0] if len(values) == 1 else values
                for key, values in responsibility.items()
            },
            "resolved": True,
        }
    )
    sources = [{
        "id": permission,
        "kind": "BUSINESS_CAPABILITY",
        "name": permission,
        "scope": source_scope,
        "version": ports.content_hash({
            "permission": permission,
            "scope": source_scope,
            "candidates": versions,
        }),
    } for permission in permissions]
    return candidates, sources, gaps


def validate_add_sign_policy(node):
    policy = node.get("add_sign_policy")
    if policy is None:
        return
    if (
        not isinstance(policy, dict)
        or set(policy) != {"timings", "users"}
        or not valid_ids(policy.get("users"))
        or not isinstance(policy.get("timings"), list)
        or not policy["timings"]
        or len(policy["timings"]) != len(set(policy["timings"]))
        or any(timing not in {"PRE", "POST"} for timing in policy["timings"])
    ):
        raise DomainError(
            "INVALID_WORKFLOW",
            "加签策略须选择前加签或后加签，并配置不重复的合格人员池",
        )


def _resolve_users(db, node, context=None, publish=False, explain=False):
    validate_assignment(node)
    if "assignment" not in node:
        ids = list(node["users"])
        details = {"candidate_basis_count": len(ids), "gaps": []}
        if explain:
            models = host_ports().models
            for user_id in ids:
                person = db.get(models.User, user_id)
                if not person or not person.active:
                    details["gaps"].append({
                        "user_id": user_id,
                        "reasons": [{
                            "permission": None,
                            "code": "ACCOUNT_INACTIVE",
                            "message": "账号不存在或已停用",
                        }],
                    })
        return ids, [], details
    models = host_ports().models
    rule = node["assignment"]
    selections, basis_selections, sources = [], [], []
    capability_gaps = {}
    for field, kind in (("roles", "ROLE"), ("departments", "DEPARTMENT")):
        if not rule.get(field):
            continue
        groups = list(db.scalars(
            select(models.AssignmentGroup)
            .where(models.AssignmentGroup.id.in_(rule[field]))
            .order_by(models.AssignmentGroup.id)
            .with_for_update(read=True)
        ))
        if len(groups) != len(rule[field]) or any(
            group.kind != kind or not group.active for group in groups
        ):
            raise DomainError(
                "ASSIGNMENT_BLOCKED",
                "人员规则引用的角色或部门不存在、已停用或类型不匹配",
            )
        query = select(models.AssignmentMember.user_id).where(
            models.AssignmentMember.group_id.in_(rule[field])
        )
        if kind == "DEPARTMENT" and rule["department_heads_only"]:
            query = query.where(models.AssignmentMember.is_head.is_(True))
        selected = set(db.scalars(query))
        selections.append(selected)
        basis_selections.append(selected)
        sources.extend({
            "id": group.id,
            "kind": group.kind,
            "name": group.name,
            "version": group.version,
        } for group in groups)
    if rule.get("domain_roles"):
        extension = component("workflow_assignment")
        domain_ids, domain_sources = (
            extension.publish_candidates(db, rule["domain_roles"])
            if publish and context is None
            else extension.resolve(db, rule["domain_roles"], context)
        )
        selected = set(domain_ids)
        selections.append(selected)
        basis_selections.append(selected)
        sources.extend(domain_sources)
    if rule.get("business_permissions"):
        capability_ids, capability_sources, capability_gaps = _capability_candidates(
            db,
            rule["business_permissions"],
            rule["responsibility_scope"],
            context,
            publish,
        )
        selections.append(set(capability_ids))
        sources.extend(capability_sources)
    ids = sorted(set.intersection(*selections))
    if len(ids) > 50:
        raise DomainError("ASSIGNMENT_BLOCKED", "节点候选人超过50位，请缩小人员范围")
    details = {"candidate_basis_count": len(ids), "gaps": []}
    if explain and basis_selections:
        basis_ids = set.intersection(*basis_selections)
        details["candidate_basis_count"] = len(basis_ids)
        for user_id in sorted(basis_ids - set(ids)):
            person = db.get(models.User, user_id)
            reasons = capability_gaps.get(user_id, [])
            if not person or not person.active:
                reasons = [{
                    "permission": None,
                    "code": "ACCOUNT_INACTIVE",
                    "message": "账号不存在或已停用",
                }]
            if reasons:
                details["gaps"].append({"user_id": user_id, "reasons": reasons})
    return ids, sources, details


def resolve_users(db, node, context=None, publish=False):
    ids, sources, _ = _resolve_users(db, node, context, publish)
    return ids, sources


def resolve_users_with_gaps(db, node, context=None):
    return _resolve_users(db, node, context, False, True)


def check_publish(db, config):
    models = host_ports().models
    for node in config["nodes"]:
        ids, _ = resolve_users(db, node, publish=True)
        if not ids or any(
            not (user := db.get(models.User, user_id)) or not user.active
            for user_id in ids
        ):
            raise DomainError(
                "ASSIGNMENT_BLOCKED", "节点没有有效人员或包含停用人员，请维护人员规则"
            )
        if node["mode"] == "QUORUM" and len(ids) < node["required_approvals"]:
            raise DomainError(
                "ASSIGNMENT_BLOCKED", "比例会签候选人数少于所需通过票数，请调整人员或票数"
            )
        policy = node.get("add_sign_policy")
        if policy and any(
            not (user := db.get(models.User, user_id)) or not user.active
            for user_id in policy["users"]
        ):
            raise DomainError(
                "ASSIGNMENT_BLOCKED", "加签人员池包含不存在或已停用的人员，请重新维护"
            )
        sla = node.get("sla", {})
        timed_recipients = set(sla.get("cc_user_ids", [])) | set(sla.get("escalation_user_ids", []))
        if any(
            not (user := db.get(models.User, user_id)) or not user.active
            for user_id in timed_recipients
        ):
            raise DomainError(
                "ASSIGNMENT_BLOCKED", "超时抄送或升级人员包含不存在、已停用的账号"
            )
