"""只发现供应商目录，不执行生成或保存配置；不把模型名称当作能力证据。"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from ipaddress import ip_address, ip_network
import json
import socket
import threading
import time

import httpcore
import httpx

from agent_core.model_adapter import tls_context
from .errors import DomainError
from .model_catalog import config_lock, provider_draft, public_catalog

_MAX_BYTES = 2 * 1024 * 1024
_MAX_MODELS = 1000
_SLOTS = threading.BoundedSemaphore(2)
_DNS_SLOTS = threading.BoundedSemaphore(2)
_DNS_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='model-directory-dns')
_FAKE_IP_V4 = ip_network('198.18.0.0/15')
_FAKE_IP_V6 = ip_network('2001:2::/48')


def _is_allowed_address(addr):
    if addr.is_multicast:
        return False
    if addr.is_global:
        return True
    if addr.version == 4 and addr in _FAKE_IP_V4:
        return True
    if addr.version == 6 and addr in _FAKE_IP_V6:
        return True
    return False


def _error(code, message='模型目录检测未完成，请检查连接配置', status=400):
    return DomainError(code, message, status)


def _public_address(host, port):
    try:
        addresses = [ip_address(host)]
    except ValueError:
        if not _DNS_SLOTS.acquire(blocking=False):
            raise _error('MODEL_DISCOVERY_BUSY', '模型地址解析繁忙，请稍后重试', 429)
        try:
            future = _DNS_POOL.submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
        except Exception:
            _DNS_SLOTS.release()
            raise
        future.add_done_callback(lambda _: _DNS_SLOTS.release())
        try:
            addresses = [ip_address(result[4][0]) for result in future.result(timeout=5)]
        except FutureTimeout:
            raise _error('MODEL_DISCOVERY_TIMEOUT', '模型地址解析超时', 504) from None
        except (OSError, ValueError):
            raise _error('MODEL_DISCOVERY_DNS_FAILED', '无法解析模型服务地址', 502) from None
    if not addresses or any(not _is_allowed_address(a) for a in addresses):
        raise _error('MODEL_DISCOVERY_ADDRESS_FORBIDDEN', '公网模型目录不能访问私网、回环或链路本地地址')
    return str(addresses[0])


class _SNIStream(httpcore.NetworkStream):
    """CONNECT/SOCKS 连接到已校验的 IP，但目标 TLS 仍校验原始域名。"""
    def __init__(self, stream, host, address, skip_proxy_tls=False):
        self.stream, self.host, self.address = stream, host, address
        self.skip_proxy_tls = skip_proxy_tls

    def read(self, max_bytes, timeout=None):
        return self.stream.read(max_bytes, timeout)

    def write(self, buffer, timeout=None):
        return self.stream.write(buffer, timeout)

    def close(self):
        self.stream.close()

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        hostname = server_hostname
        if not self.skip_proxy_tls and server_hostname == self.address:
            hostname = self.host
        stream = self.stream.start_tls(ssl_context, server_hostname=hostname, timeout=timeout)
        return _SNIStream(stream, self.host, self.address)


class _SNIBackend(httpcore.NetworkBackend):
    def __init__(self, backend, host, address, proxy_tls):
        self.backend, self.host, self.address, self.proxy_tls = backend, host, address, proxy_tls

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        stream = self.backend.connect_tcp(host, port, timeout, local_address, socket_options)
        return _SNIStream(stream, self.host, self.address, self.proxy_tls)


def _transport(provider, hostname, address):
    proxy = provider['proxy_url'] or None
    transport = httpx.HTTPTransport(
        verify=tls_context(provider['tls_max_version'], provider['tls_key_exchange']), proxy=proxy,
        trust_env=False, retries=0,
    )
    if proxy:
        # HTTPX 未公开 network_backend 注入入口。此处隔离私有桥接，保护代理 CONNECT 的证书主机名。
        pool = transport._pool
        pool._network_backend = _SNIBackend(pool._network_backend, hostname, address,
                                            httpx.URL(proxy).scheme == 'https')
    return transport


def _connection(data):
    try:
        provider_id = data.get('provider_id')
        connection = dict(data.get('connection') or {})
        if provider_id:
            with config_lock():
                if not data.get('revision') or data['revision'] != public_catalog()['revision']:
                    raise ValueError('MODEL_CONFIG_CONFLICT')
                return provider_draft(connection, provider_id)
        connection.setdefault('name', '未保存的模型连接')
        return provider_draft(connection)
    except ValueError as exc:
        code = str(exc)
        allowed = {'MODEL_CONFIG_CONFLICT', 'MODEL_CONFIG_INVALID', 'MODEL_CONFIG_BUSY',
                   'MODEL_DESTINATION_REQUIRES_KEY', 'MODEL_PROVIDER_NOT_FOUND', 'MODEL_PROVIDER_INVALID'}
        raise _error(code if code in allowed else 'MODEL_PROVIDER_INVALID') from None


def _parse_catalog(body, protocol):
    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError
        rows = data.get('models' if protocol == 'ollama' else 'data')
        if not isinstance(rows, list):
            raise ValueError
        ids = set()
        complete = not bool(data.get('has_more') or data.get('next') or data.get('next_page')
                            or data.get('next_page_token') or data.get('next_cursor'))
        if len(rows) > _MAX_MODELS:
            complete = False
        for item in rows[:_MAX_MODELS]:
            name = item.get('name' if protocol == 'ollama' else 'id') if isinstance(item, dict) else None
            if not isinstance(name, str) or not name.strip() or len(name) > 160 or any(ord(c) < 32 for c in name):
                raise ValueError
            ids.add(name.strip())
        return {'models': [{'model': name, 'capability_status': 'unverified'} for name in sorted(ids)],
                'count': len(ids), 'complete': complete, 'checked_at': datetime.now(timezone.utc).isoformat()}
    except (ValueError, TypeError, RecursionError):
        raise _error('MODEL_CATALOG_INVALID', '供应商返回的模型目录格式不受支持', 502) from None


def discover_models(data, *, transport=None):
    if not _SLOTS.acquire(blocking=False):
        raise _error('MODEL_DISCOVERY_BUSY', '模型目录检测繁忙，请稍后重试', 429)
    try:
        provider = _connection(data)
        original = httpx.URL(provider['base_url'])
        suffix = '/api/tags' if provider['protocol'] == 'ollama' else '/models'
        target = httpx.URL(str(original).rstrip('/') + suffix)
        address = original.host
        if original.scheme == 'https':
            address = _public_address(original.host, original.port or 443)
        elif provider['protocol'] == 'ollama' and original.host == 'localhost':
            # 回环例外也固定为 IP，不依赖宿主 hosts/DNS 将 localhost 指向其他地址。
            address = '127.0.0.1'
        target = target.copy_with(host=address)
        headers = {'Host': original.netloc.decode('ascii'), 'Accept': 'application/json', 'Accept-Encoding': 'identity'}
        if provider['api_key']:
            headers['Authorization'] = 'Bearer ' + provider['api_key']
        network = transport if transport is not None else _transport(provider, original.host, address)
        timeout = httpx.Timeout(connect=min(provider['connect_timeout'], 10),
                                read=min(provider['read_timeout'], 10), write=5, pool=2)
        started = time.monotonic()
        with httpx.Client(transport=network, trust_env=False, follow_redirects=False, timeout=timeout) as client:
            # 代理自身的 HTTPS 握手不能使用目标域名；隧道内目标 TLS 由 _SNIStream 单独处理。
            extensions = {} if provider['proxy_url'] else {'sni_hostname': original.host}
            with client.stream('GET', target, headers=headers, extensions=extensions) as response:
                if response.is_redirect:
                    raise _error('MODEL_DISCOVERY_REDIRECT_FORBIDDEN', '模型目录不允许重定向')
                status_errors = {401: 'MODEL_AUTH_FAILED', 403: 'MODEL_AUTH_FAILED',
                                 404: 'MODEL_CATALOG_UNSUPPORTED', 405: 'MODEL_CATALOG_UNSUPPORTED',
                                 429: 'MODEL_RATE_LIMITED'}
                if response.status_code in status_errors:
                    raise _error(status_errors[response.status_code])
                if not response.is_success:
                    raise _error('MODEL_DISCOVERY_HTTP_FAILED', status=502)
                if response.headers.get('content-encoding', 'identity').lower() not in {'', 'identity'}:
                    raise _error('MODEL_CATALOG_ENCODING_UNSUPPORTED', '模型目录不接受压缩响应，避免解压大小失控', 502)
                length = response.headers.get('content-length', '')
                if length.isdigit() and int(length) > _MAX_BYTES:
                    raise _error('MODEL_CATALOG_TOO_LARGE', '供应商模型目录超过安全大小限制', 502)
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    if time.monotonic() - started > 20:
                        raise _error('MODEL_DISCOVERY_TIMEOUT', '模型目录读取超时', 504)
                    if len(body) + len(chunk) > _MAX_BYTES:
                        raise _error('MODEL_CATALOG_TOO_LARGE', '供应商模型目录超过安全大小限制', 502)
                    body.extend(chunk)
                result = _parse_catalog(body, provider['protocol'])
                if 'rel="next"' in response.headers.get('link', ''):
                    result['complete'] = False
                return result
    except httpx.TimeoutException:
        raise _error('MODEL_DISCOVERY_TIMEOUT', '模型目录连接或读取超时', 504) from None
    except httpx.HTTPError:
        raise _error('MODEL_DISCOVERY_CONNECTION_FAILED', '模型目录连接失败，请检查地址、TLS 或代理', 502) from None
    except (ImportError, ValueError):
        raise _error('MODEL_DISCOVERY_NETWORK_CONFIG_INVALID', '模型网络配置不受支持') from None
    finally:
        _SLOTS.release()
