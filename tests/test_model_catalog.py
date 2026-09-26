"""供应商目录契约：真实临时文件，不读写正式模型配置或使用真实凭据。"""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from app import config


@pytest.fixture
def catalog_file(tmp_path, monkeypatch):
    path = tmp_path / 'models.json'
    path.write_text(json.dumps({'version': 2, 'active_profile_id': 'legacy-glm', 'profiles': [{
        'id': 'legacy-glm', 'name': '白山', 'llm_enabled': True, 'llm_provider': 'company',
        'llm_base_url': 'https://vendor.example/v1', 'llm_api_key': 'synthetic-secret',
        'llm_model': 'GLM-5.3-Flash', 'llm_max_output_tokens': 8192,
        'llm_context_window': 131072, 'llm_max_turns': 12,
        'llm_connect_timeout': 10, 'llm_read_timeout': 60,
    }]}), encoding='utf-8')
    monkeypatch.setattr(config, '_model_config_path', lambda: path)
    monkeypatch.setattr(config.settings(), 'document_model_profile_id', 'legacy-glm')
    return path


def test_legacy_projection_is_read_only_and_preserves_document_reference(catalog_file):
    from app.model_catalog import public_catalog
    before = catalog_file.read_bytes()
    value = public_catalog()
    assert value['default_model_id'] == 'legacy-glm'
    assert value['models'][0]['id'] == 'legacy-glm'
    assert value['models'][0]['model'] == 'GLM-5.3-Flash'
    assert value['providers'][0]['name'] == '白山'
    assert value['providers'][0]['api_key_configured'] is True
    assert 'synthetic-secret' not in json.dumps(value)
    assert config.document_model_settings().document_model == 'GLM-5.3-Flash'
    assert catalog_file.read_bytes() == before


def test_incomplete_disabled_legacy_profile_does_not_break_enabled_model(catalog_file):
    from app.model_catalog import public_catalog, save_model
    from app.run_model_selection import select_model, runtime_for_selection
    from types import SimpleNamespace
    raw=json.loads(catalog_file.read_text(encoding='utf-8'))
    raw['profiles'].append({'id':'unfinished','name':'未配置连接','llm_enabled':False,
                            'llm_provider':'company','llm_base_url':'','llm_model':'','llm_api_key':''})
    catalog_file.write_text(json.dumps(raw),encoding='utf-8')
    state=public_catalog()
    selected=select_model(SimpleNamespace(super_admin=True),'legacy-glm','low')
    assert runtime_for_selection(selected).llm_model=='GLM-5.3-Flash'
    save_model({'provider_id':state['models'][0]['provider_id'],'model':'another-model'},
               expected_revision=state['revision'])
    assert config.document_model_settings().document_model=='GLM-5.3-Flash'


def test_multiple_models_share_one_credential_and_keep_legacy_reader(catalog_file):
    from app.model_catalog import public_catalog, save_model
    state = public_catalog()
    result = save_model({'provider_id': state['providers'][0]['id'], 'name': '第二个模型',
                         'model': 'model-b'}, expected_revision=state['revision'])
    assert {m['model'] for m in result['models']} == {'GLM-5.3-Flash', 'model-b'}
    raw = json.loads(catalog_file.read_text(encoding='utf-8'))
    assert raw['version'] == 3
    assert len(raw['providers']) == 1
    assert catalog_file.read_text(encoding='utf-8').count('synthetic-secret') == 1
    assert all('api_key' not in m for m in raw['models'])
    assert config.model_settings().llm_model == 'GLM-5.3-Flash'
    assert config.document_model_settings().document_model_api_key == 'synthetic-secret'
    assert {p['model'] for p in config.public_model_config()['profiles']} == {'GLM-5.3-Flash', 'model-b'}
    assert 'synthetic-secret' not in json.dumps(result)


