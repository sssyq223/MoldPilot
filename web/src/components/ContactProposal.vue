<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {api,post,shanghai} from '../api'
const props=defineProps<{stepId:string;proposal:any}>()
const emit=defineEmits<{open:[id:string]}>()
const intent=ref<any>(null),receipt=ref<any>(null),busy=ref(false),error=ref('')
function displayValue(key:string,value:any){if(typeof value==='boolean')return value?'是':'否';return key==='实际发生时间'?shanghai(value):String(value)}
const base=props.proposal.kind==='project_control'?'/project-control-proposals/':'/contact-proposals/'
onMounted(async()=>{try{receipt.value=(await api(base+props.stepId)).receipt}catch(e:any){error.value=e.message}})
async function review(){busy.value=true;error.value='';try{intent.value=await post(base+props.stepId+'/intent')}catch(e:any){error.value=e.message}finally{busy.value=false}}
async function confirm(){busy.value=true;error.value='';try{receipt.value=await post('/human-actions/'+intent.value.id+'/confirm',{challenge:intent.value.challenge});intent.value=null}catch(e:any){error.value=e.message;if(e.status===403||e.status===409)intent.value=null}finally{busy.value=false}}
</script>
<template>
  <section class="contact-proposal" aria-label="业务操作建议">
    <strong>{{receipt?(proposal.kind==='project_control'?'本人已确认，已提交审批':'本人已确认，操作已记录'):'待本人核对确认'}}</strong>
    <dl><template v-for="(value,key) in proposal.display" :key="String(key)"><dt>{{key}}</dt><dd>{{displayValue(String(key),value)}}</dd></template></dl>
    <p class="muted small">{{receipt?(proposal.kind==='project_control'?'提交动作本身不改变项目状态；请以 BPM 最终审批和生效回执为准。':'以操作回执和联络单过程记录为准。'):'尚未执行业务操作。内容有误时，请在会话中说明修改要求，重新准备。'}}</p>
    <p v-if="error" role="alert">{{error}}</p>
    <button v-if="receipt&&receipt.case_id" @click="emit('open',receipt.case_id)">查看联络单材料</button>
    <button v-else :disabled="busy" @click="review">核对并准备确认</button>
  </section>
  <Teleport to="body"><div v-if="intent" class="modal-shade"><section class="modal" role="dialog" aria-modal="true" aria-label="确认业务操作">
    <h2>请核对本次业务操作</h2><dl><template v-for="(value,key) in intent.display" :key="String(key)"><dt>{{key}}</dt><dd>{{displayValue(String(key),value)}}</dd></template></dl>
    <p class="muted">{{proposal.kind==='project_control'?'点击确认后创建申请并提交 Agent BPM；只有最终审批生效才会改变项目状态。':'点击确认后才会保存记录或派发事项。协作反馈不等于正式审批。'}}</p>
    <div class="actions"><button :disabled="busy" @click="intent=null">暂不执行</button><button class="primary" :disabled="busy" @click="confirm">确认执行</button></div>
  </section></div></Teleport>
</template>
<style scoped>.contact-proposal{display:flex;flex-direction:column;gap:12px}.contact-proposal>button{align-self:flex-start}dl{margin:0;display:grid;grid-template-columns:90px minmax(0,1fr);gap:10px 16px}dt{color:var(--muted)}dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}</style>
