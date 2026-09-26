<script setup lang="ts">
import {computed,onBeforeUnmount,ref,watch} from 'vue'
import {Bot,Check,ChevronDown,RotateCcw,Search,Zap} from 'lucide-vue-next'
import {api} from '../api'

type Choice={model_profile_id:string;reasoning_effort:string}
const props=defineProps<{userId:string;conversationId:string;storagePrefix:string;open:boolean;locked?:boolean}>()
const emit=defineEmits<{ 'update:open':[value:boolean]; change:[value:Choice|null]; ready:[value:boolean]; selected:[model:any] }>()
const catalog=ref<any>({providers:[],models:[],can_select:false}),choice=ref<Choice|null>(null),search=ref(''),loading=ref(false),error=ref('')
let epoch=0,stopped=false
let efforts:Record<string,string>={}
const model=computed(()=>catalog.value.models.find((item:any)=>item.id===choice.value?.model_profile_id))
const provider=computed(()=>catalog.value.providers.find((item:any)=>item.id===model.value?.provider_id))
const levels=computed(()=>[{value:'',label:model.value?.reasoning?.default_label||'服务默认'},...(model.value?.reasoning?.levels||[])])
const levelIndex=computed(()=>levels.value.findIndex((item:any)=>item.value===choice.value?.reasoning_effort))
const levelLabel=computed(()=>levels.value[levelIndex.value]?.label||'档位不可用')
const groups=computed(()=>catalog.value.providers.map((p:any)=>({...p,models:catalog.value.models.filter((m:any)=>m.provider_id===p.id&&(`${p.name} ${m.name} ${m.model}`).toLowerCase().includes(search.value.toLowerCase()))})).filter((p:any)=>p.models.length))
function storageKey(cid=props.conversationId){return `${props.storagePrefix}.model-choice.${props.userId}.${cid||'draft'}`}
function save(cid=props.conversationId){try{localStorage.setItem(storageKey(cid),JSON.stringify({profile_id:choice.value?.model_profile_id,efforts}))}catch{/* 浏览器禁用持久化时仍可使用本次选择。 */}}
function publish(){
 const valid=Boolean(model.value&&levelIndex.value>=0)
 emit('change',valid?choice.value:null);emit('ready',valid&&!loading.value)
 if(valid)emit('selected',model.value)
}
function restoreChoice(){
 let saved:any=null
 try{saved=JSON.parse(localStorage.getItem(storageKey())||'null')}catch{}
 efforts=saved?.efforts&&typeof saved.efforts==='object'?saved.efforts:{}
 const id=catalog.value.can_select&&saved?.profile_id?saved.profile_id:catalog.value.default_model_id
 const item=catalog.value.models.find((m:any)=>m.id===id)
 const effort=catalog.value.can_select&&typeof efforts[id]==='string'?efforts[id]:(item?.default_reasoning_effort||'')
 choice.value=id?{model_profile_id:id,reasoning_effort:effort}:null
 error.value=!item?'没有可用模型，或原先选择的模型已停用，请重新选择。':!levels.value.some((l:any)=>l.value===effort)?'原思考档位已不受支持，请重新选择或恢复默认。':''
 publish()
}
async function reload(){
 const current=++epoch;loading.value=true;emit('ready',false)
 try{const result=await api('/chat-models');if(stopped||current!==epoch)return;catalog.value=result;restoreChoice()}
 catch(e:any){if(current===epoch&&!stopped){error.value=e.message;choice.value=null;emit('change',null)}}
 finally{if(current===epoch&&!stopped){loading.value=false;publish()}}
}
function choose(item:any){
 if(!catalog.value.can_select||props.locked)return
 choice.value={model_profile_id:item.id,reasoning_effort:typeof efforts[item.id]==='string'?efforts[item.id]:(item.default_reasoning_effort||'')}
 error.value=levelIndex.value<0?'原思考档位已不受支持，请恢复默认。':'';save();publish()
}
function setEffort(value:string){if(!choice.value||!catalog.value.can_select||props.locked)return;choice.value={...choice.value,reasoning_effort:value};efforts[choice.value.model_profile_id]=value;error.value='';save();publish()}
function slider(event:Event){setEffort(levels.value[Number((event.target as HTMLInputElement).value)]?.value||'')}
function bindConversation(cid:string,selected:Choice|null){if(selected){try{const stored=JSON.parse(localStorage.getItem(storageKey(cid))||'null');localStorage.setItem(storageKey(cid),JSON.stringify({profile_id:selected.model_profile_id,efforts:{...(stored?.efforts||{}),[selected.model_profile_id]:selected.reasoning_effort}}))}catch{}}}
watch(()=>[props.userId,props.conversationId],()=>{emit('update:open',false);search.value='';reload()},{immediate:true})
watch(()=>props.open,value=>{if(value)reload()})
onBeforeUnmount(()=>{stopped=true;epoch++;emit('ready',false)})
defineExpose({reload,bindConversation})
</script>

