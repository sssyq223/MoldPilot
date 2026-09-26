"""管理员模型目录管理与检测。普通聊天仅使用非敏感模型引用。"""
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from . import model_catalog as catalog, model_discovery
from .errors import DomainError
from .security import current_user


class _SafeModelRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request: Request):
            # 限制凭据请求体，并避免 Pydantic 默认错误把原始 Key 回显给前端。
            if request.method in {'POST', 'PUT', 'DELETE'}:
                chunks, size = [], 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 64 * 1024:
                        raise DomainError('MODEL_INPUT_TOO_LARGE', '模型配置请求过大', 413)
                    chunks.append(chunk)
                request._body = b''.join(chunks)
            try:
                return await original(request)
            except RequestValidationError:
                raise DomainError('MODEL_INPUT_INVALID', '模型配置参数格式不正确', 422) from None
        return handler


def _admin(user=Depends(current_user)):
    if not user.super_admin:
        raise DomainError('FORBIDDEN', '需要超级管理员', 403)
    return user


router = APIRouter(prefix='/api', dependencies=[Depends(_admin)], route_class=_SafeModelRoute)


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ConnectionPatch(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    protocol: Literal['company', 'ollama'] | None = None
    enabled: bool | None = None
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=4000)
    clear_api_key: bool = False
    trusted_http_origin: str | None = Field(default=None, max_length=500)
    proxy_url: str | None = Field(default=None, max_length=500)
    tls_max_version: Literal['auto', '1.2'] | None = None
    tls_key_exchange: Literal['auto', 'x25519'] | None = None
    connect_timeout: float | None = Field(default=None, gt=0, le=20)
    read_timeout: float | None = Field(default=None, gt=0, le=120)


class ModelPatch(_Strict):
    provider_id: str | None = Field(default=None, min_length=1, max_length=80)
    model: str | None = Field(default=None, min_length=1, max_length=160)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    enabled: bool | None = None
    max_output_tokens: int | None = Field(default=None, ge=256, le=8192)
    context_window: int | None = Field(default=None, ge=4096, le=2_000_000)
    max_turns: int | None = Field(default=None, ge=1, le=30)
    reasoning_policy: Literal['default', 'glm', 'openai', 'ollama', 'ollama-levels'] | None = None
    default_reasoning_effort: str | None = Field(default=None, max_length=20)


class RevisionInput(_Strict):
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')


class ProviderInput(RevisionInput):
    connection: ConnectionPatch


class ModelInput(RevisionInput):
    model: ModelPatch


class ModelBatchInput(RevisionInput):
    models: list[ModelPatch] = Field(min_length=1, max_length=100)


class DefaultModelInput(RevisionInput):
    model_id: str = Field(min_length=1, max_length=80)


class DiscoveryInput(_Strict):
    provider_id: str | None = Field(default=None, min_length=1, max_length=80)
    revision: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    connection: ConnectionPatch = Field(default_factory=ConnectionPatch)


def _invoke(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ValueError as error:
        code = str(error)
        codes = {
            'MODEL_CONFIG_INVALID': ('模型配置格式无效，未覆盖原配置', 400),
            'MODEL_CONFIG_CONFLICT': ('配置已被修改，请刷新后重试', 409),
            'MODEL_CONFIG_BUSY': ('配置正在保存，请稍后重试', 409),
            'MODEL_PROVIDER_NOT_FOUND': ('模型供应商不存在', 404),
            'MODEL_NOT_FOUND': ('模型不存在', 404),
            'MODEL_DISABLED': ('模型或供应商已停用', 409),
            'MODEL_ALREADY_EXISTS': ('该供应商下已添加此模型', 409),
            'MODEL_PROVIDER_INVALID': ('供应商连接配置无效', 400),
            'MODEL_INVALID': ('模型参数无效', 400),
            'MODEL_REASONING_UNSUPPORTED': ('此模型或接口协议不支持所选思考档位', 400),
            'MODEL_DESTINATION_REQUIRES_KEY': ('地址已变化，请重新输入 Key 或明确清空原 Key', 409),
            'DOCUMENT_MODEL_IN_USE': ('文档识别正在引用此模型，不能移除、停用或改变模型身份', 409),
            'DEFAULT_MODEL_IN_USE': ('请先明确选择其他默认模型', 409),
        }
        if code not in codes:
            code = 'MODEL_CONFIG_INVALID'
        message, status = codes[code]
        raise DomainError(code, message, status) from None


@router.get('/model-catalog')
def get_catalog():
    return _invoke(catalog.public_catalog)


@router.post('/model-providers/discover')
def discover(data: DiscoveryInput):
    return model_discovery.discover_models(data.model_dump(exclude_unset=True))


@router.post('/model-providers')
def add_provider(data: ProviderInput):
    return _invoke(catalog.save_provider, data.connection.model_dump(exclude_unset=True), expected_revision=data.revision)


@router.put('/model-providers/{provider_id}')
def update_provider(provider_id: str, data: ProviderInput):
    return _invoke(catalog.save_provider, data.connection.model_dump(exclude_unset=True),
                   provider_id=provider_id, expected_revision=data.revision)


@router.delete('/model-providers/{provider_id}')
def remove_provider(provider_id: str, data: RevisionInput):
    return _invoke(catalog.delete_provider, provider_id, expected_revision=data.revision)


@router.post('/catalog-models')
def add_model(data: ModelInput):
    return _invoke(catalog.save_model, data.model.model_dump(exclude_unset=True), expected_revision=data.revision)


@router.post('/catalog-models/batch')
def add_models(data: ModelBatchInput):
    return _invoke(catalog.save_models, [model.model_dump(exclude_unset=True) for model in data.models],
                   expected_revision=data.revision)


@router.put('/model-catalog/default')
def set_default(data: DefaultModelInput):
    return _invoke(catalog.set_default_model, data.model_id, expected_revision=data.revision)


@router.put('/catalog-models/{model_id}')
def update_model(model_id: str, data: ModelInput):
    return _invoke(catalog.save_model, data.model.model_dump(exclude_unset=True),
                   model_id=model_id, expected_revision=data.revision)


@router.delete('/catalog-models/{model_id}')
def remove_model(model_id: str, data: RevisionInput):
    return _invoke(catalog.delete_model, model_id, expected_revision=data.revision)
