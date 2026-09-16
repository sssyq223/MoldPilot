<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {api,post,shanghai} from '../api'
const props=defineProps<{stepId:string;proposal:any}>()
const emit=defineEmits<{open:[id:string]}>()
const intent=ref<any>(null),receipt=ref<any>(null),busy=ref(false),error=ref('')
const policy=computed(()=>intent.value?.confirmation_policy||props.proposal.confirmation_policy)
function displayValue(key:string,value:any){
  if(typeof value==='boolean')return value?'是':'否'
  if(key==='实际发生时间')return shanghai(value)
  if(Array.isArray(value))return value.map(item=>typeof item==='object'&&item!==null?Object.entries(item).map(([k,v])=>`${k}：${v}`).join('，'):String(item)).join('\n')
  if(typeof value==='object'&&value!==null)return Object.entries(value).map(([k,v])=>`${k}：${v}`).join('\n')
  return String(value)
}
const base=props.proposal.kind==='project_control'?'/project-control-proposals/':props.proposal.kind==='project_closure'?'/project-closure-proposals/':props.proposal.kind==='internal_start'?'/internal-start-proposals/':props.proposal.kind==='quote_acceptance'?'/quote-acceptance-proposals/':['sales_contract','full_outsource_contract'].includes(props.proposal.kind)?'/contract-proposals/':['customer_receipt','supplier_payment_confirmation'].includes(props.proposal.kind)?'/finance-proposals/':['project_plan_baseline','project_plan_change','plan_department_confirmation'].includes(props.proposal.kind)?'/project-plan-proposals/':'/contact-proposals/'
const approval=props.proposal.kind==='project_control'||props.proposal.kind==='project_plan_change'||props.proposal.requires_approval
onMounted(async()=>{try{receipt.value=(await api(base+props.stepId)).receipt}catch(e:any){error.value=e.message}})
async function review(){busy.value=true;error.value='';try{intent.value=await post(base+props.stepId+'/intent')}catch(e:any){error.value=e.message}finally{busy.value=false}}
async function confirm(){busy.value=true;error.value='';try{receipt.value=await post('/human-actions/'+intent.value.id+'/confirm',{challenge:intent.value.challenge});intent.value=null}catch(e:any){error.value=e.message;if(e.status===403||e.status===409)intent.value=null}finally{busy.value=false}}
</script>
<template>
  <section class="contact-proposal" aria-label="业务操作建议">
    <strong>{{receipt?(approval?'本人已确认，已提交审批':'本人已确认，操作已记录'):'待本人核对确认'}}</strong>
    <div v-if="policy" class="confirmation-policy" :class="{delegated:policy.agent_permission_mode==='delegated_auto'}">
      <strong>{{policy.title}}</strong>
      <small>{{policy.description}}</small>
    </div>
    <dl><template v-for="(value,key) in proposal.display" :key="String(key)"><dt>{{key}}</dt><dd>{{displayValue(String(key),value)}}</dd></template></dl>
    <p class="muted small">{{receipt?(approval?'提交动作本身不改变项目状态；请以 BPM 最终审批和生效回执为准。':proposal.kind==='project_closure'?'清单修订已留痕；该记录本身不修改 ERP，也不代表项目已关闭。':'以操作回执和联络单过程记录为准。'):'尚未执行业务操作。内容有误时，请在会话中说明修改要求，重新准备。'}}</p>
    <p v-if="error" role="alert">{{error}}</p>
    <button v-if="proposal.kind==='contact'&&receipt&&receipt.case_id" @click="emit('open',receipt.case_id)">查看联络单材料</button>
    <button v-else-if="!receipt" :disabled="busy" @click="review">核对并准备确认</button>
  </section>
  <Teleport to="body"><div v-if="intent" class="modal-shade"><section class="modal" role="dialog" aria-modal="true" aria-label="确认业务操作">
    <h2>请核对本次业务操作</h2><dl><template v-for="(value,key) in intent.display" :key="String(key)"><dt>{{key}}</dt><dd>{{displayValue(String(key),value)}}</dd></template></dl>
    <div v-if="policy" class="confirmation-policy modal-policy" :class="{delegated:policy.agent_permission_mode==='delegated_auto'}">
      <strong>{{policy.title}}</strong>
      <small>{{policy.description}}</small>
    </div>
    <p class="muted">{{approval?'点击确认后创建申请并提交 Agent BPM；只有最终审批生效才会改变项目状态。':proposal.kind==='project_closure'?'点击确认后保存结项清单或事项修订；原记录继续保留，不会修改 ERP 或直接关闭项目。':'点击确认后才会保存记录或派发事项。协作反馈不等于正式审批。'}}</p>
    <div class="actions"><button :disabled="busy" @click="intent=null">暂不执行</button><button class="primary" :disabled="busy" @click="confirm">确认执行</button></div>
  </section></div></Teleport>
</template>
<style scoped>.contact-proposal{display:flex;flex-direction:column;gap:12px}.contact-proposal>button{align-self:flex-start}.confirmation-policy{border:1px solid color-mix(in srgb,var(--border) 85%,var(--accent));border-radius:8px;background:color-mix(in srgb,var(--surface) 86%,var(--accent) 14%);padding:10px 12px;display:grid;gap:4px}.confirmation-policy strong{font-size:13px}.confirmation-policy small{color:var(--muted);line-height:1.5}.confirmation-policy.delegated{border-color:color-mix(in srgb,var(--success,#6fcf97) 45%,var(--border));background:color-mix(in srgb,var(--surface) 82%,var(--success,#6fcf97) 18%)}.modal-policy{margin:12px 0}dl{margin:0;display:grid;grid-template-columns:90px minmax(0,1fr);gap:10px 16px}dt{color:var(--muted)}dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}</style>
