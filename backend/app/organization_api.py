from typing import Literal
from fastapi import APIRouter,Depends
from pydantic import Field
from sqlalchemy import select,delete
from .db import get_db
from .models import AssignmentGroup,AssignmentMember,User
from .schemas import StrictModel
from .security import current_user,public_user
from .errors import DomainError
from .events import record
from . import authorization as auth

router=APIRouter(prefix='/api')

class MemberInput(StrictModel):
    user_id:str=Field(min_length=1,max_length=36)
    is_head:bool=False

class GroupInput(StrictModel):
    kind:Literal['ROLE','DEPARTMENT']
    name:str=Field(min_length=1,max_length=100)
    members:list[MemberInput]=Field(default_factory=list,max_length=500)
    active:bool=True
    reason:str=Field(min_length=1,max_length=500)

class GroupUpdate(GroupInput):
    expected_version:int=Field(ge=1)

def admin(user):
    if not user.super_admin:raise DomainError('FORBIDDEN','部门和角色由超级管理员维护',403)

def group_data(db,g):
    members=[{'user_id':m.user_id,'is_head':m.is_head} for m in db.scalars(select(AssignmentMember).where(AssignmentMember.group_id==g.id).order_by(AssignmentMember.user_id))]
    return {'id':g.id,'kind':g.kind,'name':g.name,'active':g.active,'version':g.version,'members':members}

@router.get('/organization/groups')
def groups(user=Depends(current_user),db=Depends(get_db)):
    admin(user)
    return [group_data(db,g) for g in db.scalars(select(AssignmentGroup).order_by(AssignmentGroup.kind,AssignmentGroup.name))]

def save(db,user,g,data):
    if not data.name.strip() or not data.reason.strip():raise DomainError('INVALID_INPUT','名称和变更原因不能为空')
    ids=[m.user_id for m in data.members]
    if len(ids)!=len(set(ids)) or (data.kind=='ROLE' and any(m.is_head for m in data.members)):
        raise DomainError('INVALID_INPUT','成员不能重复；角色不设置部门负责人')
    old=set(db.scalars(select(AssignmentMember.user_id).where(AssignmentMember.group_id==g.id)))
    people=list(db.scalars(select(User).where(User.id.in_(old|set(ids))).order_by(User.id).with_for_update()))
    if set(ids)-{u.id for u in people} or any(not u.active and u.id in ids for u in people):
        raise DomainError('ASSIGNMENT_BLOCKED','成员不存在或已停用')
    before=group_data(db,g)
    g.name=data.name.strip();g.active=data.active;g.version+=1
    db.execute(delete(AssignmentMember).where(AssignmentMember.group_id==g.id))
    if data.kind=='DEPARTMENT':
        member_ids=set(ids)
        if member_ids:
            other_departments=select(AssignmentGroup.id).where(AssignmentGroup.kind=='DEPARTMENT',AssignmentGroup.id!=g.id)
            db.execute(delete(AssignmentMember).where(AssignmentMember.group_id.in_(other_departments),AssignmentMember.user_id.in_(member_ids)))
        for person in people:
            if person.id in member_ids:
                person.department=g.name
            elif person.id in old and person.department==before['name']:
                person.department=''
    db.add_all(AssignmentMember(group_id=g.id,user_id=m.user_id,is_head=m.is_head) for m in data.members)
    # Member/selector changes invalidate cached authorization context, but never add a Grant.
    for person in people:person.security_version+=1
    db.flush()
    after=group_data(db,g)
    record(db,user,'organization.group.changed',g.id,{'reason':data.reason,'before':before,'after':after},[u.id for u in people])
    db.commit()
    return after

@router.post('/organization/groups')
def create(data:GroupInput,user=Depends(current_user),db=Depends(get_db)):
    admin(user)
    g=AssignmentGroup(kind=data.kind,name=data.name.strip(),version=0)
    db.add(g);db.flush()
    return save(db,user,g,data)

@router.put('/organization/groups/{group_id}')
def update(group_id:str,data:GroupUpdate,user=Depends(current_user),db=Depends(get_db)):
    admin(user)
    g=db.scalar(select(AssignmentGroup).where(AssignmentGroup.id==group_id).with_for_update())
    if not g:raise DomainError('NOT_FOUND','角色或部门不存在',404)
    if g.version!=data.expected_version:raise DomainError('VERSION_CONFLICT','成员配置已变化，请刷新后重试',409)
    if g.kind!=data.kind:raise DomainError('INVALID_INPUT','已创建的组织类型不能修改')
    return save(db,user,g,data)

@router.get('/workflows/assignment-catalog')
def catalog(user=Depends(current_user),db=Depends(get_db)):
    auth.require(db,user,'workflow.design')
    # Designer needs selection metadata, not user administration authority or credentials.
    return {'users':[public_user(u) for u in db.scalars(select(User).order_by(User.display_name))],
            'groups':[group_data(db,g) for g in db.scalars(select(AssignmentGroup).order_by(AssignmentGroup.kind,AssignmentGroup.name))]}
