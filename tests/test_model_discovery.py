"""只替换外部 DNS/HTTP；目录解析、凭据选择和 URL 安全校验使用真实实现。"""
import json
import socket

import httpx
import pytest
from app.errors import DomainError
from test_model_catalog import catalog_file


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))])


def draft(**changes):
    return {'connection': {'name': '测试供应商', 'protocol': 'company',
                           'base_url': 'https://vendor.example/v1', 'api_key': 'synthetic', **changes}}


def test_detects_catalog_via_get_pins_destination_and_does_not_save(catalog_file):
    from app.model_discovery import discover_models
    before = catalog_file.read_bytes()
    def serve(request):
        assert request.method == 'GET'
        assert request.url.host == '93.184.216.34'
        assert request.url.path == '/v1/models'
        assert request.headers['host'] == 'vendor.example'
        assert request.extensions['sni_hostname'] == 'vendor.example'
        assert request.headers['authorization'] == 'Bearer synthetic'
        return httpx.Response(200, json={'object': 'list', 'data': [
            {'id': 'model-b', 'object': 'model'}, {'id': 'model-a'}, {'id': 'model-b'}]})
    result = discover_models(draft(), transport=httpx.MockTransport(serve))
    assert [m['model'] for m in result['models']] == ['model-a', 'model-b']
    assert result['count'] == 2 and result['complete'] is True
    assert all(m['capability_status'] == 'unverified' for m in result['models'])
    assert 'synthetic' not in json.dumps(result)
    assert catalog_file.read_bytes() == before


def test_saved_credential_reused_without_echo(catalog_file):
    from app.model_catalog import public_catalog
    from app.model_discovery import discover_models
    state = public_catalog()
    def serve(request):
        assert request.headers['authorization'] == 'Bearer synthetic-secret'
        return httpx.Response(200, json={'data': []})
    result = discover_models({'provider_id': state['providers'][0]['id'], 'revision': state['revision'],
                              'connection': {'api_key': ''}}, transport=httpx.MockTransport(serve))
    assert result['count'] == 0
    assert 'synthetic-secret' not in json.dumps(result)


def test_changed_destination_cannot_use_stored_secret(catalog_file):
    from app.model_catalog import public_catalog
    from app.model_discovery import discover_models
    state = public_catalog()
    def forbidden(request):pytest.fail('不应对新目标发送已保存的 Key')
    with pytest.raises(DomainError) as error:
        discover_models({'provider_id': state['providers'][0]['id'], 'revision': state['revision'],
                         'connection': {'base_url': 'https://changed.example/v1'}},
                        transport=httpx.MockTransport(forbidden))
    assert error.value.code == 'MODEL_DESTINATION_REQUIRES_KEY'


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '::ffff:127.0.0.1'])
def test_dns_resolving_to_nonpublic_address_is_blocked(monkeypatch, address):
    from app.model_discovery import discover_models
    family = socket.AF_INET6 if ':' in address else socket.AF_INET
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [
        (family, socket.SOCK_STREAM, 6, '', (address, 443))])
    def forbidden(request):pytest.fail('不应向被拒绝地址发 HTTP 请求')
    with pytest.raises(DomainError) as error:discover_models(draft(), transport=httpx.MockTransport(forbidden))
    assert error.value.code == 'MODEL_DISCOVERY_ADDRESS_FORBIDDEN'


@pytest.mark.parametrize('address', ['198.18.0.70', '2001:2::7e'])
def test_fake_ip_benchmark_address_is_allowed(monkeypatch, address):
    from app.model_discovery import discover_models
    family = socket.AF_INET6 if ':' in address else socket.AF_INET
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [
        (family, socket.SOCK_STREAM, 6, '', (address, 443))])
    def serve(request):
        return httpx.Response(200, json={'data': [{'id': 'fake-ip-model'}]})
    result = discover_models(draft(), transport=httpx.MockTransport(serve))
    assert result['count'] == 1 and result['models'][0]['model'] == 'fake-ip-model'


@pytest.mark.parametrize('status,code', [(401,'MODEL_AUTH_FAILED'),(403,'MODEL_AUTH_FAILED'),
    (404,'MODEL_CATALOG_UNSUPPORTED'),(429,'MODEL_RATE_LIMITED'),(500,'MODEL_DISCOVERY_HTTP_FAILED'),
    (302,'MODEL_DISCOVERY_REDIRECT_FORBIDDEN')])
