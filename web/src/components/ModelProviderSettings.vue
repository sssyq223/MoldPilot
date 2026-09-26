<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {BrainCircuit,Plus,RefreshCw,Search,Trash2} from 'lucide-vue-next'
import {api,post} from '../api'
const emit=defineEmits<{updated:[model:string]}>()
const catalog=ref<any>({providers:[],models:[],reasoning_policies:[]}),providerId=ref(''),draft=ref<any>({}),key=ref(''),clearKey=ref(false)
const loading=ref(false),busy=ref(false),detecting=ref(false),error=ref(''),notice=ref(''),directory=ref<any|null>(null),checked=ref<string[]>([]),search=ref(''),manual=ref(''),editor=ref<any|null>(null)
let detectionEpoch=0,initialProxy='',disposed=false
const models=computed(()=>catalog.value.models.filter((m:any)=>m.provider_id===providerId.value))
const configured=computed(()=>new Set(models.value.map((m:any)=>m.model)))
const visibleDirectory=computed(()=>(directory.value?.models||[]).filter((m:any)=>m.model.toLowerCase().includes(search.value.toLowerCase())))
const policies=computed(()=>(catalog.value.reasoning_policies||[]).filter((p:any)=>p.protocols.includes(draft.value.protocol)))
const effortLevels=computed(()=>{
 if(!editor.value)return []
 if(editor.value.reasoning_policy==='default')return editor.value.original_model===editor.value.model&&editor.value.original_policy==='default'?(editor.value.reasoning?.levels||[]):[]
 return policies.value.find((p:any)=>p.id===editor.value.reasoning_policy)?.levels||[]
})
function connection(){
 const value:any={}
 for(const field of ['name','protocol','enabled','base_url','trusted_http_origin','proxy_url','tls_max_version','tls_key_exchange','connect_timeout','read_timeout'])if(draft.value[field]!==undefined)value[field]=draft.value[field]
 if(draft.value.proxy_credentials_configured&&value.proxy_url===initialProxy)delete value.proxy_url
 return {...value,api_key:key.value,clear_api_key:clearKey.value}
}
watch(()=>JSON.stringify(connection()),()=>{detectionEpoch++;directory.value=null;checked.value=[]})
function selectProvider(provider:any){
 providerId.value=provider?.id||'';key.value='';clearKey.value=false;editor.value=null;directory.value=null;checked.value=[];search.value='';error.value='';notice.value=''
 draft.value=provider?{...provider}:{name:'新供应商',protocol:'company',enabled:true,base_url:'',trusted_http_origin:'',proxy_url:'',tls_max_version:'auto',tls_key_exchange:'auto',connect_timeout:10,read_timeout:60}
 initialProxy=draft.value.proxy_url||'';detectionEpoch++
}
function apply(result:any,id=providerId.value){if(disposed)return;catalog.value=result;selectProvider(result.providers.find((p:any)=>p.id===id)||result.providers.find((p:any)=>p.id===result.models.find((m:any)=>m.id===result.default_model_id)?.provider_id)||result.providers[0]);emit('updated',result.models.find((m:any)=>m.id===result.default_model_id)?.model||'未配置模型')}
async function reload(){loading.value=true;try{apply(await api('/model-catalog'))}catch(e:any){error.value=e.message}finally{loading.value=false}}
async function saveConnection(){
 const previousIds=new Set(catalog.value.providers.map((p:any)=>p.id))
 const result=await api(providerId.value?`/model-providers/${providerId.value}`:'/model-providers',{
  method:providerId.value?'PUT':'POST',body:JSON.stringify({revision:catalog.value.revision,connection:connection()})})
 const id=providerId.value||result.providers.find((p:any)=>!previousIds.has(p.id))?.id
 apply(result,id);return id
}
async function saveProvider(){busy.value=true;error.value='';try{await saveConnection();notice.value='供应商已保存，未切换聊天模型。'}catch(e:any){error.value=e.message}finally{busy.value=false}}
async function detect(){
 if(detecting.value||busy.value)return
 const current=++detectionEpoch;detecting.value=true;error.value='';directory.value=null;checked.value=[]
 try{const result=await post('/model-providers/discover',{provider_id:providerId.value||undefined,revision:providerId.value?catalog.value.revision:undefined,connection:connection()});if(current===detectionEpoch)directory.value=result}
 catch(e:any){if(current===detectionEpoch)error.value=e.message+'；若供应商不提供模型目录，可手动添加准确模型 ID。'}
 finally{detecting.value=false}
}
async function addModels(names:string[]){
 if(!names.length||busy.value)return
 busy.value=true;error.value='';let connectionSaved=false
 try{
  const id=await saveConnection();connectionSaved=true
  if(disposed)return
  const result=await post('/catalog-models/batch',{revision:catalog.value.revision,models:names.map(model=>({provider_id:id,model,name:model}))})
  apply(result,id);manual.value='';notice.value=`已添加 ${names.length} 个模型。请核对各模型的预算与思考能力；目录成功不代表生成调用已验证。`
 }catch(e:any){error.value=(connectionSaved?'供应商已保存，模型添加结果请刷新核对：':'')+e.message}finally{busy.value=false}
}
function editModel(model:any){editor.value={...model,original_model:model.model,original_policy:model.reasoning_policy};error.value=''}
async function saveModel(){
 if(!editor.value)return
 busy.value=true;error.value=''
 try{
  const value:any={};for(const field of ['model','name','enabled','max_output_tokens','context_window','max_turns','reasoning_policy','default_reasoning_effort'])value[field]=editor.value[field]
  const result=await api(`/catalog-models/${editor.value.id}`,{method:'PUT',body:JSON.stringify({revision:catalog.value.revision,model:value})})
  apply(result);notice.value='模型参数已保存；已发出的任务仍使用原参数。'
 }catch(e:any){error.value=e.message}finally{busy.value=false}
}
async function removeModel(model:any){
 if(!window.confirm(`移除模型 ${model.name}？已绑定的任务不会自动改用其他模型。`))return
 busy.value=true;error.value=''
 try{apply(await api(`/catalog-models/${model.id}`,{method:'DELETE',body:JSON.stringify({revision:catalog.value.revision})}));notice.value='模型已移除。'}catch(e:any){error.value=e.message}finally{busy.value=false}
}
async function removeProvider(){
 if(!providerId.value||!window.confirm('移除此供应商及其模型？文档识别引用或系统默认模型仍在使用时会阻止删除。'))return
 busy.value=true;error.value=''
 try{apply(await api(`/model-providers/${providerId.value}`,{method:'DELETE',body:JSON.stringify({revision:catalog.value.revision})}));notice.value='供应商已移除。'}catch(e:any){error.value=e.message}finally{busy.value=false}
}
async function setDefault(model:any){
 busy.value=true;error.value=''
 try{apply(await api('/model-catalog/default',{method:'PUT',body:JSON.stringify({revision:catalog.value.revision,model_id:model.id})}));notice.value='系统默认模型已更新，仅影响未作独立选择的新任务。'}catch(e:any){error.value=e.message}finally{busy.value=false}
}
onMounted(reload)
onBeforeUnmount(()=>{disposed=true;detectionEpoch++;key.value=''})
</script>

