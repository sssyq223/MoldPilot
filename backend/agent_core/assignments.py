"""Generic approval assignment resolution through the host model contract."""
from sqlalchemy import select

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


def validate_assignment(node):
    if "assignment" not in node:
        if not valid_ids(node.get("users")):
            raise DomainError("ASSIGNMENT_BLOCKED", "必须配置有效且不重复的审批人员")
        return
    rule = node["assignment"]
    if (
        ("users" in node and node["users"] != [])
        or not isinstance(rule, dict)
        or set(rule) != {"roles", "departments", "department_heads_only"}
        or not valid_ids(rule.get("roles"), True)
        or not valid_ids(rule.get("departments"), True)
        or type(rule.get("department_heads_only")) is not bool
        or not (rule["roles"] or rule["departments"])
        or (rule["department_heads_only"] and not rule["departments"])
    ):
        raise DomainError(
            "INVALID_WORKFLOW",
            "人员规则须选择角色或部门；部门负责人须指定部门，不能混用指定用户",
        )


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


def resolve_users(db, node):
    validate_assignment(node)
    if "assignment" not in node:
        return list(node["users"]), []
    models = host_ports().models
    rule = node["assignment"]
    selections, sources = [], []
    for field, kind in (("roles", "ROLE"), ("departments", "DEPARTMENT")):
        if not rule[field]:
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
        selections.append(set(db.scalars(query)))
        sources.extend({
            "id": group.id,
            "kind": group.kind,
            "name": group.name,
            "version": group.version,
        } for group in groups)
    ids = sorted(set.intersection(*selections))
    if len(ids) > 50:
        raise DomainError("ASSIGNMENT_BLOCKED", "节点候选人超过50位，请缩小人员范围")
    return ids, sources


def check_publish(db, config):
    models = host_ports().models
    for node in config["nodes"]:
        ids, _ = resolve_users(db, node)
        if not ids or any(
            not (user := db.get(models.User, user_id)) or not user.active
            for user_id in ids
        ):
            raise DomainError(
                "ASSIGNMENT_BLOCKED", "节点没有有效人员或包含停用人员，请维护人员规则"
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
