"""Company model transport; TLS policy is explicit and certificates stay verified."""
import ssl
import json
import time
from ipaddress import ip_address, ip_network

import httpx


class ModelError(RuntimeError):
    """Stable error codes only; never retain upstream bodies or credentials."""


def tls_context(max_version: str = "auto", key_exchange: str = "auto") -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if max_version == "1.2":
        context.maximum_version = ssl.TLSVersion.TLSv1_2
    elif max_version != "auto":
        raise ValueError("Unsupported model TLS maximum version")
    if key_exchange == "x25519":
        context.set_ecdh_curve("X25519")
    elif key_exchange != "auto":
        raise ValueError("Unsupported model TLS key exchange")
    return context


class ModelAdapter:
    def __init__(self, base_url, key, model, max_output_tokens=2048, proxy=None,
                 tls_max_version="auto", connect_timeout=10, read_timeout=60,
                 transport=None, tls_key_exchange="auto", trusted_http_origin=""):
        url = httpx.URL(base_url)
        if url.username or url.password or url.query or url.fragment or not url.host:
            raise ValueError("Invalid model base URL")
        if url.scheme == "http":
            trusted = httpx.URL(trusted_http_origin)
            try:
                address = ip_address(url.host)
                private = any(address in ip_network(network) for network in
                              ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
            except ValueError:
                private = False
            if (not private or trusted.scheme != "http" or trusted.host != url.host
                    or trusted.port != url.port or trusted.path not in ("", "/")
                    or trusted.username or trusted.password or trusted.query or trusted.fragment):
                raise ValueError("HTTP model endpoint requires an exact trusted private-IP origin")
            if proxy or key:
                raise ValueError("Trusted HTTP model requests must not use a proxy or bearer credentials")
        elif url.scheme != "https":
            raise ValueError("Model endpoint must use HTTPS or an explicitly trusted private-IP origin")
        self.url = str(url).rstrip("/")+"/chat/completions"
        self.key, self.model, self.max_tokens = key, model, max_output_tokens
        self.proxy = proxy or None
        self.ssl_context = tls_context(tls_max_version, tls_key_exchange)
        self.timeout = httpx.Timeout(connect=connect_timeout, read=read_timeout, write=15, pool=5)
        self.transport = transport
        self.last_metrics = {}

    def generate(self, messages, tools):
        started = time.perf_counter()
        payload = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens,
                   "temperature": 0.2}
        if tools:
            payload["tools"] = tools
        self.last_metrics = {'tool_count':len(tools), 'request_bytes':len(json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf-8'))}
        def trace(event, info):
            # Timing only: no headers, messages, response bodies or credential values.
            field = {'connection.connect_tcp.complete':'tcp_ms','connection.start_tls.complete':'tls_ms',
                     'proxy.start_tls.complete':'tls_ms','http11.send_request_body.complete':'request_sent_ms',
                     'http11.receive_response_headers.complete':'response_headers_ms'}.get(event)
            if field: self.last_metrics[field] = round((time.perf_counter()-started)*1000)
        try:
            # No automatic redirects, implicit environment proxy, insecure TLS or repeated POST.
            with httpx.Client(verify=self.ssl_context, timeout=self.timeout, proxy=self.proxy,
                              trust_env=False, follow_redirects=False, transport=self.transport) as client:
                headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
                response = client.post(self.url, headers=headers, json=payload, extensions={'trace':trace})
                if response.status_code in (401, 403):
                    raise ModelError("MODEL_AUTH_FAILED")
                if response.status_code == 429:
                    raise ModelError("MODEL_RATE_LIMITED")
                if not response.is_success:
                    raise ModelError("MODEL_HTTP_FAILED")
                data = response.json()
                choice = data["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise ModelError("MODEL_OUTPUT_TRUNCATED")
                message = choice["message"]
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    raise ModelError("MODEL_OUTPUT_INVALID")
                return message
        except httpx.ConnectTimeout:
            raise ModelError("MODEL_CONNECT_TIMEOUT") from None
        except httpx.ReadTimeout:
            raise ModelError("MODEL_READ_TIMEOUT") from None
        except httpx.HTTPError:
            raise ModelError("MODEL_NETWORK_ERROR") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise ModelError("MODEL_OUTPUT_INVALID") from None
        finally:
            self.last_metrics['total_ms'] = round((time.perf_counter()-started)*1000)