<template>
 <div class="model-selector-wrap chat-model-picker" :inert="locked">
  <button type="button" class="picker-trigger" :aria-expanded="open" aria-haspopup="dialog" :title="`${provider?.name||''} / ${model?.model||'选择模型'} · ${levelLabel}`" @click="emit('update:open',!open)">
   <Bot :size="16"/><span>{{provider?.name?provider.name+' / ':''}}{{model?.name||'选择模型'}}</span><small v-if="model?.reasoning?.supported">{{levelLabel}}</small><ChevronDown :size="13"/>
  </button>
  <div v-if="open" class="picker-panel" role="dialog" aria-label="模型与思考程度">
   <header><span class="picker-icon"><Zap :size="18"/></span><div><strong>{{levelLabel}}</strong><small>{{provider?.name||'供应商'}} / {{model?.model||'尚未选择'}}</small></div><button type="button" class="icon-button" :disabled="!model||!catalog.can_select" title="恢复该模型默认档位" aria-label="恢复默认思考档位" @click="setEffort(model.default_reasoning_effort||'')"><RotateCcw :size="16"/></button></header>
   <div v-if="model?.reasoning?.supported" class="effort-control">
    <input type="range" min="0" :max="levels.length-1" step="1" :value="Math.max(0,levelIndex)" :style="{'--effort-fill':Math.max(0,levelIndex)/(levels.length-1)*100+'%'}" :disabled="!catalog.can_select||loading" aria-label="思考程度" :aria-valuetext="levelLabel" @input="slider"/>
    <div class="effort-labels"><button v-for="level in levels" :key="level.value" type="button" :class="{active:choice?.reasoning_effort===level.value}" :disabled="!catalog.can_select||loading" @click="setEffort(level.value)">{{level.label}}</button></div>
    <p>更高档位通常更慢、消耗更多；仅影响下一次发送的任务。</p>
   </div>
   <p v-else class="picker-hint">此模型尚未开放思考档位控制，使用默认配置。</p>
   <p v-if="model?.reasoning?.source==='administrator_declared'" class="picker-hint">思考能力由管理员声明，供应商实际支持情况需验证。</p>
   <label class="picker-search"><Search :size="15"/><input v-model="search" placeholder="搜索供应商或模型" aria-label="搜索模型"/></label>
   <p v-if="loading" role="status">正在读取模型…</p>
   <p v-if="error" class="picker-error" role="alert">{{error}} <button type="button" @click="reload">刷新</button></p>
   <div class="picker-groups">
    <section v-for="group in groups" :key="group.id"><h4>{{group.name}}</h4><button v-for="item in group.models" :key="item.id" type="button" class="picker-row" :title="item.model" :class="{active:item.id===choice?.model_profile_id}" :aria-pressed="item.id===choice?.model_profile_id" :disabled="!catalog.can_select||loading" @click="choose(item)"><span><strong>{{item.name}}</strong><small v-if="item.name!==item.model">{{item.model}}</small></span><Check v-if="item.id===choice?.model_profile_id" :size="15"/></button></section>
   </div>
   <p class="picker-hint">{{catalog.can_select?'当前会话独立选择，不修改系统默认或文档识别配置。':'当前账号使用管理员配置的默认模型。'}}</p>
  </div>
 </div>
</template>

<style scoped>
/* ─── Container ─── */
.chat-model-picker{position:relative;display:block!important;max-width:min(45vw,360px)!important;flex:0 1 auto!important}

