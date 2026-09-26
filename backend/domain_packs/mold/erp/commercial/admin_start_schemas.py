"""开工草稿允许管理员填写的结构化字段；来源引用不可由输入覆盖。"""
from datetime import date
from pydantic import Field, ValidationError
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.ports.errors import DomainError


class AdminStartMaterial(StrictModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    project_version: int | None = Field(default=None, ge=1)
    project_name: str | None = Field(default=None, max_length=150)
    project_number: str | None = Field(default=None, max_length=80)
    customer_name: str | None = Field(default=None, max_length=150)
    external_order_number: str | None = Field(default=None, max_length=100)
    customer_mold_numbers: list[str] = Field(default_factory=list, max_length=100)
    internal_mold_numbers: list[str] = Field(default_factory=list, max_length=100)
    effective_date: date | None = None
    customer_due_date: date | None = None


def material_values(value):
    try:
        payload = dict(value or {})
        # 兼容旧草稿/旧确认卡；该字段从“系统 ID”迁移为后续匹配用的人工编号。
        if 'internal_mold_numbers' not in payload and 'internal_mold_ids' in payload:
            payload['internal_mold_numbers'] = payload.pop('internal_mold_ids')
        return AdminStartMaterial.model_validate(payload).model_dump(mode='json', exclude_unset=True)
    except ValidationError:
        # 不回显用户提交的资料或原始校验输入。
        raise DomainError('ADMIN_MATERIAL_INVALID', '开工资料字段或类型无效，来源信息不可修改', 400) from None
