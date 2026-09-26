"""宿主固定任务模型身份与预算；凭据始终在服务器端实时解析。"""
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

import httpx
from sqlalchemy import select
from agent_core.model_adapter import ModelError
from agent_core.model_capabilities import reasoning_parameters
from . import config, models as m
from .errors import DomainError
from .model_catalog import read_catalog, flatten_catalog, public_catalog


def _lookup(document, profile_id):
    model = next((item for item in document['models'] if item['id'] == profile_id), None)
    provider = next((item for item in document['providers'] if model and item['id'] == model['provider_id']), None)
    if not model or not provider:
        raise ModelError('MODEL_PROFILE_NOT_FOUND')
    if not model['enabled'] or not provider['enabled']:
        raise ModelError('MODEL_DISABLED')
    return model, provider


def _route_hash(model, provider):
    proxy = httpx.URL(provider['proxy_url']) if provider['proxy_url'] else None
    identity = {'provider_id': provider['id'], 'protocol': provider['protocol'],
                'base_url': str(httpx.URL(provider['base_url'])).rstrip('/'), 'model': model['model'],
                'trusted_http_origin': provider['trusted_http_origin'],
                'proxy_url': str(proxy.copy_with(username='',password='')) if proxy else '',
                'tls_max_version': provider['tls_max_version'], 'tls_key_exchange': provider['tls_key_exchange']}
    return sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()


def select_model(user, profile_id=None, effort=None, *, default_enabled=True):
    try:
        document = read_catalog()
        selected_id = profile_id or document['default_model_id']
        if not user.super_admin and profile_id and profile_id != document['default_model_id']:
            raise DomainError('FORBIDDEN', '当前账号不能切换模型或思考档位', 403)
        if selected_id is None or (not profile_id and not default_enabled):
            return None
        model, provider = _lookup(document, selected_id)
        selected_effort = model['default_reasoning_effort'] if effort is None else effort
        if not user.super_admin and (selected_id != document['default_model_id']
                                     or selected_effort != model['default_reasoning_effort']):
            raise DomainError('FORBIDDEN', '当前账号不能切换模型或思考档位', 403)
        reasoning_parameters(provider['protocol'], model['model'], model['reasoning_policy'], selected_effort)
        return {'version': 1, 'profile_id': model['id'], 'provider_id': provider['id'],
                'provider_name': provider['name'], 'model_name': model['name'], 'model': model['model'],
                'route_hash': _route_hash(model,provider), 'reasoning_policy': model['reasoning_policy'],
                'reasoning_effort': selected_effort, 'max_output_tokens': model['max_output_tokens'],
                'context_window': model['context_window'], 'max_turns': model['max_turns'],
                'connect_timeout': provider['connect_timeout'], 'read_timeout': provider['read_timeout']}
    except ModelError as exc:
        raise DomainError(str(exc), '所选模型不存在或已停用，请重新选择', 409) from None
    except ValueError as exc:
        code = 'MODEL_REASONING_UNSUPPORTED' if str(exc)=='MODEL_REASONING_UNSUPPORTED' else 'MODEL_CONFIG_INVALID'
        raise DomainError(code, '模型配置或思考档位不受支持', 400) from None


def selection_for_run(db, user, run):
    events = db.scalars(select(m.AuditEvent).where(m.AuditEvent.resource_id==run.id,
        m.AuditEvent.action.in_(['agent.run.created','agent.run.model_selected'])).order_by(m.AuditEvent.created_at.desc()))
    for event in events:
        value = (event.detail or {}).get('model_selection')
        if isinstance(value,dict) and value.get('version')==1:
            return deepcopy(value)
    previous = (run.checkpoint or {}).get('model_selection')
    if isinstance(previous,dict) and previous.get('version')==1:
        # 审计保留策略不应改变任务。此字段在 checkpoint API 中由宿主单独保护。
        return deepcopy(previous)
    value = select_model(user)
    if value is None:
        raise ModelError('MODEL_PROFILE_NOT_FOUND')
    db.add(m.AuditEvent(user_id=user.id, action='agent.run.model_selected', resource_id=run.id,
                       detail={'source':'legacy_first_claim','model_selection':value}))
    return value


def runtime_for_selection(selection):
    try:
        if not isinstance(selection,dict) or selection.get('version')!=1:
            raise ModelError('MODEL_SELECTION_INVALID')
        document = read_catalog()
        model, provider = _lookup(document, selection['profile_id'])
        if (selection['route_hash'] != _route_hash(model,provider)
                or selection['model'] != model['model'] or selection['provider_id'] != provider['id']
                or selection['reasoning_policy'] != model['reasoning_policy']):
            raise ModelError('MODEL_CONFIGURATION_CHANGED')
        reasoning_parameters(provider['protocol'],model['model'],selection['reasoning_policy'],selection['reasoning_effort'])
        profile = next(p for p in flatten_catalog(document)['profiles'] if p['id']==model['id'])
        values = config._settings_values(profile)
        for source,target in {'max_output_tokens':'llm_max_output_tokens','context_window':'llm_context_window',
                              'max_turns':'llm_max_turns','connect_timeout':'llm_connect_timeout',
                              'read_timeout':'llm_read_timeout','reasoning_policy':'llm_reasoning_policy',
                              'reasoning_effort':'llm_reasoning_effort'}.items():
            values[target] = selection[source]
        values['config_version'] = sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()
        return SimpleNamespace(**values)
    except (ValueError,KeyError,TypeError,StopIteration):
        raise ModelError('MODEL_SELECTION_INVALID') from None


def chat_catalog(user):
    data = public_catalog()
    enabled_providers = {p['id']:p for p in data['providers'] if p['enabled']}
    models = [m for m in data['models'] if m['enabled'] and m['provider_id'] in enabled_providers
              and (user.super_admin or m['id']==data['default_model_id'])]
    return {'default_model_id': data['default_model_id'], 'can_select': user.super_admin,
            'providers': [{'id':p['id'],'name':p['name']} for p in enabled_providers.values()
                          if any(m['provider_id']==p['id'] for m in models)],
            'models': models}
