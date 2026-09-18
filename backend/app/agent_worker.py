import time
import httpx
from .config import settings, model_settings
from agent_core.harness import run_loop
from agent_core.model_adapter import ModelAdapter, ModelError
from agent_core.ollama_adapter import OllamaAdapter


class Gateway:
    def __init__(self, client, context):
        self.client, self.run_id, self.epoch = client, context["id"], context["epoch"]
        self.rpc_id=0
        self.tool_annotations={}

    def rpc(self,method,params,notification=False):
        self.rpc_id+=1
        body={'jsonrpc':'2.0','method':method,'params':params}
        if not notification:body['id']=self.rpc_id
        response=self.client.post(f'/internal/runs/{self.run_id}/mcp',json=body,headers={
            'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-06-18',
            'X-Agent-Run-Epoch':str(self.epoch)})
        response.raise_for_status()
        if notification:return None
        value=response.json()
        if value.get('error'):raise RuntimeError('MCP_REQUEST_FAILED')
        return value['result']

    def discover(self):
        result=self.rpc('initialize',{'protocolVersion':'2025-06-18','capabilities':{},
            'clientInfo':{'name':'agent-core','version':'0.1.0'}})
        if result['protocolVersion']!='2025-06-18':raise RuntimeError('MCP_VERSION_UNSUPPORTED')
        self.rpc('notifications/initialized',{},notification=True)
        catalog=self.rpc('tools/list',{})['tools']
        self.tool_annotations={t['name']:dict(t.get('annotations') or {}) for t in catalog}
        return [{'type':'function','function':{'name':t['name'],'description':t['description'],
            'parameters':t['inputSchema'],'strict':True}} for t in catalog]
    def post(self, path, body=None):
        r = self.client.post(f"/internal/runs/{self.run_id}/{path}", json={"epoch": self.epoch, **(body or {})})
        if not r.is_success:
            try:
                code = ((r.json().get("error") or {}).get("code"))
            except (ValueError, AttributeError):
                code = None
            if code:
                raise RuntimeError(str(code))
            r.raise_for_status()
        return r.json()
    def check(self): return self.post("check")
    def execute(self, sequence, key, arguments):
        result=self.rpc('tools/call',{'name':key,'arguments':arguments,'_meta':{'agent/sequence':sequence}})
        if result.get('isError'):
            structured=result.get('structuredContent')
            if isinstance(structured,dict) and isinstance(structured.get('tool_error'),dict):
                return structured
            content=result.get('content') or []
            message=next((item.get('text') for item in content
                          if isinstance(item,dict) and isinstance(item.get('text'),str)),
                         '工具拒绝了本次请求，请根据错误信息修正或向用户澄清。')
            return {'tool_error':{'code':result.get('errorCode') or 'TOOL_REJECTED','message':message}}
        structured=result.get('structuredContent')
        if not isinstance(structured,dict):raise RuntimeError('MCP_TOOL_RESULT_INVALID')
        return structured
    def checkpoint(self, checkpoint): return self.post("checkpoint", {"checkpoint": checkpoint})
    def finish(self, result): return self.post("finish", {"result": result})


def create_model(config):
    if not config.llm_enabled or not config.worker_secret or (config.llm_provider=='company' and (not config.llm_base_url or not config.llm_model or (not config.llm_api_key and not config.llm_trusted_http_origin))):
        raise SystemExit("Model or worker configuration missing; no simulated model is substituted.")
    return OllamaAdapter(config.ollama_base_url,config.ollama_model,config.llm_max_output_tokens,context_window=config.llm_context_window) if config.llm_provider=='ollama' else ModelAdapter(config.llm_base_url, config.llm_api_key, config.llm_model,
                         config.llm_max_output_tokens, proxy=config.llm_proxy_url,
                         trusted_http_origin=config.llm_trusted_http_origin,
                         tls_max_version=config.llm_tls_max_version,
                         tls_key_exchange=config.llm_tls_key_exchange,
                         connect_timeout=config.llm_connect_timeout, read_timeout=config.llm_read_timeout)


def main():
    config = settings()
    runtime_config = model_settings()
    model = create_model(runtime_config)
    model_config_version = runtime_config.config_version
    with httpx.Client(base_url=config.api_base_url, headers={"Authorization": "Bearer "+config.worker_secret}, timeout=30, trust_env=False) as client:
        while True:
            try:
                runtime_config = model_settings()
                if runtime_config.config_version != model_config_version:
                    previous_model = model
                    model = create_model(runtime_config)
                    model_config_version = runtime_config.config_version
                    close = getattr(previous_model, "close", None)
                    if callable(close):
                        close()
                response = client.post("/internal/runs/claim"); response.raise_for_status()
                context = response.json()["run"]
                if not context: time.sleep(0.5); continue
                gateway = Gateway(client, context)
                try:
                    context['tools']=gateway.discover()
                    context['tool_annotations']=gateway.tool_annotations
                    run_loop(context, model, gateway, max_turns=runtime_config.llm_max_turns,
                             context_window=runtime_config.llm_context_window,
                             max_output_tokens=runtime_config.llm_max_output_tokens)
                except Exception as exc:
                    # Do not send arbitrary upstream responses or credentials into business logs.
                    if isinstance(exc, (ModelError, RuntimeError)):
                        code = str(exc) or type(exc).__name__
                    else:
                        code = type(exc).__name__
                    gateway.post("fail", {"code": code})
            except httpx.HTTPError:
                time.sleep(5)


if __name__ == "__main__": main()