/* ─── Trigger button ─── */
.picker-trigger{
  display:flex;align-items:center;gap:7px;max-width:100%;height:32px;
  border:1px solid transparent;background:color-mix(in srgb,var(--accent,#172334) 8%,transparent);
  color:var(--text);padding:4px 10px;border-radius:10px;font-size:12.5px;
  transition:background .2s,border-color .2s,box-shadow .2s;cursor:pointer
}
.picker-trigger:hover{
  background:color-mix(in srgb,var(--accent,#172334) 14%,transparent);
  border-color:color-mix(in srgb,var(--accent,#172334) 22%,var(--border,#353a42));
  box-shadow:0 2px 8px color-mix(in srgb,var(--accent,#172334) 10%,transparent)
}
.picker-trigger>span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:500}
.picker-trigger small{color:#3478e8;white-space:nowrap;font-weight:600;font-size:11.5px}
.picker-trigger svg{flex-shrink:0;opacity:.7;transition:transform .2s}
.picker-trigger[aria-expanded="true"] svg:last-child{transform:rotate(180deg)}

/* ─── Panel (popover) ─── */
.picker-panel{
  position:absolute;right:0;bottom:42px;z-index:65;
  width:min(360px,calc(100vw - 24px));
  border:1px solid color-mix(in srgb,var(--border,#353a42) 70%,transparent);
  border-radius:20px;padding:16px;
  background:color-mix(in srgb,var(--surface,#22262b) 92%,transparent);
  backdrop-filter:blur(24px) saturate(1.4);-webkit-backdrop-filter:blur(24px) saturate(1.4);
  color:var(--text,#f4f6f8);
  box-shadow:0 20px 60px -8px #0003,0 0 0 1px color-mix(in srgb,var(--border,#353a42) 40%,transparent);
  animation:picker-enter .22s cubic-bezier(.22,1,.36,1)
}
@keyframes picker-enter{from{opacity:0;transform:translateY(8px) scale(.97)}to{opacity:1;transform:none}}

/* ─── Panel header ─── */
.picker-panel header{
  display:flex;align-items:center;gap:12px;padding-bottom:14px;
  border-bottom:1px solid color-mix(in srgb,var(--border,#353a42) 60%,transparent)
}
.picker-panel header>div{flex:1;min-width:0;text-align:center}
.picker-panel header strong{
  color:#3478e8;font-size:16px;font-weight:700;letter-spacing:-.01em
}
.picker-panel header small{
  display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  font-size:11px;color:var(--muted,#a7b1bf);margin-top:4px;letter-spacing:.02em
}
.picker-icon{
  display:grid;place-items:center;width:38px;height:38px;border-radius:12px;
  background:linear-gradient(135deg,#3478e822,#3478e80a);
  color:#3478e8;transition:background .2s
}
.picker-icon:hover{background:linear-gradient(135deg,#3478e833,#3478e818)}

/* ─── Effort slider ─── */
.effort-control{padding:16px 0 6px}
.effort-control input{
  width:100%;height:6px;margin:12px 0 14px;padding:0;appearance:none;border:0;border-radius:6px;
  background:linear-gradient(to right,#3478e8 var(--effort-fill),color-mix(in srgb,var(--border,#353a42) 50%,transparent) var(--effort-fill));
  cursor:pointer;transition:background .15s
}
.effort-control input::-webkit-slider-thumb{
  appearance:none;width:22px;height:22px;border-radius:50%;
  background:linear-gradient(145deg,#fff,#f0f2f5);border:2px solid #3478e8;
  box-shadow:0 2px 10px #3478e830,0 0 0 4px #3478e812;
  transition:box-shadow .2s,transform .15s;cursor:grab
}
.effort-control input::-webkit-slider-thumb:hover{
  box-shadow:0 3px 14px #3478e840,0 0 0 6px #3478e818;transform:scale(1.08)
}
.effort-control input::-webkit-slider-thumb:active{cursor:grabbing;transform:scale(.95)}
.effort-control input::-moz-range-thumb{
  width:22px;height:22px;border-radius:50%;
  background:linear-gradient(145deg,#fff,#f0f2f5);border:2px solid #3478e8;
  box-shadow:0 2px 10px #3478e830,0 0 0 4px #3478e812;cursor:grab
}
.effort-labels{display:flex;justify-content:space-between;gap:2px}
.effort-labels button{
  border:0;background:transparent;font-size:11px;padding:5px 6px;
  color:var(--muted,#a7b1bf);border-radius:6px;transition:color .15s,background .15s
}
.effort-labels button:hover{background:color-mix(in srgb,var(--border,#353a42) 40%,transparent)}
.effort-labels button.active{color:#3478e8;font-weight:700;background:#3478e80e}

/* ─── Hints ─── */
.picker-hint,.effort-control p{
  font-size:11px;color:var(--muted,#a7b1bf);margin:10px 0 0;line-height:1.55
}

/* ─── Search ─── */
.picker-search{
  display:flex!important;flex-direction:row!important;align-items:center!important;gap:8px!important;
  margin:14px 0 10px!important;padding:0 12px!important;height:38px!important;
  border:1px solid color-mix(in srgb,var(--border,#353a42) 70%,transparent)!important;
  border-radius:10px!important;
  background:color-mix(in srgb,var(--surface,#22262b) 50%,transparent)!important;
  transition:border-color .2s,box-shadow .2s!important;
  box-sizing:border-box!important
}
.picker-search:focus-within{
  border-color:#3478e8!important;
  box-shadow:0 0 0 3px #3478e820!important;
  background:color-mix(in srgb,var(--surface,#22262b) 80%,transparent)!important
}
.picker-search svg{opacity:.6!important;flex-shrink:0!important;color:inherit!important;margin:0!important}
.picker-search input{
  flex:1!important;min-width:0!important;height:100%!important;padding:0!important;margin:0!important;
  border:0!important;border-style:none!important;outline:0!important;background:transparent!important;
  box-shadow:none!important;color:inherit!important;font-size:12.5px!important;line-height:normal!important;
  appearance:none!important;-webkit-appearance:none!important
}
.picker-search input::placeholder{color:var(--muted,#a7b1bf)!important;opacity:.8!important}

/* ─── Model groups & rows ─── */
.picker-groups{max-height:260px;overflow-y:auto;margin:4px -4px 0;padding:0 4px;scrollbar-width:thin}
.picker-groups::-webkit-scrollbar{width:4px}
.picker-groups::-webkit-scrollbar-thumb{background:color-mix(in srgb,var(--muted,#a7b1bf) 30%,transparent);border-radius:4px}
.picker-groups h4{
  margin:14px 6px 6px;color:var(--muted,#a7b1bf);font-size:10.5px;font-weight:600;
  letter-spacing:.06em;text-transform:uppercase
}
.picker-row{
  width:100%;border:0;display:flex;align-items:center;justify-content:space-between;
  border-radius:12px;padding:10px 12px;text-align:left;background:transparent;
  color:inherit;transition:background .15s,box-shadow .15s;cursor:pointer;gap:8px
}
.picker-row:hover{
  background:color-mix(in srgb,#3478e8 8%,transparent)
}
.picker-row.active{
  background:linear-gradient(135deg,#3478e812,#3478e808);
  box-shadow:inset 0 0 0 1px #3478e825
}
.picker-row>span{min-width:0;flex:1}
.picker-row strong{
  display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  font-size:13px;font-weight:500
}
.picker-row small{
  display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  color:var(--muted,#a7b1bf);font-size:11px;margin-top:2px
}
.picker-row svg{
  color:#3478e8;flex-shrink:0;opacity:0;transition:opacity .2s,transform .2s;transform:scale(.85)
}
.picker-row.active svg{opacity:1;transform:scale(1)}

/* ─── Error ─── */
.picker-error{
  color:#ff6b6b;font-size:12px;background:#ff6b6b0a;border:1px solid #ff6b6b22;
  border-radius:10px;padding:10px 12px;margin:8px 0
}
.picker-error button{font-size:11px;margin-left:6px;color:#3478e8;border:0;background:transparent;text-decoration:underline;cursor:pointer}

/* ─── Global overrides ─── */
:global(.workspace-open) .picker-trigger>span{display:none}

/* ─── Dark theme ─── */
:global(html[data-theme="dark"]) .picker-panel{
  background:color-mix(in srgb,#232933 88%,transparent);
  border-color:#3a424e80;
  box-shadow:0 24px 64px -10px #000a,0 0 0 1px #ffffff08
}
:global(html[data-theme="dark"]) .effort-control input::-webkit-slider-thumb{
  background:linear-gradient(145deg,#3a4250,#2d3440);border-color:#3478e8
}
:global(html[data-theme="dark"]) .effort-control input::-moz-range-thumb{
  background:linear-gradient(145deg,#3a4250,#2d3440);border-color:#3478e8
}

/* ─── Mobile ─── */
@media(max-width:650px){
  .chat-model-picker{max-width:130px!important}
  .picker-trigger>span{max-width:64px}
  .picker-trigger small{display:none}
  .picker-panel{border-radius:16px;padding:12px}
}
</style>