<template>
 <section class="provider-settings">
  <div class="provider-heading"><div><h2>供应商与模型</h2><p class="muted">一套连接共用一个 Key，可添加多个模型。聊天选择按会话隔离。</p></div><button type="button" :disabled="busy||loading" @click="selectProvider(null)"><Plus :size="15"/>添加供应商</button><button type="button" :disabled="busy||loading" title="重新读取配置" @click="reload"><RefreshCw :size="15"/></button></div>
  <p v-if="loading" role="status">正在读取配置…</p>
  <p v-if="error" class="provider-error" role="alert">{{error}}</p><p v-if="notice" class="provider-notice" role="status">{{notice}}</p>
  <div class="provider-list"><button v-for="provider in catalog.providers" :key="provider.id" type="button" :class="{active:provider.id===providerId}" :disabled="busy" @click="selectProvider(provider)"><BrainCircuit :size="18"/><span><strong>{{provider.name}}</strong><small>{{catalog.models.filter((m:any)=>m.provider_id===provider.id).length}} 个模型 · {{provider.protocol==='ollama'?'本机 Ollama':'OpenAI 兼容接口'}}{{provider.enabled?'':' · 已停用'}}</small></span><small>{{provider.api_key_configured?'Key 已配置':provider.protocol==='ollama'?'本机服务':'未设置 Key'}}</small></button></div>
  <form v-if="draft.name!==undefined" class="provider-card" @submit.prevent="saveProvider">
   <fieldset :disabled="busy"><div class="provider-grid">
    <label>供应商名称<input v-model.trim="draft.name" maxlength="80" required/></label><label>服务类型<select v-model="draft.protocol"><option value="company">OpenAI 兼容接口</option><option value="ollama">本机 Ollama</option></select></label>
    <label class="provider-wide">Base URL<input v-model.trim="draft.base_url" :placeholder="draft.protocol==='ollama'?'http://127.0.0.1:11434':'https://api.example.com/v1'" required/></label>
    <template v-if="draft.protocol==='company'"><label>API Key<input v-model="key" type="password" autocomplete="new-password" :placeholder="draft.api_key_configured?'已配置，留空保留':'请输入 API Key'" maxlength="4000"/></label><label class="provider-check"><input v-model="clearKey" type="checkbox"/>明确清空已保存 Key</label><label>可信 HTTP Origin<input v-model.trim="draft.trusted_http_origin" placeholder="仅显式受信内网 HTTP 需要填写"/></label><label>代理地址<input v-model.trim="draft.proxy_url" placeholder="可选"/></label></template>
    <label>连接超时（秒）<input v-model.number="draft.connect_timeout" type="number" min="1" max="20"/></label><label>读取超时（秒）<input v-model.number="draft.read_timeout" type="number" min="1" max="120"/></label>
   </div><label class="provider-check"><input v-model="draft.enabled" type="checkbox"/>启用此供应商</label>
   <p class="muted small">更换地址不能默带旧 Key，需重新填写或明确清空。检测不保存配置、不调用生成接口、不发送业务原文。</p>
   <div class="provider-actions"><button type="button" :disabled="detecting||!draft.base_url" @click="detect">{{detecting?'正在检测…':'检测模型'}}</button><button class="primary" :disabled="!draft.name||!draft.base_url">保存供应商</button><button v-if="providerId" type="button" class="danger ghost" @click="removeProvider"><Trash2 :size="14"/>移除供应商</button></div>
   </fieldset>
  </form>
  <section v-if="directory" class="provider-card directory">
   <h3>本次发现 {{directory.count}} 个模型{{directory.complete?'':'（目录未完整获取）'}}</h3><p class="muted small">这是供应商返回的目录，不代表 Key 对每个模型都有调用权限，也不证明工具调用或思考能力。请只添加适用于对话和工具调用的模型，Embedding、图片生成等模型不适用于此聊天入口。</p>
   <label class="provider-search"><Search :size="15"/><input v-model="search" placeholder="搜索模型 ID"/></label>
   <div class="directory-list"><label v-for="item in visibleDirectory" :key="item.model"><input v-model="checked" type="checkbox" :value="item.model" :disabled="busy||configured.has(item.model)"/><span>{{item.model}}</span><small v-if="configured.has(item.model)">已添加</small></label></div>
   <button type="button" class="primary" :disabled="busy||!checked.length||checked.length>100" @click="addModels([...checked])">保存供应商并添加所选（{{checked.length}}）</button><small class="muted">每批最多 100 个。</small>
  </section>
  <section class="provider-card">
   <h3>已添加模型</h3><form class="manual-add" @submit.prevent="addModels([manual.trim()])"><input v-model="manual" placeholder="手动输入准确的 API 模型 ID" maxlength="160" :disabled="busy"/><button :disabled="busy||!manual.trim()||!draft.base_url">手动添加</button></form>
   <p class="muted small">输出/上下文预算需要按实际服务核对，目录检测不会自动证明这些上限。</p>
   <div v-for="model in models" :key="model.id" class="configured-model"><span><strong>{{model.name}}</strong><small>{{model.model}} · {{model.enabled?'已启用':'已停用'}}{{model.id===catalog.default_model_id?' · 系统默认':''}}</small></span><div><button type="button" :disabled="busy" @click="editModel(model)">参数</button><button v-if="model.id!==catalog.default_model_id" type="button" :disabled="busy||!model.enabled||!draft.enabled" @click="setDefault(model)">设为默认</button><button type="button" :disabled="busy" @click="removeModel(model)">移除</button></div></div>
   <p v-if="!models.length" class="muted">尚未添加模型。可检测后勾选，也可手动添加。</p>
  </section>
  <form v-if="editor" class="provider-card" @submit.prevent="saveModel"><h3>模型参数</h3><fieldset :disabled="busy"><div class="provider-grid">
   <label>显示名称<input v-model.trim="editor.name" maxlength="160" required/></label><label>API 模型 ID<input v-model.trim="editor.model" maxlength="160" required/></label>
   <label>最大输出 token<input v-model.number="editor.max_output_tokens" type="number" min="256" max="8192" required/></label><label>上下文窗口 token<input v-model.number="editor.context_window" type="number" min="4096" max="2000000" required/></label>
   <label>最大 ReAct 轮次<input v-model.number="editor.max_turns" type="number" min="1" max="30" required/></label><label>思考参数协议<select v-model="editor.reasoning_policy" @change="editor.default_reasoning_effort=''"><option v-for="policy in policies" :key="policy.id" :value="policy.id">{{policy.name}}</option></select></label>
   <label>默认思考档位<select v-model="editor.default_reasoning_effort"><option value="">{{draft.protocol==='ollama'?'默认（关闭）':'服务默认'}}</option><option v-for="level in effortLevels" :key="level.value" :value="level.value">{{level.label}}</option></select></label><label class="provider-check"><input v-model="editor.enabled" type="checkbox"/>启用模型</label>
  </div><p class="muted small">未知模型默认不发送思考参数。手工声明协议前请核对服务商文档；不支持的档位会明确报错，不会偷偷降档。高档位可能需要更多输出预算。</p><button class="primary">保存模型参数</button><button type="button" @click="editor=null">取消</button></fieldset></form>
 </section>
