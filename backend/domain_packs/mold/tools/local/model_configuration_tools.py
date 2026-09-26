"""本地模型目录查询与管理员 Proposal Tool。"""
from uuid import uuid4

from pydantic import Field, ValidationError, field_validator
import httpx

from agent_core.host_ports import host_ports
from agent_core.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel


DISCOVERY_KEY = "query_model_provider_directory"


catalog = host_ports().model_catalog
model_discovery = host_ports().model_discovery


class CatalogQueryInput(StrictModel):
    pass


class ProviderDirectoryQueryInput(StrictModel):
    provider_id: str = Field(min_length=1, max_length=80)
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class ProviderSaveInput(StrictModel):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider_id: str | None = Field(default=None, min_length=1, max_length=80)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    protocol: str | None = Field(default=None, pattern=r"^(company|ollama)$")
    enabled: bool | None = None
    base_url: str | None = Field(default=None, max_length=500)
    clear_api_key: bool = False
    trusted_http_origin: str | None = Field(default=None, max_length=500)
    proxy_url: str | None = Field(default=None, max_length=500)
    tls_max_version: str | None = Field(default=None, pattern=r"^(auto|1\.2)$")
    tls_key_exchange: str | None = Field(default=None, pattern=r"^(auto|x25519)$")
    connect_timeout: float | None = Field(default=None, gt=0, le=20)
    read_timeout: float | None = Field(default=None, gt=0, le=120)

    @field_validator('base_url', 'proxy_url', 'trusted_http_origin')
    @classmethod
    def no_embedded_credentials(cls, value):
        if value:
            try:
                url = httpx.URL(value)
                if url.username or url.password or url.query or url.fragment:
                    raise ValueError('连接地址不能包含凭据、查询串或片段')
            except httpx.InvalidURL:
                raise ValueError('连接地址无效') from None
        return value


class ModelSaveInput(StrictModel):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_id: str | None = Field(default=None, min_length=1, max_length=80)
    provider_id: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    enabled: bool = True
    max_output_tokens: int = Field(default=2048, ge=256, le=8192)
    context_window: int = Field(default=32768, ge=4096, le=2_000_000)
    max_turns: int = Field(default=12, ge=1, le=30)
    reasoning_policy: str = Field(default="default", pattern=r"^(default|glm|openai|ollama|ollama-levels)$")
    default_reasoning_effort: str = Field(default="", max_length=20)


class DefaultModelInput(StrictModel):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_id: str = Field(min_length=1, max_length=80)


INPUTS = {
    "query_model_configuration": CatalogQueryInput,
    DISCOVERY_KEY: ProviderDirectoryQueryInput,
    "prepare_model_provider_save": ProviderSaveInput,
    "prepare_model_save": ModelSaveInput,
    "prepare_model_default": DefaultModelInput,
}


def schema(key):
    return INPUTS[key].model_json_schema()


def _admin(user):
    if not user or not user.super_admin or not getattr(user, 'active', True):
        raise DomainError("FORBIDDEN", "只有超级管理员可以管理本地模型配置", 403)


def _parse(key, arguments):
    try:
        return INPUTS[key].model_validate(arguments or {})
    except (ValidationError, KeyError) as error:
        message = error.errors()[0]["msg"] if hasattr(error, "errors") else "参数无效"
        raise DomainError("INVALID_TOOL_INPUT", f"本地模型配置参数无效：{message}") from None


def _proposal(key, data, db=None, user=None, run=None):
    return {
        "data": [],
        "source": "agent_proposal",
        "proposal": {
            "tool": key,
            "action": key.removeprefix("prepare_"),
            "input": data.model_dump(mode="json"),
            "display": data.model_dump(mode="json"),
            "operation_id": str(uuid4()),
            "security_version": user.security_version if user else None,
            "authorization_hash": host_ports().fingerprint(db, user) if db and user else None,
            "requires_approval": False,
            "confirmation_policy": host_ports().proposal_confirmation_policy(run, requires_approval=False),
        },
        "limitations": ["仅准备模型配置建议，尚未写入本机配置；API Key 不由此 Tool 接收或回显。"],
    }