def test_upstream_error_is_specific_and_does_not_leak_body(status, code):
    from app.model_discovery import discover_models
    def serve(request):return httpx.Response(status, text='secret-upstream-body', headers={'location':'http://169.254.169.254'})
    with pytest.raises(DomainError) as error:discover_models(draft(), transport=httpx.MockTransport(serve))
    assert error.value.code == code
    assert 'secret-upstream-body' not in str(error.value)


@pytest.mark.parametrize('body', [None, {'data':'wrong'}, {'data':[{}]}, {'data':[{'id':'x'*161}]}])
def test_malformed_catalog_is_not_misreported_as_zero_models(body):
    from app.model_discovery import discover_models
    with pytest.raises(DomainError) as error:
        discover_models(draft(), transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body)))
    assert error.value.code == 'MODEL_CATALOG_INVALID'


def test_partial_catalog_is_reported_as_incomplete():
    from app.model_discovery import discover_models
    result = discover_models(draft(), transport=httpx.MockTransport(lambda r:httpx.Response(
        200,json={'data':[{'id':'first'}], 'has_more':True})))
    assert result['count'] == 1 and result['complete'] is False


def test_oversized_response_is_bounded():
    from app.model_discovery import discover_models
    with pytest.raises(DomainError) as error:
        discover_models(draft(), transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b' '*2_097_153)))
    assert error.value.code == 'MODEL_CATALOG_TOO_LARGE'


def test_compressed_response_is_rejected_before_decompression():
    from app.model_discovery import discover_models
    def serve(request):
        assert request.headers['accept-encoding']=='identity'
        return httpx.Response(200,headers={'content-encoding':'gzip'},stream=httpx.ByteStream(b'not-decoded'))
    with pytest.raises(DomainError) as error:discover_models(draft(),transport=httpx.MockTransport(serve))
    assert error.value.code=='MODEL_CATALOG_ENCODING_UNSUPPORTED'


@pytest.mark.parametrize('proxy',['http://proxy.example:8080','https://proxy.example:8443'])
def test_proxy_connect_uses_pinned_ip_but_preserves_both_tls_hostnames(monkeypatch, proxy):
    import httpcore
    from app.model_discovery import discover_models
    names, writes, connections = [], [], []
    body=b'{"data":[{"id":"through-proxy"}]}'
    class Stream(httpcore.MockStream):
        def start_tls(self,ssl_context,server_hostname=None,timeout=None):
            assert ssl_context.check_hostname is True
            names.append(server_hostname)
            return self
        def write(self,buffer,timeout=None):writes.append(buffer)
    stream=Stream([b'HTTP/1.1 200 Connection established\r\n\r\n',
                   b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body])
    def connect(self,host,port,timeout=None,local_address=None,socket_options=None):
        connections.append((host,port))
        return stream
    monkeypatch.setattr(httpcore.SyncBackend,'connect_tcp',connect)
    result=discover_models(draft(proxy_url=proxy))
    assert result['models'][0]['model']=='through-proxy'
    assert connections==[('proxy.example',8443 if proxy.startswith('https') else 8080)]
    assert names==(['proxy.example','vendor.example'] if proxy.startswith('https') else ['vendor.example'])
    assert any(b'CONNECT 93.184.216.34:443 HTTP/1.1' in chunk for chunk in writes)
    assert any(b'Host: vendor.example' in chunk for chunk in writes)


def test_timeout_does_not_fall_back_to_guessed_models():
    from app.model_discovery import discover_models
    def serve(request):raise httpx.ReadTimeout('synthetic')
    with pytest.raises(DomainError) as error:discover_models(draft(),transport=httpx.MockTransport(serve))
    assert error.value.code == 'MODEL_DISCOVERY_TIMEOUT'


def test_ollama_lists_tags_without_company_credentials():
    from app.model_discovery import discover_models
    def serve(request):
        assert str(request.url) == 'http://127.0.0.1:11434/api/tags'
        assert 'authorization' not in request.headers
        return httpx.Response(200,json={'models':[{'name':'qwen3:8b'}]})
    result=discover_models(draft(protocol='ollama',base_url='http://127.0.0.1:11434',api_key=''),
                           transport=httpx.MockTransport(serve))
    assert result['models'][0]['model']=='qwen3:8b'
