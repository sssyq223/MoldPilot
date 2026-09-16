"""Resolve reusable approval templates against authoritative business context."""
from sqlalchemy import select
from . import models as m
from .errors import DomainError

CATEGORIES = {'raw_material', 'hardware', 'outsource', 'auxiliary', 'office_supply', 'trial_material'}
DESIGN_TYPES = {'NEW_MOLD', 'MOLD_CHANGE'}


def validate_applicability(config):
    scope = config.get('applicability', {})
    if config.get('business_type')=='generic' and scope:
        raise DomainError('INVALID_WORKFLOW_SCOPE','通用模板使用自定义流程类别，不配置固定采购或设计类别')
    if not isinstance(scope, dict) or set(scope) - {'categories', 'design_types'}:
        raise DomainError('INVALID_WORKFLOW_SCOPE', '审批模板适用范围无效')
    for field, values in scope.items():
        allowed = CATEGORIES if field == 'categories' else DESIGN_TYPES
        if not isinstance(values, list) or not values or len(values) > len(allowed) or any(not isinstance(v, str) or v not in allowed for v in values):
            raise DomainError('INVALID_WORKFLOW_SCOPE', '模板类别或设计类型未登记')
        if len(set(values)) != len(values):
            raise DomainError('INVALID_WORKFLOW_SCOPE', '适用范围不能重复')
    if 'design_types' in scope and config['business_type'] != 'design_route':
        raise DomainError('INVALID_WORKFLOW_SCOPE', '新模/改模范围仅用于设计审批')


def context(db, resource):
    if isinstance(resource, m.PurchaseRequest):
        categories = set(db.scalars(select(m.Material.category).join(m.PurchaseLine).where(m.PurchaseLine.request_id == resource.id)))
        return {'business_type': 'purchase_request', 'categories': categories, 'design_type': None}
    categories = {resource.category} if resource.category else set()
    design_type = None
    if resource.kind == 'design_route':
        detail = db.get(m.DesignDetail, resource.id)
        design_type = detail.design_type if detail else None
        categories = set(db.scalars(select(m.Material.category).join(m.DesignItem).where(m.DesignItem.design_id == resource.id)))
    return {'business_type': resource.kind, 'categories': categories, 'design_type': design_type}


def matches(config, values):
    if config['business_type']=='generic':return True
    if config['business_type'] != values['business_type']: return False
    scope = config.get('applicability', {})
    if 'categories' in scope and (not values['categories'] or not values['categories'] <= set(scope['categories'])): return False
    if 'design_types' in scope and values['design_type'] not in scope['design_types']: return False
    return True


def available(db, user, resource):
    from .business import request_access
    request_access(db, user, resource, 'purchase.read')
    request_access(db, user, resource, 'purchase.submit')
    values = context(db, resource)
    definitions = db.scalars(select(m.WorkflowDefinition).where(m.WorkflowDefinition.status == 'PUBLISHED').order_by(m.WorkflowDefinition.process_key, m.WorkflowDefinition.version.desc()))
    candidates = []
    for definition in definitions:
        if definition.category_id:
            category=db.get(m.WorkflowCategory,definition.category_id)
            if not category or not category.active:continue
        if matches(definition.config, values): candidates.append(definition)
    # Explicit user selection: all published versions, categorized by administrator metadata.
    return candidates


def validate_material_review(db, user, definition, material_review_id):
    if definition.config.get('material_contract') is None:
        if material_review_id:
            raise DomainError('MATERIAL_NOT_REQUIRED', '当前流程不需要资料核对包', 400)
        return None
    if not material_review_id:
        raise DomainError('MATERIALS_NOT_BOUND','该流程需要已核对的资料模板数据，请先选择已确认的资料核对包',409)
    review = db.get(m.MaterialReview, material_review_id)
    if not review or review.owner_id != user.id:
        raise DomainError('NOT_FOUND', '资料核对包不存在或无权访问', 404)
    if review.status != 'CONFIRMED' or review.issues:
        raise DomainError('MATERIAL_REVIEW_NOT_CONFIRMED', '资料核对包尚未确认或仍有待核对问题', 409)
    template = db.get(m.MaterialTemplate, review.template_id)
    if not template or template.status != 'PUBLISHED' or template.package_hash != review.template_hash:
        raise DomainError('MATERIAL_TEMPLATE_CHANGED', '资料模板版本已变化，请重新核对资料', 409)
    if definition.material_template_id and review.template_id != definition.material_template_id:
        raise DomainError('MATERIAL_TEMPLATE_MISMATCH', '资料核对包不属于当前审批模板要求的资料版本', 409)
    if template.contract != definition.config.get('material_contract'):
        raise DomainError('MATERIAL_CONTRACT_MISMATCH', '资料核对包结构与当前审批模板不一致', 409)
    return review


def require_template(db, user, resource, definition_id, material_review_id=None):
    definition = next((d for d in available(db, user, resource) if d.id == definition_id), None)
    if not definition:
        raise DomainError('WORKFLOW_MISMATCH', '所选版本未发布、类别已停用或历史模板不适用于当前材料，请重新选择', 409)
    validate_material_review(db, user, definition, material_review_id)
    return definition


def metadata(definition,db=None):
    category=db.get(m.WorkflowCategory,definition.category_id) if db is not None and definition.category_id else None
    return {'id': definition.id, 'name': definition.name, 'version': definition.version,
            'process_key':definition.process_key,'category_id':definition.category_id,
            'category_name':category.name if category else '待整理',
            'business_type': definition.config['business_type'], 'applicability': definition.config.get('applicability', {})}
