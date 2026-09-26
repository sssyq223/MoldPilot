"""思考参数协议。目录发现不构成能力证明；显式策略是管理员的能力声明。"""

POLICIES = ('default', 'glm', 'openai', 'ollama', 'ollama-levels')
_LABELS = {'low': '低', 'medium': '中', 'high': '高', 'max': '最高', 'off': '关闭', 'on': '开启'}
_GLM_MODELS = {'glm-5.3', 'glm-5.3-flash'}


def reasoning_capability(protocol, model, policy='default'):
    if policy not in POLICIES:
        raise ValueError('MODEL_REASONING_UNSUPPORTED')
    effective = policy
    source = 'administrator_declared' if policy != 'default' else 'unknown'
    if policy == 'default' and protocol == 'company' and model.lower() in _GLM_MODELS:
        effective, source = 'glm', 'known_model_protocol'
    if effective in {'glm', 'openai'} and protocol != 'company':
        raise ValueError('MODEL_REASONING_UNSUPPORTED')
    if effective in {'ollama', 'ollama-levels'} and protocol != 'ollama':
        raise ValueError('MODEL_REASONING_UNSUPPORTED')
    levels = {'glm': ['low', 'high', 'max'], 'openai': ['low', 'medium', 'high'],
              'ollama': ['off', 'on'], 'ollama-levels': ['low', 'medium', 'high']}.get(effective, [])
    # 精确已知的 GLM 不允许管理员模板意外开放它不支持的 medium/关闭。
    if protocol == 'company' and model.lower() in _GLM_MODELS and effective != 'glm':
        raise ValueError('MODEL_REASONING_UNSUPPORTED')
    return {'supported': bool(levels), 'policy': effective, 'source': source,
            'levels': [{'value': v, 'label': _LABELS[v]} for v in levels],
            'parameter': ('think' if protocol == 'ollama' else 'reasoning_effort') if levels else None,
            'default_label': '服务默认' if protocol == 'company' else '默认（关闭）'}


def reasoning_parameters(protocol, model, policy='default', effort=''):
    capability = reasoning_capability(protocol, model, policy)
    if not effort:
        return {} if protocol == 'company' else {'think': False}
    if effort not in {item['value'] for item in capability['levels']}:
        raise ValueError('MODEL_REASONING_UNSUPPORTED')
    value = effort
    if capability['policy'] == 'ollama':
        value = effort == 'on'
    return {capability['parameter']: value}
