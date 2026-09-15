from fastapi import APIRouter,Depends
from pydantic import Field
from sqlalchemy import select
from .models import WorkflowCategory
from .db import get_db
from .security import current_user
from .schemas import StrictModel
from .authorization import require
from .errors import DomainError
from .events import record

router=APIRouter(prefix='/api/workflow-categories')
class CategoryInput(StrictModel):
    name:str=Field(min_length=1,max_length=100)
class CategoryUpdate(CategoryInput):
    active:bool=True
    expected_version:int=Field(ge=1)
    reason:str=Field(min_length=1,max_length=500)
def data(c):return {'id':c.id,'name':c.name,'active':c.active,'version':c.version}

@router.get('')
def listing(user=Depends(current_user),db=Depends(get_db)):
    require(db,user,'workflow.design')
    return [data(c) for c in db.scalars(select(WorkflowCategory).order_by(WorkflowCategory.name))]

@router.post('')
def create(body:CategoryInput,user=Depends(current_user),db=Depends(get_db)):
    require(db,user,'workflow.design')
    if not body.name.strip():raise DomainError('INVALID_INPUT','类别名称不能为空')
    c=WorkflowCategory(name=body.name.strip());db.add(c);db.flush()
    record(db,user,'workflow.category.created',c.id,{'name':c.name});db.commit();return data(c)

@router.put('/{category_id}')
def update(category_id:str,body:CategoryUpdate,user=Depends(current_user),db=Depends(get_db)):
    require(db,user,'workflow.design')
    c=db.scalar(select(WorkflowCategory).where(WorkflowCategory.id==category_id).with_for_update())
    if not c:raise DomainError('NOT_FOUND','流程类别不存在',404)
    if c.version!=body.expected_version:raise DomainError('VERSION_CONFLICT','类别已变化，请刷新',409)
    if not body.name.strip() or not body.reason.strip():raise DomainError('INVALID_INPUT','名称与原因不能为空')
    old=data(c);c.name=body.name.strip();c.active=body.active;c.version+=1
    record(db,user,'workflow.category.changed',c.id,{'before':old,'after':data(c),'reason':body.reason})
    db.commit();return data(c)

def require_category(db,config,category_id):
    if category_id is None and config.get('business_type')!='generic':return
    c=db.get(WorkflowCategory,category_id) if category_id else None
    if not c or not c.active:raise DomainError('CATEGORY_REQUIRED','请选择已启用的流程类别')