</template>

<style scoped>
.provider-heading{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.provider-heading>div{flex:1;min-width:200px}.provider-heading h2{margin:0}.provider-heading p{margin:5px 0 12px}.provider-heading button,.provider-actions button{display:inline-flex;gap:5px;align-items:center}
.provider-list{display:flex;flex-direction:column;gap:7px;margin:12px 0}.provider-list>button{display:flex;align-items:center;gap:11px;padding:12px 15px;border-radius:14px;text-align:left;background:var(--surface,#fff);color:var(--text)}.provider-list>button>span{flex:1}.provider-list>button.active{border-color:var(--text);box-shadow:inset 3px 0 var(--text)}.provider-list small,.configured-model small{display:block;color:var(--muted);font-size:11px;margin-top:4px}
.provider-card{border:1px solid var(--border);border-radius:14px;padding:15px;margin:14px 0;background:var(--surface,#fff)}.provider-card h3{font-size:14px;margin:0 0 12px}.provider-card fieldset{border:0;padding:0;margin:0;min-width:0}.provider-grid{display:grid;grid-template-columns:1fr 1fr;gap:13px}.provider-grid label{display:flex;flex-direction:column;gap:6px;font-size:12px}.provider-wide{grid-column:1/-1}.provider-check{display:flex!important;flex-direction:row!important;align-items:center;gap:7px;margin-top:10px;font-size:12px}.provider-check input{width:auto}.provider-actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:12px}.provider-error{padding:10px;border-radius:9px;background:#c4424210;color:#be3636}.provider-notice{font-size:12px;color:var(--muted)}
.provider-search{
  display:flex!important;flex-direction:row!important;align-items:center!important;gap:8px!important;
  margin:14px 0 12px!important;padding:0 12px!important;height:38px!important;
  border:1px solid var(--border)!important;border-radius:10px!important;
  background:color-mix(in srgb,var(--surface) 60%,transparent)!important;
  transition:border-color .2s,box-shadow .2s!important;box-sizing:border-box!important
}
.provider-search:focus-within{
  border-color:var(--accent,#3478e8)!important;
  box-shadow:0 0 0 3px color-mix(in srgb,var(--accent,#3478e8) 18%,transparent)!important
}
.provider-search svg{opacity:.6!important;flex-shrink:0!important;color:inherit!important;margin:0!important}
.provider-search input{
  flex:1!important;min-width:0!important;height:100%!important;padding:0!important;margin:0!important;
  border:0!important;border-style:none!important;outline:0!important;background:transparent!important;
  box-shadow:none!important;color:inherit!important;font-size:13px!important;
  appearance:none!important;-webkit-appearance:none!important
}
.directory-list{max-height:340px;overflow-y:auto;margin:12px 0;padding:2px 4px 2px 0;scrollbar-width:thin}
.directory-list label{
  display:flex!important;flex-direction:row!important;align-items:center!important;gap:12px!important;
  padding:10px 14px!important;margin:4px 0!important;border-radius:10px!important;
  border:1px solid color-mix(in srgb,var(--border) 60%,transparent)!important;
  background:color-mix(in srgb,var(--surface) 80%,transparent)!important;
  transition:background .15s,border-color .15s,box-shadow .15s!important;cursor:pointer!important
}
.directory-list label:hover{
  background:color-mix(in srgb,var(--accent) 8%,var(--surface))!important;
  border-color:color-mix(in srgb,var(--accent) 30%,var(--border))!important
}
.directory-list label:has(input:checked){
  background:color-mix(in srgb,var(--accent) 12%,var(--surface))!important;
  border-color:color-mix(in srgb,var(--accent) 45%,var(--border))!important
}
.directory-list input[type="checkbox"]{
  width:16px!important;height:16px!important;margin:0!important;cursor:pointer!important;flex-shrink:0!important
}
.directory-list span{
  flex:1!important;font-size:13px!important;font-weight:500!important;
  font-family:ui-monospace,SFMono-Regular,Consolas,monospace,sans-serif!important;
  color:var(--text)!important;overflow-wrap:anywhere!important;text-align:left!important
}
.directory-list small{
  color:var(--accent)!important;background:color-mix(in srgb,var(--accent) 15%,transparent)!important;
  padding:2px 8px!important;border-radius:6px!important;font-size:11px!important;font-weight:600!important;
  margin:0!important;white-space:nowrap!important
}
.directory>small{margin-left:10px}
.manual-add{display:flex;gap:8px}.manual-add input{flex:1;min-width:0}
.configured-model{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:12px 0;border-top:1px solid var(--border)}.configured-model>span{min-width:0;overflow-wrap:anywhere}.configured-model>div{display:flex;gap:6px;flex-shrink:0}.configured-model strong{font-size:13px}.configured-model button{font-size:11px;padding:5px 8px}.provider-card fieldset>button{margin-right:8px}
@media(max-width:650px){.provider-grid{grid-template-columns:1fr}.provider-wide{grid-column:auto}.configured-model{align-items:flex-start;flex-direction:column}}
</style>
