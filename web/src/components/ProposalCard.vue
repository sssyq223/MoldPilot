<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {ShieldCheck,X} from 'lucide-vue-next'
import {api,post,shanghai} from '../api'
type ProposalDetailLink={target:string;receipt_field:string;label:string}
type ProposalPresentation={action_prefixes?:string[];action_suffixes?:string[];value_names?:Record<string,string>;detail_links?:Record<string,ProposalDetailLink>}
const props=withDefaults(defineProps<{stepId:string;proposal:any;placement?:'message'|'composer';productName?:string;decision?:string;presentation?:ProposalPresentation}>(),{placement:'message',productName:'Agent',decision:'',presentation:()=>({})})
const emit=defineEmits<{open:[link:{target:string;id:string}];status:[confirmed:boolean];confirmed:[];dismissed:[]}>()
const intent=ref<any>(null),receipt=ref<any>(null),showDetails=ref(false),busy=ref(false),error=ref('')
const policy=computed(()=>intent.value?.confirmation_policy||props.proposal.confirmation_policy)
const actionTitle=computed(()=>{
  let title=String(props.proposal.display?.操作||props.proposal.action||'业务操作')
  for(const prefix of props.presentation.action_prefixes||[]){if(title.startsWith(prefix)){title=title.slice(prefix.length);break}}
  for(const suffix of props.presentation.action_suffixes||[]){if(title.endsWith(suffix)){title=title.slice(0,-suffix.length);break}}
  return title.trim()||'业务操作'
})
const resolved=computed(()=>Boolean(receipt.value||props.decision))
const dismissed=computed(()=>props.decision==='dismissed')
const summaryTitle=computed(()=>resolved.value
  ? `${actionTitle.value}${dismissed.value?'已取消':(approval.value?'已提交审批':'已确认')}`
  : `${actionTitle.value}待本人确认`)
const detailDisplay=computed(()=>intent.value?.display||props.proposal.display||{})
const proposalValueNames=computed(()=>props.presentation.value_names||{})
const detailLink=computed(()=>props.presentation.detail_links?.[String(props.proposal.kind||'')])
const detailLinkId=computed(()=>detailLink.value?String(receipt.value?.[detailLink.value.receipt_field]||''):'')
function displayScalar(key:string,value:any){
  if(typeof value==='boolean')return value?'是':'否'
  if(key==='实际发生时间')return shanghai(value)
  const text=String(value)
  if(proposalValueNames.value[text])return proposalValueNames.value[text]
  return text.split(/(\s*[·、，]\s*)/).map(part=>{
    const trimmed=part.trim()
    return proposalValueNames.value[trimmed]?part.replace(trimmed,proposalValueNames.value[trimmed]):part
  }).join('')
}
function displayValue(key:string,value:any){
  if(Array.isArray(value))return value.map(item=>typeof item==='object'&&item!==null?Object.entries(item).map(([k,v])=>`${k}：${displayScalar(String(k),v)}`).join('，'):displayScalar(key,item)).join('\n')
  if(typeof value==='object'&&value!==null)return Object.entries(value).map(([k,v])=>`${k}：${displayScalar(String(k),v)}`).join('\n')
  return displayScalar(key,value)
}
const base='/proposals/'
const approval=computed(()=>Boolean(policy.value?.requires_approval??props.proposal.requires_approval))
onMounted(async()=>{try{receipt.value=(await api(base+props.stepId)).receipt;emit('status',Boolean(receipt.value))}catch(e:any){error.value=e.message;emit('status',false)}})
async function review(){
  if(resolved.value){showDetails.value=true;return}
  busy.value=true;error.value='';try{intent.value=await post(base+props.stepId+'/intent')}catch(e:any){error.value=e.message}finally{busy.value=false}
}
async function confirm(){busy.value=true;error.value='';try{receipt.value=await post('/human-actions/'+intent.value.id+'/confirm',{challenge:intent.value.challenge});intent.value=null;emit('status',true);emit('confirmed')}catch(e:any){error.value=e.message;if(e.status===403||e.status===409)intent.value=null}finally{busy.value=false}}
async function dismiss(){busy.value=true;error.value='';try{await post(base+props.stepId+'/dismiss');emit('status',true);emit('dismissed')}catch(e:any){error.value=e.message}finally{busy.value=false}}
</script>
<template>
  <section v-if="placement==='composer'&&!receipt" class="proposal-card composer-approval" aria-label="等待批准">
    <div class="composer-approval-label"><ShieldCheck :size="15"/><span>权限</span></div>
    <div class="composer-approval-copy">
      <strong>允许 {{productName}} 执行“{{actionTitle}}”吗？</strong>
      <small>操作前会展示完整字段供你核对，批准后才会生成正式回执。</small>
    </div>
    <div class="composer-approval-actions">
      <button type="button" :disabled="busy" @click="dismiss">暂不执行</button>
      <button type="button" class="primary" :disabled="busy" @click="review">{{busy?'正在处理…':'查看并批准'}}</button>
    </div>
    <p v-if="error" class="proposal-error" role="alert">{{error}}</p>
  </section>
  <section v-else-if="placement==='message'" class="proposal-card" aria-label="业务操作建议">
    <div class="proposal-brief">
      <span>
        <strong>{{summaryTitle}}</strong>
        <small>{{resolved?(dismissed?'本人已选择暂不执行，可随时查看原确认字段。':(approval?'已提交后续审批，请以最终生效回执为准。':'操作已按回执留痕，可随时查看确认字段。')):'点击详情核对字段后再确认，不会自动执行。'}}</small>
      </span>
      <button v-if="detailLink&&detailLinkId" @click="emit('open',{target:detailLink.target,id:detailLinkId})">{{detailLink.label}}</button>
      <button v-else :disabled="busy" @click="review">{{busy?'正在准备…':'查看详情'}}</button>
    </div>
    <p v-if="error" class="proposal-error" role="alert">{{error}}</p>
  </section>
  <Teleport to="body"><div v-if="intent||showDetails" class="modal-shade" @click.self="intent=null;showDetails=false"><section class="modal proposal-modal" role="dialog" aria-modal="true" aria-label="确认业务操作">
    <div class="proposal-modal-head">
      <h2>{{resolved?'本次业务操作详情':'请核对本次业务操作'}}</h2>
      <button type="button" class="icon-button" aria-label="关闭业务操作详情" @click="intent=null;showDetails=false"><X :size="17"/></button>
    </div>
    <dl><template v-for="(value,key) in detailDisplay" :key="String(key)"><dt>{{key}}</dt><dd>{{displayValue(String(key),value)}}</dd></template></dl>
    <div v-if="policy" class="confirmation-policy modal-policy" :class="{delegated:policy.agent_permission_mode==='delegated_auto'}">
      <strong>{{policy.title}}</strong>
      <small>{{policy.description}}</small>
    </div>
    <p class="muted">{{resolved?(dismissed?'本人已选择暂不执行，该确认卡已关闭。':(approval?'操作已经提交后续审批，最终状态以审批生效回执为准。':'操作已由本人确认并生成审计回执，不能重复执行。')):(approval?'点击确认后创建申请并提交 Agent BPM；只有最终审批生效才会改变业务状态。':'点击确认后才会执行卡片中列明的操作并生成审计回执。')}}</p>
    <div v-if="resolved" class="actions"><button disabled>{{dismissed?'已取消':'暂不执行'}}</button><button class="primary" disabled>{{dismissed?'确认执行':'已确认执行'}}</button></div>
    <div v-else class="actions"><button :disabled="busy" @click="intent=null">暂不执行</button><button class="primary" :disabled="busy" @click="confirm">确认执行</button></div>
  </section></div></Teleport>
