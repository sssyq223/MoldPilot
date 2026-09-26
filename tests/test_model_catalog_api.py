"""目录 HTTP 权限、CSRF、公开返回及草稿检测；仅隔离库与临时配置。"""
import httpx
from conftest import sign_in
from test_model_catalog import catalog_file
from test_model_discovery import public_dns


def test_catalog_and_discovery_remain_admin_only(client, catalog_file):
    assert client.get('/api/model-catalog').status_code == 401
    sign_in(client, 'test_buyer')
    assert client.get('/api/model-catalog').status_code == 403
    assert client.post('/api/model-providers/discover', json={'connection': {}}).status_code == 403


def test_admin_can_add_models_under_one_provider(client, catalog_file):
    sign_in(client)
    response = client.get('/api/model-catalog')
    assert response.status_code == 200, response.text
    state = response.json()
    response = client.post('/api/catalog-models', json={
        'revision': state['revision'], 'model': {'provider_id': state['providers'][0]['id'],
                                              'name': '第二模型', 'model': 'model-b'}})
    assert response.status_code == 200, response.text
    state = response.json()
    assert len(state['providers']) == 1 and len(state['models']) == 2
    assert 'synthetic-secret' not in response.text
    stale = client.post('/api/catalog-models', json={'revision': '0'*64,
        'model': {'provider_id': state['providers'][0]['id'], 'model': 'model-c'}})
    assert stale.status_code == 409 and stale.json()['error']['code'] == 'MODEL_CONFIG_CONFLICT'
    client.headers.pop('X-CSRF-Token')
    assert client.post('/api/catalog-models', json={'revision': state['revision'],
        'model': {'provider_id': state['providers'][0]['id'], 'model': 'model-c'}}).status_code == 403


def test_detection_uses_unsaved_key_without_saving_or_generating(client, catalog_file, monkeypatch):
    from app import model_discovery
    sign_in(client)
    before = catalog_file.read_bytes()
    def serve(request):
        assert request.method == 'GET' and request.url.path == '/v1/models'
        assert request.headers['authorization'] == 'Bearer unsaved-synthetic'
        return httpx.Response(200,json={'data':[{'id':'model-a'},{'id':'model-b'}]})
    monkeypatch.setattr(model_discovery, '_transport', lambda *args:httpx.MockTransport(serve))
    response = client.post('/api/model-providers/discover', json={'connection': {
        'name':'未保存', 'protocol':'company', 'base_url':'https://vendor.example/v1',
        'api_key':'unsaved-synthetic'}})
    assert response.status_code == 200, response.text
    assert response.json()['count'] == 2
    assert 'unsaved-synthetic' not in response.text
    assert catalog_file.read_bytes() == before


def test_validation_does_not_echo_submitted_key(client, catalog_file):
    sign_in(client)
    secret = 'sensitive-synthetic-'*300
    response = client.post('/api/model-providers/discover', json={'connection': {
        'name':'未保存', 'base_url':'https://vendor.example/v1', 'api_key':secret}})
    assert response.status_code == 422, response.text
    assert 'sensitive-synthetic-' not in response.text
