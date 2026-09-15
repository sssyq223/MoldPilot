"""Approval selectors do not grant permissions. Resolve only when entering a node."""
from sqlalchemy import select
from .models import AssignmentGroup, AssignmentMember, User
from .errors import DomainError


def valid_ids(value, allow_empty=False):
    return (isinstance(value,list) and (allow_empty or bool(value)) and len(value)<=50
            and all(isinstance(v,str) and 1<=len(v)<=36 for v in value)
            and len(value)==len(set(value)))


def validate_assignment(node):
    if 'assignment' not in node:
        if not valid_ids(node.get('users')):
            raise DomainError('ASSIGNMENT_BLOCKED','必须配置有效且不重复的审批人员')
        return
    rule=node['assignment']
    if (('users' in node and node['users']!=[]) or not isinstance(rule,dict)
            or set(rule)!={'roles','departments','department_heads_only'}
            or not valid_ids(rule.get('roles'),True) or not valid_ids(rule.get('departments'),True)
            or type(rule.get('department_heads_only')) is not bool
            or not (rule['roles'] or rule['departments'])
            or (rule['department_heads_only'] and not rule['departments'])):
        raise DomainError('INVALID_WORKFLOW','人员规则须选择角色或部门；部门负责人须指定部门，不能混用指定用户')


def resolve_users(db,node):
    validate_assignment(node)
    if 'assignment' not in node:return list(node['users']),[]
    rule=node['assignment']; selections=[]; sources=[]
    for field,kind in [('roles','ROLE'),('departments','DEPARTMENT')]:
        if not rule[field]:continue
        groups=list(db.scalars(select(AssignmentGroup).where(AssignmentGroup.id.in_(rule[field])).order_by(AssignmentGroup.id).with_for_update(read=True)))
        if len(groups)!=len(rule[field]) or any(g.kind!=kind or not g.active for g in groups):
            raise DomainError('ASSIGNMENT_BLOCKED','人员规则引用的角色或部门不存在、已停用或类型不匹配')
        query=select(AssignmentMember.user_id).where(AssignmentMember.group_id.in_(rule[field]))
        if kind=='DEPARTMENT' and rule['department_heads_only']:query=query.where(AssignmentMember.is_head.is_(True))
        selections.append(set(db.scalars(query)))
        sources.extend({'id':g.id,'kind':g.kind,'name':g.name,'version':g.version} for g in groups)
    ids=sorted(set.intersection(*selections))
    if len(ids)>50:raise DomainError('ASSIGNMENT_BLOCKED','节点候选人超过50位，请缩小人员范围')
    return ids,sources


def check_publish(db,config):
    for node in config['nodes']:
        ids,_=resolve_users(db,node)
        if not ids or any(not (u:=db.get(User,uid)) or not u.active for uid in ids):
            raise DomainError('ASSIGNMENT_BLOCKED','节点没有有效人员或包含停用人员，请维护人员规则')
