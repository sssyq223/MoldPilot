"""供应商连接与模型条目：兼容只读投影，显式保存时才升级本机配置。"""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from ipaddress import ip_address, ip_network
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import config
from agent_core.model_capabilities import reasoning_capability, reasoning_parameters


class Provider(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    name: str = Field(min_length=1, max_length=80)
    protocol: Literal['company', 'ollama'] = 'company'
    enabled: bool = True
    base_url: str = Field(max_length=500)
    api_key: str = Field(default='', max_length=4000)
    trusted_http_origin: str = Field(default='', max_length=500)
    proxy_url: str = Field(default='', max_length=500)
    tls_max_version: Literal['auto', '1.2'] = 'auto'
    tls_key_exchange: Literal['auto', 'x25519'] = 'auto'
    connect_timeout: float = Field(default=10, gt=0, le=20)
    read_timeout: float = Field(default=60, gt=0, le=120)


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    provider_id: str
    model: str = Field(max_length=160)
    name: str = Field(min_length=1, max_length=160)
    enabled: bool = True
    max_output_tokens: int = Field(default=2048, ge=256, le=8192)
    context_window: int = Field(default=32768, ge=4096, le=2_000_000)
    max_turns: int = Field(default=12, ge=1, le=30)
    reasoning_policy: Literal['default', 'glm', 'openai', 'ollama', 'ollama-levels'] = 'default'
    default_reasoning_effort: str = Field(default='', max_length=20)


_WRITE_LOCK = threading.RLock()


@contextmanager
def config_lock():
    """线程及进程均串行化 read-check-write；稳定锁文件不能在退出时删除。"""
    path = config._model_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK, path.with_suffix(path.suffix + '.lock').open('a+b') as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b'\0')
            lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            deadline = time.monotonic() + 5
            while True:
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ValueError('MODEL_CONFIG_BUSY') from None
                    time.sleep(0.02)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _read_raw():
    try:
        raw = json.loads(config._model_config_path().read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise ValueError('MODEL_CONFIG_INVALID') from None
    if not isinstance(raw, dict) or raw.get('version') not in {None, 2, 3}:
        raise ValueError('MODEL_CONFIG_INVALID')
    return raw


def _validate_provider(provider):
    if not provider['enabled'] and not provider['base_url']:
        return  # 保留旧版允许保存的停用草稿，不让无关草稿阻断已配置模型。
    try:
        url = httpx.URL(provider['base_url'])
        if not url.host or url.username or url.password or url.query or url.fragment:
            raise ValueError
        if provider['protocol'] == 'ollama':
            if url.scheme != 'http' or url.host not in {'127.0.0.1', 'localhost', '::1'}:
                raise ValueError
            if provider['api_key'] or provider['proxy_url']:
                raise ValueError
        elif url.scheme == 'http':
            address = ip_address(url.host)
            trusted = httpx.URL(provider['trusted_http_origin'])
            if (not any(address in ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
                    or trusted.scheme != 'http' or trusted.host != url.host or trusted.port != url.port
                    or trusted.path not in {'', '/'} or trusted.username or trusted.password
                    or trusted.query or trusted.fragment or provider['api_key'] or provider['proxy_url']):
                raise ValueError
        elif url.scheme != 'https':
            raise ValueError
        if provider['proxy_url']:
            proxy = httpx.URL(provider['proxy_url'])
            if proxy.scheme not in {'http', 'https', 'socks5', 'socks5h'} or not proxy.host or proxy.query or proxy.fragment:
                raise ValueError
    except (ValueError, httpx.InvalidURL):
        raise ValueError('MODEL_PROVIDER_INVALID') from None


def _validated(document):
    try:
        if set(document) != {'version', 'providers', 'models', 'default_model_id'} or document['version'] != 3:
            raise ValueError
        providers = [Provider.model_validate(p).model_dump() for p in document['providers']]
        models = [CatalogModel.model_validate(m).model_dump() for m in document['models']]
        pids = {p['id'] for p in providers}
        mids = {m['id'] for m in models}
        if len(pids) != len(providers) or len(mids) != len(models):
            raise ValueError
        valid_default = document['default_model_id'] in mids if models else document['default_model_id'] is None
        if not valid_default or any(m['provider_id'] not in pids for m in models):
            raise ValueError
        if len({(m['provider_id'], m['model']) for m in models}) != len(models):
            raise ValueError('MODEL_ALREADY_EXISTS')
        for provider in providers:
            _validate_provider(provider)
        protocols = {p['id']: p['protocol'] for p in providers}
        for model in models:
            if model['enabled'] and not model['model'].strip():
                raise ValueError
            reasoning_parameters(protocols[model['provider_id']], model['model'], model['reasoning_policy'], model['default_reasoning_effort'])
        return {'version': 3, 'providers': providers, 'models': models,
                'default_model_id': document['default_model_id']}
    except (ValidationError, TypeError, KeyError, ValueError) as exc:
        if str(exc) in {'MODEL_ALREADY_EXISTS', 'MODEL_REASONING_UNSUPPORTED'}:
            raise
        raise ValueError('MODEL_CONFIG_INVALID') from None


def read_catalog():
    raw = _read_raw()
    if raw.get('version') == 3:
        return _validated(raw)
    if 'profiles' in raw and (not isinstance(raw['profiles'], list) or not raw['profiles']
                             or any(not isinstance(p, dict) or not p.get('id') or not p.get('name') for p in raw['profiles'])):
        raise ValueError('MODEL_CONFIG_INVALID')
    legacy = config._profile_document()
    providers, models = [], []
    for profile in legacy['profiles']:
        runtime = config._settings_values(profile)
        if profile['id'] == 'environment' and not runtime['active_model'] and not runtime['llm_enabled']:
            continue
        pid = str(uuid5(NAMESPACE_URL, 'model-provider:' + str(profile['id'])))
        protocol = runtime['llm_provider']
        providers.append({
            'id': pid, 'name': profile['name'], 'protocol': protocol, 'enabled': runtime['llm_enabled'],
            'base_url': runtime['ollama_base_url'] if protocol == 'ollama' else runtime['llm_base_url'],
            'api_key': '' if protocol == 'ollama' else runtime['llm_api_key'],
            'trusted_http_origin': runtime['llm_trusted_http_origin'],
            'proxy_url': '' if protocol == 'ollama' else runtime['llm_proxy_url'] or '',
            'tls_max_version': runtime['llm_tls_max_version'], 'tls_key_exchange': runtime['llm_tls_key_exchange'],
            'connect_timeout': runtime['llm_connect_timeout'], 'read_timeout': runtime['llm_read_timeout'],
        })
        models.append({'id': str(profile['id']), 'provider_id': pid, 'name': runtime['active_model'] or profile['name'],
                       'model': runtime['active_model'], 'enabled': runtime['llm_enabled'],
                       'max_output_tokens': runtime['llm_max_output_tokens'], 'context_window': runtime['llm_context_window'],
                       'max_turns': runtime['llm_max_turns']})
    default_id = legacy['active_profile_id'] if models else None
    return {'version': 3, 'providers': providers,
            'models': [dict(m, reasoning_policy='default', default_reasoning_effort='') for m in models],
            'default_model_id': default_id}


def _revision(document):
    encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return sha256(encoded).hexdigest()


def _public(document):
    providers = []
    for value in document['providers']:
        provider = {k: v for k, v in value.items() if k != 'api_key'}
        provider['api_key_configured'] = bool(value['api_key'])
        if provider.get('proxy_url'):
            proxy = httpx.URL(provider['proxy_url'])
            if proxy.username or proxy.password:
                provider['proxy_url'] = str(proxy.copy_with(username='', password=''))
                provider['proxy_credentials_configured'] = True
        providers.append(provider)
    models = deepcopy(document['models'])
    protocol = {p['id']: p['protocol'] for p in document['providers']}
    for model in models:
        model['reasoning'] = reasoning_capability(protocol[model['provider_id']], model['model'], model['reasoning_policy'])
    policies = [{'id':'default','name':'自动（仅已知模型）','protocols':['company','ollama'],'levels':[]}]
    for policy, protocol, name in [('glm','company','GLM 档位'),('openai','company','原生 OpenAI 推理（手工声明）'),
                                   ('ollama','ollama','Ollama 开关（手工声明）'),('ollama-levels','ollama','Ollama 档位（手工声明）')]:
        policies.append({'id':policy,'name':name,'protocols':[protocol],
                         'levels':reasoning_capability(protocol,'',policy)['levels']})
    return {'version': 3, 'revision': _revision(document), 'default_model_id': document['default_model_id'],
            'providers': providers, 'models': models, 'reasoning_policies':policies}


def public_catalog():
    return _public(read_catalog())


def flatten_catalog(document):
    """旧运行端只读视图；模型 ID 稳定，不把重复的 Key 再写入文件。"""
    document = _validated(document)
    providers = {p['id']: p for p in document['providers']}
    profiles = []
    for model in document['models']:
        p = providers[model['provider_id']]
        profiles.append({
            'id': model['id'], 'name': model['name'], 'llm_provider': p['protocol'],
            'llm_enabled': p['enabled'] and model['enabled'], 'llm_base_url': p['base_url'] if p['protocol'] == 'company' else '',
            'llm_api_key': p['api_key'], 'llm_model': model['model'] if p['protocol'] == 'company' else '',
            'ollama_base_url': p['base_url'] if p['protocol'] == 'ollama' else 'http://127.0.0.1:11434',
            'ollama_model': model['model'] if p['protocol'] == 'ollama' else '',
            'llm_trusted_http_origin': p['trusted_http_origin'], 'llm_proxy_url': p['proxy_url'] or None,
            'llm_tls_max_version': p['tls_max_version'], 'llm_tls_key_exchange': p['tls_key_exchange'],
            'llm_connect_timeout': p['connect_timeout'], 'llm_read_timeout': p['read_timeout'],
            'llm_max_output_tokens': model['max_output_tokens'], 'llm_context_window': model['context_window'],
            'llm_max_turns': model['max_turns'],
            'llm_reasoning_policy': model['reasoning_policy'], 'llm_reasoning_effort': model['default_reasoning_effort'],
        })
    if not profiles:
        # 兼容旧 /me 及 Worker 的“等待配置”分支，不加载环境中的其他 Key。
        profiles = [{'id': 'unconfigured', 'name': '未配置模型', 'llm_enabled': False,
                     'llm_provider': 'company', 'llm_base_url': '', 'llm_model': '', 'llm_api_key': ''}]
    return {'version': 3, 'active_profile_id': document['default_model_id'] or 'unconfigured', 'profiles': profiles}


def _persist(document):
    path = config._model_config_path()
    fd, filename = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(filename, path)
    finally:
        Path(filename).unlink(missing_ok=True)


def _change(expected_revision, operation):
    with config_lock():
        document = read_catalog()
        if not expected_revision or expected_revision != _revision(document):
            raise ValueError('MODEL_CONFIG_CONFLICT')
        doc_id = config.settings().document_model_profile_id
        protected = next((m for m in document['models'] if m['id'] == doc_id), None)
        protected_provider = next((p for p in document['providers'] if protected and p['id']==protected['provider_id']), None)
        operation(document)
        if protected:
            current = next((m for m in document['models'] if m['id'] == doc_id), None)
            parent = next((p for p in document['providers'] if current and p['id'] == current['provider_id']), None)
            if not current or not parent or not current['enabled'] or not parent['enabled']:
                raise ValueError('DOCUMENT_MODEL_IN_USE')
            if (current['provider_id'], current['model']) != (protected['provider_id'], protected['model']) or (protected_provider and parent['protocol'] != protected_provider['protocol']):
                raise ValueError('DOCUMENT_MODEL_IN_USE')
        document = _validated(document)
        _persist(document)
        return _public(document)


def _find(rows, identifier, code):
    item = next((item for item in rows if item['id'] == identifier), None)
    if item is None:
        raise ValueError(code)
    return item


def provider_draft(data, provider_id=None):
    """检测与保存共用凭据解析；目标改变时不能默带旧 Key。"""
    current = (_find(read_catalog()['providers'], provider_id, 'MODEL_PROVIDER_NOT_FOUND')
               if provider_id else {})
    values = dict(data)
    clear_key = values.pop('clear_api_key', False)
    key = values.pop('api_key', '')
    merged = {**current, **values, 'id': provider_id or str(uuid4())}
    old_destination = (current.get('protocol'), str(current.get('base_url', '')).rstrip('/'))
    new_destination = (merged.get('protocol', 'company'), str(merged.get('base_url', '')).strip().rstrip('/'))
    if current.get('api_key') and old_destination != new_destination and not key and not clear_key:
        raise ValueError('MODEL_DESTINATION_REQUIRES_KEY')
    merged['api_key'] = '' if clear_key else key or current.get('api_key', '')
    merged['base_url'] = new_destination[1]
    if 'name' in merged:
        merged['name'] = str(merged['name']).strip()
    try:
        provider = Provider.model_validate(merged).model_dump()
    except ValidationError:
        raise ValueError('MODEL_PROVIDER_INVALID') from None
    _validate_provider(provider)
    return provider


def save_provider(data, provider_id=None, *, expected_revision):
    def apply(document):
        provider = provider_draft(data, provider_id)
        if provider_id:
            document['providers'] = [provider if p['id'] == provider_id else p for p in document['providers']]
        else:
            document['providers'].append(provider)
    return _change(expected_revision, apply)


def _model_change(data, model_id=None):
    def apply(document):
        old = _find(document['models'], model_id, 'MODEL_NOT_FOUND') if model_id else {}
        merged = {**old, **data, 'id': model_id or str(uuid4())}
        merged['model'] = str(merged.get('model') or '').strip()
        merged['name'] = str(merged.get('name') or merged['model']).strip()
        try:
            model = CatalogModel.model_validate(merged).model_dump()
        except ValidationError:
            raise ValueError('MODEL_INVALID') from None
        provider = _find(document['providers'], model['provider_id'], 'MODEL_PROVIDER_NOT_FOUND')
        reasoning_parameters(provider['protocol'], model['model'], model['reasoning_policy'], model['default_reasoning_effort'])
        if any(m['provider_id'] == model['provider_id'] and m['model'] == model['model'] and m['id'] != model['id']
               for m in document['models']):
            raise ValueError('MODEL_ALREADY_EXISTS')
        if model_id:
            document['models'] = [model if m['id'] == model_id else m for m in document['models']]
        else:
            document['models'].append(model)
            if document['default_model_id'] is None:
                document['default_model_id'] = model['id']
    return apply


def save_model(data, model_id=None, *, expected_revision):
    return _change(expected_revision, _model_change(data, model_id))


def save_models(models, *, expected_revision):
    if not isinstance(models, list) or not 1 <= len(models) <= 100:
        raise ValueError('MODEL_INVALID')
    def apply(document):
        for data in models:
            _model_change(data)(document)
    return _change(expected_revision, apply)


def set_default_model(model_id, *, expected_revision):
    def apply(document):
        model = _find(document['models'], model_id, 'MODEL_NOT_FOUND')
        provider = _find(document['providers'], model['provider_id'], 'MODEL_PROVIDER_NOT_FOUND')
        if not model['enabled'] or not provider['enabled']:
            raise ValueError('MODEL_DISABLED')
        document['default_model_id'] = model_id
    return _change(expected_revision, apply)


def delete_model(model_id, *, expected_revision):
    def apply(document):
        _find(document['models'], model_id, 'MODEL_NOT_FOUND')
        if model_id != config.settings().document_model_profile_id and model_id == document['default_model_id']:
            raise ValueError('DEFAULT_MODEL_IN_USE')
        document['models'] = [m for m in document['models'] if m['id'] != model_id]
    return _change(expected_revision, apply)


def delete_provider(provider_id, *, expected_revision):
    def apply(document):
        _find(document['providers'], provider_id, 'MODEL_PROVIDER_NOT_FOUND')
        removed = {m['id'] for m in document['models'] if m['provider_id'] == provider_id}
        if document['default_model_id'] in removed and config.settings().document_model_profile_id not in removed:
            raise ValueError('DEFAULT_MODEL_IN_USE')
        document['models'] = [m for m in document['models'] if m['id'] not in removed]
        document['providers'] = [p for p in document['providers'] if p['id'] != provider_id]
    return _change(expected_revision, apply)
