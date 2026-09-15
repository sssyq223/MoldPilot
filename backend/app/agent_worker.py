import time
import httpx
from .config import settings, model_settings
from .harness import ModelAdapter, run_loop
from .model_adapter import ModelError
from .ollama_adapter import OllamaAdapter


class Gateway:
    def __init__(self, client, context):
        self.client, self.run_id, self.epoch = client, context["id"], context["epoch"]
        self.rpc_id=0

    def rpc(self,method,params,notification=False):
        self.rpc_id+=1
        body={'jsonrpc':'2.0','method':method,'params':params}
        if not notification:body['id']=self.rpc_id
        response=self.client.post(f'/internal/runs/{self.run_id}/mcp',json=body,headers={
            'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-06-18',
            'X-Mold-Run-Epoch':str(self.epoch)})
        response.raise_for_status()
        if notification:return None
        value=response.json()
        if value.get('error'):raise RuntimeError('MCP_REQUEST_FAILED')
        return value['result']

    def discover(self):
        result=self.rpc('initialize',{'protocolVersion':'2025-06-18','capabilities':{},
            'clientInfo':{'name':'mold-harness','version':'0.1.0'}})
        if result['protocolVersion']!='2025-06-18':raise RuntimeError('MCP_VERSION_UNSUPPORTED')
        self.rpc('notifications/initialized',{},notification=True)
        catalog=self.rpc('tools/list',{})['tools']
        return [{'type':'function','function':{'name':t['name'],'description':t['description'],
            'parameters':t['inputSchema'],'strict':True}} for t in catalog]
    def post(self, path, body=None):
        r = self.client.post(f"/internal/runs/{self.run_id}/{path}", json={"epoch": self.epoch, **(body or {})})
        r.raise_for_status(); return r.json()
    def check(self): return self.post("check")
    def execute(self, sequence, key, arguments):
        result=self.rpc('tools/call',{'name':key,'arguments':arguments,'_meta':{'mold/sequence':sequence}})
        if result.get('isError'):raise ModelError('TOOL_BUSINESS_REJECTED')
        return result['structuredContent']
    def checkpoint(self, checkpoint): return self.post("checkpoint", {"checkpoint": checkpoint})
    def finish(self, result): return self.post("finish", {"result": result})


def create_model(config):
    if not config.llm_enabled or not config.worker_secret or (config.llm_provider=='company' and (not config.llm_base_url or not config.llm_model or (not config.llm_api_key and not config.llm_trusted_http_origin))):
        raise SystemExit("Model or worker configuration missing; no simulated model is substituted.")
    return OllamaAdapter(config.ollama_base_url,config.ollama_model,config.llm_max_output_tokens) if config.llm_provider=='ollama' else ModelAdapter(config.llm_base_url, config.llm_api_key, config.llm_model,
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
                    model = create_model(runtime_config)
                    model_config_version = runtime_config.config_version
                response = client.post("/internal/runs/claim"); response.raise_for_status()
                context = response.json()["run"]
                if not context: time.sleep(0.5); continue
                gateway = Gateway(client, context)
                try:
                    context['tools']=gateway.discover()
                    run_loop(context, model, gateway, max_turns=config.llm_max_turns)
                except Exception as exc:
                    # Do not send arbitrary upstream responses or credentials into business logs.
                    gateway.post("fail", {"code": str(exc) if isinstance(exc, ModelError) else type(exc).__name__})
            except httpx.HTTPError:
                time.sleep(5)


if __name__ == "__main__": main()