</template>
<style scoped>.proposal-card{display:flex;flex-direction:column;gap:6px}.composer-approval{position:relative;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px 18px;width:min(820px,calc(100% - 48px));margin:8px auto 0;padding:14px 16px;border:1px solid color-mix(in srgb,var(--border) 82%,transparent);border-radius:18px;background:var(--surface);box-shadow:0 18px 50px color-mix(in srgb,var(--shadow) 16%,transparent)}.composer-approval-label{grid-column:1/-1;display:flex;align-items:center;gap:7px;color:var(--muted);font-size:12px}.composer-approval-copy{min-width:0;display:grid;gap:4px}.composer-approval-copy strong{font-size:14px;line-height:1.5;color:var(--text)}.composer-approval-copy small{font-size:12px;line-height:1.5;color:var(--muted)}.composer-approval-actions{display:flex;align-items:end;justify-content:flex-end;gap:8px}.composer-approval-actions button{height:34px;padding:0 13px;border-radius:9px;font-size:12px;white-space:nowrap}.composer-approval>.proposal-error{grid-column:1/-1}.proposal-brief{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:38px;padding:8px 10px;border:1px solid color-mix(in srgb,var(--border) 76%,transparent);border-radius:10px;background:color-mix(in srgb,var(--surface) 38%,transparent)}.proposal-brief>span{min-width:0;display:grid;gap:2px}.proposal-brief strong{font-size:14px;font-weight:500;color:var(--text);line-height:1.45}.proposal-brief small{font-size:12px;color:var(--muted);line-height:1.45;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.proposal-brief button{flex-shrink:0;height:30px;padding:0 11px;border-radius:8px;font-size:12px}.proposal-error{margin:0;color:var(--error,#b4534b);font-size:12px}.confirmation-policy{border:1px solid color-mix(in srgb,var(--border) 85%,var(--accent));border-radius:8px;background:color-mix(in srgb,var(--surface) 86%,var(--accent) 14%);padding:10px 12px;display:grid;gap:4px}.confirmation-policy strong{font-size:13px}.confirmation-policy small{color:var(--muted);line-height:1.5}.confirmation-policy.delegated{border-color:color-mix(in srgb,var(--success,#6fcf97) 45%,var(--border));background:color-mix(in srgb,var(--surface) 82%,var(--success,#6fcf97) 18%)}.modal-policy{margin:12px 0}.proposal-modal{width:500px;max-height:calc(100vh - 32px);overflow:auto;scrollbar-width:none;-ms-overflow-style:none}.proposal-modal::-webkit-scrollbar{display:none}.proposal-modal-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:12px}.proposal-modal-head h2{margin:0}.proposal-modal-head .icon-button{flex-shrink:0;margin:-4px -4px 0 0}dl{margin:0;display:grid;grid-template-columns:90px minmax(0,1fr);gap:10px 16px}dt{color:var(--muted)}dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}@media(max-width:620px){.composer-approval{width:calc(100% - 20px);grid-template-columns:1fr}.composer-approval-actions{justify-content:flex-end}.proposal-brief{align-items:flex-start;flex-direction:column}.proposal-brief small{white-space:normal}.proposal-modal{width:calc(100vw - 24px)}dl{grid-template-columns:82px minmax(0,1fr)}}</style>
