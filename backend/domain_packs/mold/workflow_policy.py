"""Workflow vocabulary and rule policy supplied by the mold business pack."""
from domain_packs.mold.erp.core.domain_schemas import CATALOG
from domain_packs.mold.erp.core.rules import evaluate, validate_rule
from domain_packs.mold.erp.core.workflow_selection import available, metadata, validate_applicability
from domain_packs.mold import models as m
from domain_packs.mold.ports.errors import DomainError

WORKFLOW_TYPES = frozenset({"purchase_request", *CATALOG})


def available_workflows(db, user, resource_type, resource_id):
    if resource_type not in {"purchase_request", "business_subject"}:
        raise DomainError("RESOURCE_TYPE_INVALID", "审批业务对象类型无效")
    model = m.PurchaseRequest if resource_type == "purchase_request" else m.BusinessSubject
    resource = db.get(model, resource_id)
    if not resource:
        raise DomainError("NOT_FOUND", "业务对象不存在或无权访问", 404)
    return [metadata(definition, db) for definition in available(db, user, resource)]

__all__ = ["CATALOG", "evaluate", "validate_rule", "validate_applicability", "available_workflows"]