def test_batch_add_is_atomic_and_default_change_does_not_change_document_model(catalog_file):
    from app.model_catalog import public_catalog, save_models, set_default_model
    state=public_catalog();pid=state['providers'][0]['id'];before=catalog_file.read_bytes()
    with pytest.raises(ValueError):
        save_models([{'provider_id':pid,'model':'valid'},{'provider_id':pid,'model':''}],expected_revision=state['revision'])
    assert catalog_file.read_bytes()==before
    state=save_models([{'provider_id':pid,'model':'a'},{'provider_id':pid,'model':'b'}],expected_revision=state['revision'])
    assert {m['model'] for m in state['models']}=={'GLM-5.3-Flash','a','b'}
    mid=next(m['id'] for m in state['models'] if m['model']=='a')
    set_default_model(mid,expected_revision=state['revision'])
    assert config.model_settings().llm_model=='a'
    assert config.document_model_settings().document_model=='GLM-5.3-Flash'


def test_duplicate_model_rejected_but_same_name_on_other_provider_allowed(catalog_file):
    from app.model_catalog import public_catalog, save_model, save_provider
    state = public_catalog()
    with pytest.raises(ValueError, match='MODEL_ALREADY_EXISTS'):
        save_model({'provider_id': state['providers'][0]['id'], 'model': 'GLM-5.3-Flash'},
                   expected_revision=state['revision'])
    result = save_provider({'name': '另一个账号', 'protocol': 'company',
                            'base_url': 'https://vendor.example/v1', 'api_key': 'second-synthetic'},
                           expected_revision=state['revision'])
    pid = next(p['id'] for p in result['providers'] if p['name'] == '另一个账号')
    result = save_model({'provider_id': pid, 'model': 'GLM-5.3-Flash'}, expected_revision=result['revision'])
    assert len(result['providers']) == 2 and len(result['models']) == 2


def test_stale_revision_does_not_overwrite_new_provider(catalog_file):
    from app.model_catalog import public_catalog, save_provider
    state = public_catalog()
    save_provider({'name': '新账号', 'protocol': 'company', 'base_url': 'https://other.example/v1'},
                  expected_revision=state['revision'])
    before = catalog_file.read_bytes()
    with pytest.raises(ValueError, match='MODEL_CONFIG_CONFLICT'):
        save_provider({'name': '旧页面覆盖'}, provider_id=state['providers'][0]['id'],
                      expected_revision=state['revision'])
    assert catalog_file.read_bytes() == before


def test_concurrent_saves_with_one_revision_only_commit_one(catalog_file):
    from app.model_catalog import public_catalog, save_provider
    state = public_catalog()
    def save(name):
        try:
            save_provider({'name': name, 'protocol': 'company', 'base_url': 'https://other.example/v1'},
                          expected_revision=state['revision'])
            return 'saved'
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(save, ['账号 A', '账号 B']))
    assert sorted(outcomes) == ['MODEL_CONFIG_CONFLICT', 'saved']
    assert len(public_catalog()['providers']) == 2


@pytest.mark.parametrize('operation', ['delete_model', 'disable_model', 'delete_provider', 'disable_provider', 'change_protocol'])
def test_document_reference_cannot_be_removed_or_disabled(catalog_file, operation):
    from app.model_catalog import public_catalog, save_model, save_provider, delete_model, delete_provider
    state = public_catalog()
    before = catalog_file.read_bytes()
    with pytest.raises(ValueError, match='DOCUMENT_MODEL_IN_USE'):
        if operation == 'delete_model':
            delete_model('legacy-glm', expected_revision=state['revision'])
        elif operation == 'disable_model':
            save_model({'enabled': False}, model_id='legacy-glm', expected_revision=state['revision'])
        elif operation == 'delete_provider':
            delete_provider(state['providers'][0]['id'], expected_revision=state['revision'])
        elif operation == 'disable_provider':
            save_provider({'enabled': False}, provider_id=state['providers'][0]['id'],
                          expected_revision=state['revision'])
        else:
            save_provider({'protocol':'ollama','base_url':'http://127.0.0.1:11434','clear_api_key':True},
                          provider_id=state['providers'][0]['id'],expected_revision=state['revision'])
    assert catalog_file.read_bytes() == before


