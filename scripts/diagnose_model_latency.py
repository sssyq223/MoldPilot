"""Synthetic model transport timings only; never print credentials or upstream text."""
import sys,time,json,hashlib,subprocess,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import httpx
from app.config import settings
from app.model_adapter import tls_context
from app.harness import SYSTEM
from app.tool_gateway import TOOLS,tool_schema
s=settings()
mode=sys.argv[1] if len(sys.argv)>1 else 'minimal'
started=time.perf_counter();events=[]
def trace(event,info):
    if event.endswith('.complete') or event.endswith('.failed'): events.append([event,round(time.perf_counter()-started,3)])
payload={'model':s.llm_model,'messages':[{'role':'user','content':'你好，请简短回复。'}],'max_tokens':64,'temperature':0.2}
if mode in {'tools','one_tool','system','none','no_strict','four','eight','stream_tools','segmented','curl','explicit_proxy','tls12','lan_tools','lan_query'}:
    payload['messages'].insert(0,{'role':'system','content':SYSTEM})
    if mode!='system':payload['tools']=[tool_schema(k) for k in (list(TOOLS)[:1] if mode=='one_tool' else TOOLS)]
    payload['max_tokens']=512
if mode in {'four','eight'}:payload['tools']=payload['tools'][:4 if mode=='four' else 8]
if mode=='none':payload['tool_choice']='none'
if mode=='no_strict':
    for tool in payload['tools']:tool['function'].pop('strict',None)
if mode=='padding':
    payload['messages'].insert(0,{'role':'system','content':'以下重复文本仅用于传输诊断，不需要复述。'+('测试资料。'*1500)})
    payload['max_tokens']=512
if mode=='lan_query':payload['messages'][-1]['content']='你好，帮我看看我有权限查看的模具项目及状态。'
if mode in {'stream','stream_tools'}:payload['stream']=True
result={'mode':mode}
result['tool_count']=len(payload.get('tools',[]));result['request_bytes']=len(json.dumps(payload,ensure_ascii=False).encode('utf-8'))
body_bytes=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf-8')
result['body_bytes']=len(body_bytes);result['body_sha256']=hashlib.sha256(body_bytes).hexdigest()
headers={'Authorization':'Bearer '+s.llm_api_key,'Content-Type':'application/json','Content-Length':str(len(body_bytes))}
target=s.llm_base_url.rstrip('/')+'/chat/completions'
if mode.startswith('lan_'):
    headers.pop('Authorization')
content=(body_bytes[i:i+512] for i in range(0,len(body_bytes),512)) if mode=='segmented' else body_bytes
if mode=='curl':
    with tempfile.TemporaryDirectory(prefix='mold-model-diag-') as folder:
        request_file=Path(folder)/'request.json';response_file=Path(folder)/'response.json'
        request_file.write_bytes(body_bytes)
        config='\n'.join(['url = '+json.dumps(s.llm_base_url.rstrip('/')+'/chat/completions'),
            'request = "POST"','header = "Content-Type: application/json"',
            'header = '+json.dumps('Authorization: Bearer '+s.llm_api_key),
            'data-binary = '+json.dumps('@'+request_file.as_posix()),
            'output = '+json.dumps(response_file.as_posix()),'connect-timeout = 10','max-time = 30',
            'noproxy = "*"','silent','show-error'])
        proc=subprocess.run(['curl.exe','--config','-','--write-out','%{http_code} %{time_connect} %{time_appconnect} %{time_starttransfer} %{time_total}'],input=config,text=True,capture_output=True,timeout=35)
        result['curl_exit']=proc.returncode;result['curl_timings']=proc.stdout
        if proc.returncode==0:
            result['response_bytes']=response_file.stat().st_size
            try:result['usage']=json.loads(response_file.read_bytes()).get('usage')
            except ValueError:pass
        print(json.dumps(result,ensure_ascii=True),flush=True)
    raise SystemExit()
try:
    with httpx.Client(verify=tls_context('1.2' if mode=='tls12' else s.llm_tls_max_version,s.llm_tls_key_exchange),trust_env=False,proxy='http://127.0.0.1:7890' if mode=='explicit_proxy' else s.llm_proxy_url or None,timeout=httpx.Timeout(connect=10,read=30,write=10,pool=5)) as client:
        with client.stream('POST',target,headers=headers,content=content,extensions={'trace':trace}) as response:
            result['status']=response.status_code;result['headers_seconds']=round(time.perf_counter()-started,3)
            body=b'';first=None
            for chunk in response.iter_bytes():
                if first is None:first=round(time.perf_counter()-started,3)
                body+=chunk
            result['first_body_seconds']=first;result['bytes']=len(body)
            if not payload.get('stream') and response.is_success:
                data=json.loads(body);result['usage']=data.get('usage');result['finish_reason']=data.get('choices',[{}])[0].get('finish_reason');msg=data.get('choices',[{}])[0].get('message',{});result['synthetic_reply']=(msg.get('content') or '')[:500];result['tool_calls']=msg.get('tool_calls')
except Exception as e:result['error_type']=type(e).__name__
result['total_seconds']=round(time.perf_counter()-started,3);result['events']=events
print(json.dumps(result,ensure_ascii=True),flush=True)
