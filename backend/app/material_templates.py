from copy import deepcopy
from fastapi import APIRouter,Depends,Query
from pydantic import Field
from sqlalchemy import select,func,text
from .models import MaterialTemplate,MaterialTemplateXlsxMapping
from .schemas import StrictModel
from .db import get_db
from .security import current_user
from .authorization import require
from .errors import DomainError
from .events import record
from .material_rules import validate_contract
from .bpm import content_hash

router=APIRouter(prefix='/api/material-templates')
class TemplateInput(StrictModel):
    template_key:str=Field(pattern=r'^[a-z][a-z0-9_]{2,79}$')
    name:str=Field(min_length=1,max_length=150)
    contract:dict
class TemplateEdit(StrictModel):
    name:str=Field(min_length=1,max_length=150)
    contract:dict
    expected_hash:str=Field(min_length=64,max_length=64)
class XlsxMappingInput(StrictModel):
    name:str=Field(min_length=1,max_length=150)
    mapping:dict
    expected_template_hash:str|None=Field(default=None,min_length=64,max_length=64)
class XlsxPreviewInput(StrictModel):
    file_id:str=Field(min_length=1,max_length=36)
    mapping:dict|None=None
    mapping_id:str|None=Field(default=None,min_length=1,max_length=36)

def material_data(t):
    return {'id':t.id,'template_key':t.template_key,'name':t.name,'version':t.version,'status':t.status,
            'contract':t.contract,'package_hash':t.package_hash,
            'edit_hash':content_hash({'name':t.name,'contract':t.contract}),'created_at':t.created_at}

def mapping_data(row):
    return {'id':row.id,'template_id':row.template_id,'version':row.version,'name':row.name,'mapping':row.mapping,
            'mapping_hash':row.mapping_hash,'created_by':row.created_by,'created_at':row.created_at}

def latest_mapping(db,template_id,mapping_id=None):
    if mapping_id:
        row=db.get(MaterialTemplateXlsxMapping,mapping_id)
        if not row or row.template_id!=template_id:raise DomainError('NOT_FOUND','Excel 映射版本不存在',404)
        return row
    return db.scalar(select(MaterialTemplateXlsxMapping).where(MaterialTemplateXlsxMapping.template_id==template_id)
        .order_by(MaterialTemplateXlsxMapping.version.desc()).limit(1))

def valid(name,contract):
    if not name.strip():raise DomainError('INVALID_INPUT','资料模板名称不能为空')
    fields,tables=validate_contract(contract)
    if not fields and not tables:raise DomainError('INVALID_INPUT','资料模板至少需要一个字段或明细表')

@router.get('')
def listing(offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=100),template_key:str|None=Query(None,max_length=80),user=Depends(current_user),db=Depends(get_db)):
    require(db,user,'workflow.design')
    q=select(MaterialTemplate)
    if template_key:q=q.where(MaterialTemplate.template_key==template_key)
    return [material_data(t) for t in db.scalars(q.order_by(MaterialTemplate.template_key,MaterialTemplate.version.desc()).offset(offset).limit(limit))]

@router.post('')
def create(body:TemplateInput,user=Depends(current_user),db=Depends(get_db)):
    require(db,user,'workflow.design');valid(body.name,body.contract)
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'material-template:'+body.template_key})
    version=(db.scalar(select(func.max(MaterialTemplate.version)).where(MaterialTemplate.template_key==body.template_key)) or 0)+1
    t=MaterialTemplate(template_key=body.template_key,name=body.name.strip(),version=version,contract=body.contract)
    db.add(t);db.flush();record(db,user,'material.template.created',t.id,{'version':version});db.commit();return material_data(t)

@router.put('/{template_id}')
def edit(template_id:str,body:TemplateEdit,user=Depends(current_user),db=Depends(get_db)):
    require(db,user,'workflow.design')
    t=db.scalar(select(MaterialTemplate).where(MaterialTemplate.id==template_id).with_for_update())
    if not t:raise DomainError('NOT_FOUND','资料模板不存在',404)
    if t.status!='DRAFT':raise DomainError('PUBLISHED_IMMUTABLE','已发布资料模板不能覆盖，请另存新版本',409)
    if body.expected_hash!=material_data(t)['edit_hash']:raise DomainError('VERSION_CONFLICT','资料模板已变化，请刷新',409)
    valid(body.name,body.contract);t.name=body.name.strip();t.contract=body.contract
    record(db,user,'material.template.updated',t.id,{'previous_hash':body.expected_hash});db.commit();return material_data(t)

@router.post('/{template_id}/publish')
def publish(template_id:str,user=Depends(current_user),db=Depends(get_db)):
    require(db,user,'workflow.publish')
    t=db.scalar(select(MaterialTemplate).where(MaterialTemplate.id==template_id).with_for_update())
    if not t:raise DomainError('NOT_FOUND','资料模板不存在',404)
    if t.status=='PUBLISHED':return material_data(t)
    valid(t.name,t.contract)
    t.package_hash=content_hash({'template_key':t.template_key,'version':t.version,'contract':t.contract})
    t.status='PUBLISHED';record(db,user,'material.template.published',t.id,{'hash':t.package_hash});db.commit();return material_data(t)

