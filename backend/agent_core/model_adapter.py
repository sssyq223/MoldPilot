"""Company model transport; TLS policy is explicit and certificates stay verified."""
import ssl
import json
import time
from ipaddress import ip_address, ip_network

import httpx
from .model_capabilities import reasoning_parameters


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
                 transport=None, tls_key_exchange="auto", trusted_http_origin="",
                 reasoning_effort='', reasoning_policy='default'):
        self._reasoning_parameters = reasoning_parameters('company', model, reasoning_policy, reasoning_effort)
        self._token_parameter = 'max_completion_tokens' if reasoning_policy == 'openai' else 'max_tokens'
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
        self._transport = transport
        self.last_metrics = {}
        # The worker owns one adapter for many model turns. Reusing the client
        # also reuses healthy HTTP/TLS connections instead of handshaking for
        # ToolSearch, the business tool call, and the final answer separately.
        self.client = self._build_client()

    def _build_client(self):
        return httpx.Client(
            verify=self.ssl_context,
            timeout=self.timeout,
            proxy=self.proxy,
            trust_env=False,
            follow_redirects=False,
            transport=self._transport,
        )

    @property
    def transport(self):
        return self._transport

    @transport.setter
    def transport(self, value):
        """Replace the transport without leaving the reusable client stale."""
        if value is self._transport:
            return
        self._transport = value
        if hasattr(self, "client"):
            self.client.close()
            self.client = self._build_client()

    def close(self):
        self.client.close()

    def _record_retry(self, reason, attempt_started):
        self.last_metrics['retry_count'] = self.last_metrics.get('retry_count', 0) + 1
        self.last_metrics['retry_reason'] = reason
        self.last_metrics['retry_wait_ms'] = round(
            self.last_metrics.get('retry_wait_ms', 0)
            + (time.perf_counter() - attempt_started) * 1000
        )

    def _payload(self, messages, tools, stream=False):
        payload = {"model": self.model, "messages": messages, self._token_parameter: self.max_tokens,
                   **self._reasoning_parameters}
        if self._token_parameter == 'max_tokens':
            payload['temperature'] = 0.2
        if tools:
            payload["tools"] = tools
        else:
            # A no-tool turn is the Harness terminal protocol, not free-form
            # chat. Ask compatible OpenAI-style providers to constrain it as a
            # JSON object instead of trying to recover prose/YAML afterward.
            payload["response_format"] = {"type": "json_object"}
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _trace(self, started):
        def trace(event, info):
            # Timing only: no headers, messages, response bodies or credential values.
            field = {'connection.connect_tcp.complete':'tcp_ms','connection.start_tls.complete':'tls_ms',
                     'proxy.start_tls.complete':'tls_ms','http11.send_request_body.complete':'request_sent_ms',
                     'http11.receive_response_headers.complete':'response_headers_ms'}.get(event)
            if field: self.last_metrics[field] = round((time.perf_counter()-started)*1000)
        return trace

    def _record_usage(self, usage):
        if not isinstance(usage, dict):
            return
        for source, target in (("prompt_tokens", "prompt_tokens"),
                               ("completion_tokens", "completion_tokens"),
                               ("total_tokens", "total_tokens")):
            if isinstance(usage.get(source), int):
                self.last_metrics[target] = usage[source]
        details = usage.get("completion_tokens_details")
        if isinstance(details, dict) and isinstance(details.get("reasoning_tokens"), int):
            self.last_metrics["reasoning_tokens"] = details["reasoning_tokens"]

    def _record_finish_reason(self, reason):
        if reason is not None:
            self.last_metrics['finish_reason'] = reason if reason in ('stop', 'length', 'tool_calls', 'function_call', 'content_filter') else 'other'

    @staticmethod
    def _check_status(response):
        if response.status_code in (401, 403):
            raise ModelError("MODEL_AUTH_FAILED")
        if response.status_code == 429:
            raise ModelError("MODEL_RATE_LIMITED")
        if not response.is_success:
            raise ModelError("MODEL_HTTP_FAILED")

    def generate(self, messages, tools):
        started = time.perf_counter()
        payload = self._payload(messages, tools)
        self.last_metrics = {'tool_count':len(tools), 'request_bytes':len(json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf-8'))}
        try:
            headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
            for attempt in range(2):
                attempt_started = time.perf_counter()
                try:
                    response = self.client.post(
                        self.url, headers=headers, json=payload,
                        extensions={'trace': self._trace(started)},
                    )
                except httpx.ConnectTimeout:
                    if attempt == 0:
                        # No response or model delta exists yet, so one retry
                        # cannot duplicate a business tool side effect.
                        self._record_retry('connect_timeout_before_response', attempt_started)
                        continue
                    raise
                break
            self._check_status(response)
            data = response.json()
            self._record_usage(data.get("usage") if isinstance(data, dict) else {})
            choice = data["choices"][0]
            self._record_finish_reason(choice.get('finish_reason'))
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

    def generate_stream(self, messages, tools, on_update):
        """Return one assistant message while publishing coalesced SSE snapshots."""
        started = time.perf_counter()
        payload = self._payload(messages, tools, stream=True)
        self.last_metrics = {
            'tool_count': len(tools),
            'request_bytes': len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')),
        }
        content = ""
        reasoning = ""
        calls = {}
        finish_reason = None
        first_chunk = False

        def snapshot():
            ordered_calls = []
            for index in sorted(calls):
                call = calls[index]
                function = call["function"]
                ordered_calls.append({
                    "id": call.get("id") or f"stream_call_{index}",
                    "type": call.get("type") or "function",
                    "function": {"name": function.get("name", ""),
                                 # SiliconFlow omits argument deltas for a
                                 # valid zero-parameter call. Normalize that
                                 # provider representation to OpenAI's JSON
                                 # object string before the Harness validates it.
                                 "arguments": function.get("arguments") or "{}"},
                })
            return {
                "role": "assistant",
                "content": content or None,
                **({"reasoning_content": reasoning} if reasoning else {}),
                **({"tool_calls": ordered_calls} if ordered_calls else {}),
            }

        try:
            headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
            # Retry only before the provider emits a delta. At that point no
            # tool has run and nothing user-visible needs deduplication.
            for attempt in range(2):
                attempt_started = time.perf_counter()
                try:
                    with self.client.stream("POST", self.url, headers=headers, json=payload,
                                            extensions={'trace': self._trace(started)}) as response:
                        self.last_metrics['http_status'] = response.status_code
                        if response.status_code in {500, 502, 503, 504} and attempt == 0:
                            response.read()
                            self._record_retry('upstream_5xx', attempt_started)
                            continue
                        self._check_status(response)
                        for line in response.iter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if raw == "[DONE]":
                                break
                            data = json.loads(raw)
                            self._record_usage(data.get("usage"))
                            choices = data.get("choices")
                            if not isinstance(choices, list) or not choices:
                                continue
                            choice = choices[0]
                            finish_reason = choice.get("finish_reason") or finish_reason
                            self._record_finish_reason(finish_reason)
                            delta = choice.get("delta")
                            if not isinstance(delta, dict):
                                continue
                            if not first_chunk:
                                self.last_metrics['first_chunk_ms'] = round((time.perf_counter()-started)*1000)
                                first_chunk = True
                            if isinstance(delta.get("content"), str):
                                content += delta["content"]
                            if isinstance(delta.get("reasoning_content"), str):
                                reasoning += delta["reasoning_content"]
                            for position, fragment in enumerate(delta.get("tool_calls") or []):
                                if not isinstance(fragment, dict):
                                    continue
                                index = fragment.get("index") if isinstance(fragment.get("index"), int) else position
                                call = calls.setdefault(index, {"id": "", "type": "function",
                                                                "function": {"name": "", "arguments": ""}})
                                if isinstance(fragment.get("id"), str) and fragment["id"]:
                                    call["id"] = fragment["id"]
                                if isinstance(fragment.get("type"), str) and fragment["type"]:
                                    call["type"] = fragment["type"]
                                function = fragment.get("function")
                                if isinstance(function, dict):
                                    if isinstance(function.get("name"), str):
                                        call["function"]["name"] += function["name"]
                                    if isinstance(function.get("arguments"), str):
                                        call["function"]["arguments"] += function["arguments"]
                            on_update(snapshot())
                except httpx.ConnectTimeout:
                    if attempt == 0 and not first_chunk:
                        self._record_retry('connect_timeout_before_first_chunk', attempt_started)
                        continue
                    raise
                except httpx.ReadTimeout:
                    if attempt == 0 and not first_chunk:
                        self._record_retry('read_timeout_before_first_chunk', attempt_started)
                        continue
                    raise
                break
            if finish_reason == "length":
                raise ModelError("MODEL_OUTPUT_TRUNCATED")
            message = snapshot()
            if not content and not reasoning and not message.get("tool_calls"):
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