def _preview(data):
    # 只读核对目录版本；实际写入仍由 catalog 的锁内校验和占用门禁执行。
    state = catalog.public_catalog()
    if data.revision != state['revision']:
        raise DomainError('MODEL_CONFIG_CONFLICT', '配置已变化，请重新查询并准备', 409)
    return state


def execute_tool(db, user, key, arguments, run=None):
    _admin(user)
    data = _parse(key, arguments)
    if key == "query_model_configuration":
        return {"data": [catalog.public_catalog()], "source": "agent_model_catalog"}
    if key == DISCOVERY_KEY:
        data = _parse(key, arguments)
        try:
            result = model_discovery.discover_models({
                "provider_id": data.provider_id,
                "revision": data.revision,
                "connection": {},
            })
        except ValueError as error:
            raise DomainError("MODEL_DISCOVERY_FAILED", "模型目录检测未完成", 502) from error
        return {"data": [result], "source": "agent_model_provider", "limitations": [
            "目录检测只返回供应商声明的模型名称，不证明模型可调用或具备工具能力。",
        ]}
    _preview(data)
    return _proposal(key, data, db=db, user=user, run=run)


def source(db, user, step_id):
    _admin(user)
    from domain_packs.mold.tool_gateway import available_tools

    step = db.get(host_ports().models.Step, step_id)
    run = db.get(host_ports().models.Run, step.run_id) if step else None
    if not run or run.user_id != user.id or run.status not in {"RUNNING", "SUCCEEDED"}:
        raise DomainError("NOT_FOUND", "模型配置建议不存在或无权访问", 404)
    proposal = step.result.get("proposal") if isinstance(step.result, dict) else None
    if (not proposal or step.tool not in {'prepare_model_provider_save', 'prepare_model_save', 'prepare_model_default'}
            or proposal.get('tool') != step.tool
            or proposal.get('action') != step.tool.removeprefix('prepare_')
            or step.tool not in available_tools(db, user)):
        raise DomainError("TOOL_FORBIDDEN", "模型配置能力不可用", 403)
    if (run.security_version != user.security_version
            or proposal.get('security_version') != user.security_version
            or proposal.get('authorization_hash') != host_ports().fingerprint(db, user)):
        raise DomainError("AUTHORIZATION_CHANGED", "授权已变化，请重新准备操作", 403)
    return proposal


def validate_intent(db, user, payload):
    proposal = source(db, user, payload['step_id'])
    if host_ports().content_hash(proposal) != payload.get('proposal_hash'):
        raise DomainError('CONFIRMATION_INVALID', '模型配置建议内容已变化', 409)
    data = _parse(proposal['tool'], proposal['input'])
    _preview(data)
    return proposal, data


def confirm(db, user, payload):
    proposal, data = validate_intent(db, user, payload)
    try:
        result = _apply(proposal, data)
    except ValueError as error:
        code = str(error)
        if code not in {'MODEL_CONFIG_CONFLICT', 'DOCUMENT_MODEL_IN_USE', 'MODEL_DISABLED',
                        'MODEL_NOT_FOUND', 'MODEL_PROVIDER_NOT_FOUND', 'MODEL_ALREADY_EXISTS',
                        'MODEL_CONFIG_BUSY', 'MODEL_DESTINATION_REQUIRES_KEY'}:
            code = 'MODEL_CONFIG_INVALID'
        raise DomainError(code, '模型配置未保存，请重新核对目录和占用状态', 409) from None
    host_ports().record(db, user, 'model_configuration.confirmed', proposal['operation_id'],
                        {'tool': proposal['tool'], 'before_revision': data.revision, 'after_revision': result['revision']})
    return result


def _apply(proposal, data):
    values = data.model_dump(exclude_none=True)
    revision = values.pop("revision")
    if proposal["tool"] == "prepare_model_provider_save":
        provider_id = values.pop("provider_id", None)
        return catalog.save_provider(values, provider_id=provider_id, expected_revision=revision)
    if proposal["tool"] == "prepare_model_save":
        model_id = values.pop("model_id", None)
        return catalog.save_model(values, model_id=model_id, expected_revision=revision)
    if proposal["tool"] == "prepare_model_default":
        return catalog.set_default_model(values["model_id"], expected_revision=revision)
    raise DomainError("ACTION_UNKNOWN", "本地模型配置动作未登记")