@router.get('/{template_id}/xlsx-mappings')
def xlsx_mappings(template_id:str,user=Depends(current_user),db=Depends(get_db)):
    require(db,user,'workflow.design')
    t=db.get(MaterialTemplate,template_id)
    if not t:raise DomainError('NOT_FOUND','资料模板不存在',404)
    return [mapping_data(row) for row in db.scalars(select(MaterialTemplateXlsxMapping)
        .where(MaterialTemplateXlsxMapping.template_id==template_id).order_by(MaterialTemplateXlsxMapping.version.desc()))]

@router.post('/{template_id}/xlsx-mappings')
def create_xlsx_mapping(template_id:str,body:XlsxMappingInput,user=Depends(current_user),db=Depends(get_db)):
    from .material_xlsx import validate_xlsx_mapping
    require(db,user,'workflow.design')
    t=db.scalar(select(MaterialTemplate).where(MaterialTemplate.id==template_id).with_for_update())
    if not t:raise DomainError('NOT_FOUND','资料模板不存在',404)
    if t.status!='PUBLISHED':raise DomainError('MATERIAL_TEMPLATE_REQUIRED','只能为已发布资料模板保存 Excel 映射版本')
    if body.expected_template_hash and body.expected_template_hash!=t.package_hash:
        raise DomainError('VERSION_CONFLICT','资料模板版本已变化，请刷新后重新保存映射',409)
    validate_xlsx_mapping(t.contract,body.mapping)
    version=(db.scalar(select(func.max(MaterialTemplateXlsxMapping.version)).where(MaterialTemplateXlsxMapping.template_id==template_id)) or 0)+1
    row=MaterialTemplateXlsxMapping(template_id=template_id,version=version,name=body.name.strip(),mapping=body.mapping,
        mapping_hash=content_hash({'template_id':template_id,'template_hash':t.package_hash,'version':version,'mapping':body.mapping}),
        created_by=user.id)
    db.add(row);db.flush();record(db,user,'material.xlsx_mapping.created',row.id,
        {'template_id':template_id,'template_version':t.version,'version':version,'mapping_hash':row.mapping_hash})
    db.commit();return mapping_data(row)

@router.post('/{template_id}/xlsx-preview')
def xlsx_preview(template_id:str,body:XlsxPreviewInput,user=Depends(current_user),db=Depends(get_db)):
    from . import files,object_storage
    from .material_xlsx import preview_xlsx
    require(db,user,'file.upload')
    t=db.get(MaterialTemplate,template_id)
    if not t:raise DomainError('NOT_FOUND','资料模板不存在',404)
    if t.status!='PUBLISHED':require(db,user,'workflow.design')
    blob=files.load(db,user,body.file_id)
    if blob.media_type!='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
        raise DomainError('FILE_TYPE_UNSUPPORTED','资料解析预览当前只支持 XLSX 原件')
    selected_mapping=None
    mapping=body.mapping
    if mapping is None:
        selected_mapping=latest_mapping(db,t.id,body.mapping_id)
        if not selected_mapping:raise DomainError('XLSX_MAPPING_REQUIRED','请先选择或保存 Excel 列映射版本',409)
        mapping=selected_mapping.mapping
    elif body.mapping_id:
        raise DomainError('INVALID_INPUT','不能同时传入临时映射和映射版本')
    preview=preview_xlsx(object_storage.read(blob),t.contract,mapping)
    record(db,user,'material.xlsx.previewed',blob.id,{'template_id':t.id,'template_key':t.template_key,
        'template_version':t.version,'mapping_id':selected_mapping.id if selected_mapping else None,
        'mapping_version':selected_mapping.version if selected_mapping else None,
        'status':preview['status'],'issue_count':len(preview['issues'])})
    db.commit()
    return {**preview,'template':{'id':t.id,'template_key':t.template_key,'version':t.version,'package_hash':t.package_hash},
            'mapping':mapping_data(selected_mapping) if selected_mapping else {'id':None,'version':None,'name':'临时映射','mapping_hash':content_hash(mapping)},
            'file':files.metadata(blob),'limitations':['解析结果仅供人工核对，尚未绑定业务对象或审批实例','不会执行宏、外部链接或公式；含公式或缺列的数据必须人工处理','本接口不解除正式提交的 MATERIALS_NOT_BOUND 门禁']}

def bind_contract(db,config,template_id):
    if not template_id:return config
    t=db.get(MaterialTemplate,template_id)
    if not t or t.status!='PUBLISHED':raise DomainError('MATERIAL_TEMPLATE_REQUIRED','只能引用已发布的资料模板版本')
    if 'material_contract' in config and config['material_contract']!=t.contract:
        raise DomainError('MATERIAL_CONTRACT_MISMATCH','资料字段与所选资料模板版本不一致')
    return {**config,'material_contract':deepcopy(t.contract)}