def test_provider_key_rotation_updates_all_models_without_copying(catalog_file):
    from app.model_catalog import public_catalog, save_provider, save_model
    state = public_catalog()
    pid = state['providers'][0]['id']
    state = save_model({'provider_id': pid, 'model': 'model-b'}, expected_revision=state['revision'])
    state = save_provider({'api_key': 'rotated-synthetic'}, provider_id=pid,
                          expected_revision=state['revision'])
    profiles = config._profile_document()['profiles']
    assert {p['llm_api_key'] for p in profiles} == {'rotated-synthetic'}
    assert catalog_file.read_text().count('rotated-synthetic') == 1
    assert 'rotated-synthetic' not in json.dumps(state)


def test_changing_destination_requires_explicit_new_key(catalog_file):
    from app.model_catalog import public_catalog, save_provider
    state = public_catalog()
    with pytest.raises(ValueError, match='MODEL_DESTINATION_REQUIRES_KEY'):
        save_provider({'base_url': 'https://different.example/v1', 'api_key': ''},
                      provider_id=state['providers'][0]['id'], expected_revision=state['revision'])
    assert config.model_settings().llm_base_url == 'https://vendor.example/v1'


def test_legacy_writer_cannot_downgrade_v3(catalog_file):
    from app.model_catalog import public_catalog, save_model
    state = public_catalog()
    save_model({'provider_id': state['providers'][0]['id'], 'model': 'model-b'},
               expected_revision=state['revision'])
    before = catalog_file.read_bytes()
    with pytest.raises(ValueError, match='MODEL_CONFIG_V3_REQUIRED'):
        config.activate_model_profile('legacy-glm')
    assert catalog_file.read_bytes() == before


def test_model_default_effort_validated_and_does_not_change_document_effort(catalog_file,monkeypatch):
    from app.model_catalog import public_catalog, save_model
    monkeypatch.setattr(config.settings(),'document_model_reasoning_effort','low')
    state=public_catalog()
    assert [level['value'] for level in state['models'][0]['reasoning']['levels']]==['low','high','max']
    with pytest.raises(ValueError,match='MODEL_REASONING_UNSUPPORTED'):
        save_model({'default_reasoning_effort':'medium'},model_id='legacy-glm',expected_revision=state['revision'])
    save_model({'default_reasoning_effort':'high'},model_id='legacy-glm',expected_revision=state['revision'])
    assert config.model_settings().llm_reasoning_effort=='high'
    assert config.document_model_settings().document_model_reasoning_effort=='low'


def test_first_provider_can_be_saved_before_any_model_is_configured(tmp_path, monkeypatch):
    from app.model_catalog import public_catalog, save_provider, save_model
    path = tmp_path / 'new.json'
    monkeypatch.setattr(config, '_model_config_path', lambda: path)
    monkeypatch.setattr(config.settings(), 'llm_provider', 'company')
    monkeypatch.setattr(config.settings(), 'llm_model', '')
    monkeypatch.setattr(config.settings(), 'llm_base_url', '')
    monkeypatch.setattr(config.settings(), 'llm_enabled', False)
    state = public_catalog()
    assert state['models'] == [] and state['default_model_id'] is None
    state = save_provider({'name': '新供应商', 'base_url': 'https://vendor.example/v1'},
                          expected_revision=state['revision'])
    assert state['models'] == []
    assert config.model_settings().llm_enabled is False
    state = save_model({'provider_id': state['providers'][0]['id'], 'model': 'first-model'},
                       expected_revision=state['revision'])
    assert state['default_model_id'] == state['models'][0]['id']
    assert config.model_settings().llm_model == 'first-model'


def test_malformed_config_fails_closed_instead_of_replacing_with_environment(catalog_file):
    from app.model_catalog import public_catalog
    catalog_file.write_text('{not-json', encoding='utf-8')
    with pytest.raises(ValueError, match='MODEL_CONFIG_INVALID'):
        public_catalog()
    assert catalog_file.read_text() == '{not-json'
