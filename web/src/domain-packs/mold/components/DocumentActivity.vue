<script setup lang="ts">
import {computed,onUnmounted,ref,watch} from 'vue'
import {api,post,shanghai} from '../../../api'
import FileMaterial from '../../../components/FileMaterial.vue'
import DocumentFieldsDialog from './DocumentFieldsDialog.vue'
import {isAdminStartDateField,normalizeAdminStartDate} from './adminStartDates'
import {validateAdminStartSelection} from './adminStartSelection'
const props=defineProps<{conversationId:string;archived?:boolean;refreshKey?:number}>()
const emit=defineEmits<{processed:[ids:string[]];'draft-action':[payload:{prompt:string}]}>()
const items=ref<any[]>([]),forms=ref<Record<string,any>>({}),intent=ref<any>(null),error=ref(''),busy=ref(false),clock=ref(Date.now())
const draftModal=ref<any|null>(null),draftMaterial=ref<Record<string,string>>({}),draftReason=ref(''),draftError=ref('')
const draftStep=ref<'material'|'dispatch'|'receipts'>('material'),dispatchSelection=ref<Record<string,boolean>>({})
const selectedProjectId=ref(''),sourceContext=computed(()=>draftModal.value?.source_context||null)
const departmentOptions=[
 {key:'DESIGN',label:'设计部'},{key:'PURCHASE',label:'采购部'},{key:'MANUFACTURING',label:'制造部'},
 {key:'ASSEMBLY',label:'装配部'},{key:'FINANCE',label:'财务部'},
]
const selectedFields=ref<{intakeId:string;groupId:string}|null>(null)
// 只保存身份，数据始终取自当前会话最新的权限过滤结果。
const fieldGroup=computed(()=>{
 const selected=selectedFields.value
 return selected?items.value.find(item=>item.id===selected.intakeId)?.groups?.find((group:any)=>group.id===selected.groupId):null
})
watch(fieldGroup,group=>{if(!group)selectedFields.value=null})
watch(intent,current=>{if(current)selectedFields.value=null})
const autoOpenedDrafts=new Set<string>()
function sourceValue(context:any,key:string){
 const rows=context?.fields?.[key]
 return Array.isArray(rows)&&rows.length?rows[0]?.value:''
}
function openDraft(draft:any, automatic=false){
 draftModal.value=draft;draftError.value='';draftReason.value='';selectedProjectId.value=draft.project_id||''
 draftStep.value=draft.status==='DEPARTMENTS_NOTIFIED'?'receipts':'material'
 dispatchSelection.value=Object.fromEntries(departmentOptions.map(option=>[option.key,true]))
 const source=draft?.material_snapshot&&typeof draft.material_snapshot==='object'?draft.material_snapshot:{}
 const context=draft?.source_context||{}
 draftMaterial.value=Object.fromEntries(draftFieldMeta.map(field=>{
  const value=source[field.key] ?? sourceValue(context,field.key)
  const text=Array.isArray(value)?value.join(', '):value==null?'':String(value)
  return [field.key,isAdminStartDateField(field.key)?normalizeAdminStartDate(text):text]
 }))
 if(automatic)autoOpenedDrafts.add(String(draft.id))
}
function chooseProject(project:any){
 if(!draftModal.value)return
 selectedProjectId.value=project.id
 draftMaterial.value.project_id=project.id
 draftMaterial.value.project_version=String(project.row_version)
 draftMaterial.value.project_name=project.name||''
 draftMaterial.value.project_number=project.code||''
 draftMaterial.value.customer_name=project.customer?.name||draftMaterial.value.customer_name||''
}
function draftPayload(){
 const payload:Record<string,any>={}
 for(const key of draftMaterialKeys){
  const original=(draftMaterial.value[key]||'').trim()
  const raw=isAdminStartDateField(key)?normalizeAdminStartDate(original):original
  if(!raw)continue
  payload[key]=['customer_mold_numbers','internal_mold_numbers'].includes(key)
   ? raw.split(/[,，]/).map(value=>value.trim()).filter(Boolean)
   : raw
 }
 if(draftMaterial.value.project_id)payload.project_id=draftMaterial.value.project_id
 if(draftMaterial.value.project_version)payload.project_version=Number(draftMaterial.value.project_version)
 return payload
}
function submitDraftAction(decision?:string){
 if(!draftModal.value||props.archived)return
 const material=draftPayload()
 const selectionError=validateAdminStartSelection(material,sourceContext.value?.projects,decision?'decision':'update')
 if(selectionError){draftError.value=selectionError;return}
 if(!draftReason.value.trim()){draftError.value=decision?'请先填写本次决定依据。':'请填写本次补充资料的依据。';return}
 const tool=decision?'prepare_admin_start_notice_decision':'prepare_admin_start_notice_update'
 const args:any={draft_id:draftModal.value.id,expected_row_version:draftModal.value.row_version,material_snapshot:material,reason:draftReason.value.trim()}
 if(decision)args.decision=decision
 emit('draft-action',{prompt:`请使用 ${tool} 工具处理内部开工通知草稿，不要直接写入业务事实。参数 JSON：${JSON.stringify(args)}`})
 draftModal.value=null
}
function submitDispatch(){
 if(!draftModal.value||props.archived)return
 const keys=departmentOptions.filter(option=>dispatchSelection.value[option.key]).map(option=>option.key)
 if(!keys.length){draftError.value='请至少勾选一个接收部门。';return}
 if(!draftReason.value.trim()){draftError.value='请填写本次分发依据。';return}
 const args={draft_id:draftModal.value.id,expected_row_version:draftModal.value.row_version,department_keys:keys,reason:draftReason.value.trim()}
 emit('draft-action',{prompt:`请使用 prepare_admin_start_department_dispatch 工具把内部开工通知单分发给选定部门，不要直接写入业务事实。参数 JSON：${JSON.stringify(args)}`})
 draftModal.value=null
}
function submitAck(departmentKey:string){
 if(!draftModal.value||props.archived)return
 if(!draftReason.value.trim()){draftError.value='请填写该部门确认收到的核对依据。';return}
 const args={draft_id:draftModal.value.id,expected_row_version:draftModal.value.row_version,department_key:departmentKey,note:draftReason.value.trim()}
 emit('draft-action',{prompt:`请使用 prepare_admin_start_department_ack 工具记录该部门已确认收到内部开工通知单，不要直接写入业务事实。参数 JSON：${JSON.stringify(args)}`})
 draftModal.value=null
}
watch(items,list=>{
 const drafts=list.flatMap((item:any)=>item.admin_start_drafts||[]).filter((row:any)=>['ADMIN_PENDING_INPUT','ADMIN_CONFIRMED','DEPARTMENTS_NOTIFIED'].includes(row.status)&&!autoOpenedDrafts.has(String(row.id)))
 const draft=[...drafts].reverse()[0]
 if(draft){drafts.forEach(row=>autoOpenedDrafts.add(String(row.id)));openDraft(draft)}
})
let epoch=0,loading=false,stopped=false,tick=0
const types:Record<string,string>={BID_NOTICE:'中标通知',CUSTOMER_START_NOTICE:'客户开模通知',SALES_CONTRACT:'销售合同',MOLD_DRAWING:'模具图纸',ENGINEERING_CONTACT:'工程联络单',OTHER:'其他'}
const states:Record<string,string>={PRECLASSIFYING:'正在自动解析并判断类型',AWAITING_TYPE_CONFIRMATION:'文件类型待本人确认',FULL_OCR_QUEUED:'完整识别已排队',FULL_OCR_PROCESSING:'正在识别合同字段',AWAITING_FIELD_CONFIRMATION:'识别完成，候选字段待复核',OCR_FAILED:'识别失败，需要处理',CLASSIFIED_ARCHIVED:'已按确认类型归档',READY_FOR_DRAFT:'字段复核完成'}
const adminStartStates:Record<string,string>={ADMIN_PENDING_INPUT:'待超级管理员确认承接',ADMIN_CONFIRMED:'已确认承接，待补充项目',READY_FOR_CONTRACT_MATCH:'等待合同匹配',REJECTED:'已拒绝',NEEDS_REVIEW:'需要超级管理员核查'}
const draftFieldMeta=[
 {key:'project_name',label:'项目名称'}, {key:'project_number',label:'项目编号'},
 {key:'customer_name',label:'客户名称'}, {key:'external_order_number',label:'外部订单号'},
 {key:'customer_mold_numbers',label:'客户模具号（逗号分隔）'},
 {key:'internal_mold_numbers',label:'内部模具编号（人工填写，逗号分隔）'}, {key:'effective_date',label:'内部开工日期'},
 {key:'customer_due_date',label:'客户交期'},
]
const draftMaterialKeys=draftFieldMeta.map(field=>field.key)
const jobStates:Record<string,string>={QUEUED:'排队中',PROCESSING:'处理中',RETRY_WAIT:'等待自动重试',SUCCEEDED:'本阶段完成',FAILED:'失败'}
const errors:Record<string,string>={DOCUMENT_MODEL_READ_TIMEOUT:'文档模型长时间未响应',DOCUMENT_MODEL_TOTAL_TIMEOUT:'文档模型超过单批处理时限',DOCUMENT_MODEL_OUTPUT_TRUNCATED:'模型输出被截断',DOCUMENT_MODEL_OUTPUT_INVALID:'模型输出未通过结构校验',DOCUMENT_FIELD_SOURCE_INVALID:'字段来源未通过校验',DOCUMENT_MODEL_BATCH_TOO_LARGE:'单页内容超过安全提取预算',DOCUMENT_MODEL_CALL_LIMIT:'本轮模型调用次数达到上限',DOCUMENT_MODEL_FAILED:'文档模型调用失败',OCR_SERVICE_UNAVAILABLE:'文字识别服务不可用'}
function value(v:any){const raw=v&&typeof v==='object'&&'value' in v?v.value:v;return raw==null?'未识别':typeof raw==='object'?JSON.stringify(raw):String(raw)}
function elapsed(job:any){const start=Date.parse(job.created_at),end=job.finished_at?Date.parse(job.finished_at):clock.value;if(!Number.isFinite(start)||!Number.isFinite(end))return '';const seconds=Math.max(0,Math.floor((end-start)/1000));return `${Math.floor(seconds/60)}分${seconds%60}秒`}
async function load(){
 if(!props.conversationId||loading||stopped)return
 const current=epoch,id=props.conversationId;loading=true
 try{const result=await api(`/conversations/${encodeURIComponent(id)}/documents`);if(stopped||current!==epoch)return
  items.value=result;error.value='';emit('processed',result.flatMap((i:any)=>i.files.map((f:any)=>f.file_id)))
  for(const item of result){if(forms.value[item.id]?.version!==item.row_version){forms.value[item.id]={version:item.row_version,files:item.files.map((f:any,index:number)=>({intake_file_id:f.id,document_type:f.suggested_type||f.classification?.document_type||'',contract_group_key:`合同${index+1}`}))}}}
 }catch(e:any){if(current===epoch){error.value=`无法刷新后台状态：${e.message}`;if([401,403,404].includes(e.status)){items.value=[];forms.value={};intent.value=null;selectedFields.value=null}}}
 finally{loading=false;if(current!==epoch&&!stopped)void load()}
}
watch(()=>props.conversationId,()=>{epoch++;items.value=[];forms.value={};intent.value=null;selectedFields.value=null;error.value='';void load()},{immediate:true})
watch(()=>props.refreshKey,()=>void load())
const timer=setInterval(()=>{clock.value=Date.now();tick++;if(typeof document!=='undefined'&&document.hidden)return;const active=items.value.some(i=>['PRECLASSIFYING','FULL_OCR_QUEUED','FULL_OCR_PROCESSING'].includes(i.status));if(tick%(active?3:12)===0)void load()},1000)
onUnmounted(()=>{stopped=true;epoch++;clearInterval(timer)})
async function prepare(item:any,operation:'types'|'retry'){
 if(busy.value||props.archived)return
 busy.value=true;error.value='';const current=epoch
 try{const body:any={document_intake_id:item.id,expected_version:item.row_version}
  if(operation==='types')body.files=forms.value[item.id].files.map((f:any)=>({...f,contract_group_key:f.document_type==='SALES_CONTRACT'?f.contract_group_key.trim():null}))
  const result=await post(`/document-intakes/${item.id}/${operation==='types'?'type':'retry'}-intent`,body)
  if(current===epoch)intent.value=result
 }catch(e:any){if(current===epoch)error.value=e.message}finally{busy.value=false}
}
async function confirm(){
 if(!intent.value||busy.value||props.archived)return
 busy.value=true;error.value='';const current=epoch
 try{await post(`/human-actions/${intent.value.id}/confirm`,{challenge:intent.value.challenge});if(current===epoch){intent.value=null;await load()}}
 catch(e:any){if(current===epoch){if([403,409].includes(e.status)){intent.value=null;await load()}if(current===epoch)error.value=e.message}}
 finally{busy.value=false}
}
function reviewed(){selectedFields.value=null;void load()}
</script>
<template>
 <section v-if="items.length||error" class="document-activity" aria-label="后台文件识别">
  <p v-if="error" class="document-error" role="alert">{{error}}。显示的旧状态不代表后台最新进展。</p>
  <article v-for="item in items" :key="item.id" class="document-task">
   <header><strong>{{states[item.status]||item.status}}</strong><small>{{shanghai(item.created_at)}}</small></header>
   <p class="muted">上传后由系统自动解析，无需发送聊天指令。机器候选不等于正式业务事实。</p>
   <div v-for="(file,index) in item.files" :key="file.id" class="document-file">
    <FileMaterial :file="{...file,id:file.file_id}" @error="error=$event"/>
    <p v-if="file.ocr_job" class="muted" role="status">{{jobStates[file.ocr_job.status]||file.ocr_job.status}} · {{file.ocr_job.phase==='PRECLASSIFY'?'类型预分类':'合同字段提取'}} · 耗时 {{elapsed(file.ocr_job)}} · 文字缓存 {{file.ocr_job.cached_pages}} 页<span v-if="file.ocr_job.attempts"> · 已执行 {{file.ocr_job.attempts}} 次</span><span v-if="file.ocr_job.current_attempt"> · 当前第 {{file.ocr_job.current_attempt}} 次</span></p>
    <p v-if="file.ocr_job?.retry_at" class="muted">下次重试：{{shanghai(file.ocr_job.retry_at)}}</p>
    <p v-if="file.ocr_job?.previous_error" class="document-warning">上一次尝试未完成：{{errors[file.ocr_job.previous_error]||file.ocr_job.previous_error}}；当前正在继续处理，已有成功批次会复用。</p>
    <p v-if="file.ocr_job?.error_code" class="document-error">处理失败：{{errors[file.ocr_job.error_code]||file.ocr_job.error_code}}</p>
    <div v-if="item.status==='AWAITING_TYPE_CONFIRMATION'&&forms[item.id]" class="document-confirm-fields">
     <p>推荐类型：{{types[file.suggested_type||file.classification?.document_type]||'尚无法判断'}}<span v-if="(file.suggested_confidence||file.classification?.confidence)!=null"> · 置信度 {{Math.round(Number(file.suggested_confidence||file.classification?.confidence)*100)}}%</span></p>
     <label>确认类型<select v-model="forms[item.id].files[index].document_type" :disabled="busy||archived"><option value="" disabled>请选择</option><option v-for="(name,key) in types" :key="key" :value="key">{{name}}</option></select></label>
     <label v-if="forms[item.id].files[index].document_type==='SALES_CONTRACT'">合同分组<input v-model="forms[item.id].files[index].contract_group_key" maxlength="60" :disabled="busy||archived"/></label>
    </div>
   </div>
   <p v-if="item.duplicate_sha_groups?.length" class="document-warning">存在同内容的上传记录，请核对本批文件是否需要继续办理，避免重复登记。</p>
   <section v-for="draft in item.admin_start_drafts||[]" :key="draft.id" class="admin-start-next-step" aria-label="内部开工通知下一步">
    <header><strong>下一步：内部开工通知</strong><span>{{adminStartStates[draft.status]||draft.status}}</span></header>
    <p v-if="draft.status==='ADMIN_PENDING_INPUT'">系统已生成超级管理员专属草稿。请先核对中标资料并选择承接方式，项目资料在承接后补充。</p>
    <p v-else-if="draft.status==='ADMIN_CONFIRMED'">承接方式已确认。请继续选择系统项目并补充内部模具编号、开工日期。</p>
    <button v-if="draft.status==='ADMIN_PENDING_INPUT'||draft.status==='ADMIN_CONFIRMED'||draft.status==='NEEDS_REVIEW'" type="button" class="admin-start-open" :disabled="busy||archived" @click="openDraft(draft)">打开内部开工通知单</button>
    <p v-else-if="draft.status==='NEEDS_REVIEW'">自动生成未完成，请超级管理员打开消息中心核查失败原因。</p>
    <p v-else-if="draft.status==='READY_FOR_CONTRACT_MATCH'">承接方式已确认；上传销售合同后系统会生成匹配候选，正式关联仍需人工确认。</p>
    <p v-else>当前草稿状态：{{adminStartStates[draft.status]||draft.status}}。</p>
   </section>
   <template v-if="item.status==='AWAITING_TYPE_CONFIRMATION'"><p class="muted">同一分组表示同一份合同及其附件；默认每个文件独立分组，请按实际材料调整。确认前不会开始完整合同提取。</p><button :disabled="busy||archived" @click="prepare(item,'types')">核对文件类型</button></template>
   <button v-if="item.status==='OCR_FAILED'" :disabled="busy||archived" @click="prepare(item,'retry')">查看失败任务并准备重试</button>
   <button v-for="group in item.groups||[]" :key="group.id" type="button" class="document-fields-trigger" aria-haspopup="dialog" :disabled="busy||!!intent" @click="selectedFields={intakeId:item.id,groupId:group.id}">{{group.group_key}} · 查看 {{group.fields.length}} 个候选字段</button>
   <p v-if="item.status==='READY_FOR_DRAFT'" class="document-success">字段复核已完成。下一步请在下方对话框输入“准备合同审批”，系统会先生成审批建议供你确认。</p><p v-if="item.files.some((file:any)=>file.classification?.document_type==='ENGINEERING_CONTACT')" class="document-success">已识别到工程联络单候选；请确认类型后，工程联络 Skill 会继续生成创建 Proposal。</p>
  </article>
 </section>
 <DocumentFieldsDialog v-if="fieldGroup" :key="fieldGroup.id" :group="fieldGroup" :refresh-error="error" :archived="archived" @close="selectedFields=null" @reviewed="reviewed" @error="error=$event"/>
 <Teleport to="body"><div v-if="draftModal" class="modal-shade" role="dialog" aria-modal="true" aria-label="内部开工通知单" @click.self="draftModal=null"><section class="modal admin-start-modal"><header><div><small>内部开工通知单</small><h2>{{draftStep==='material'?'项目部门补充资料':draftStep==='dispatch'?'分发内部通知单给部门':'部门回执与最终决定'}}</h2></div><button type="button" class="icon-button" aria-label="关闭内部开工通知单" @click="draftModal=null">×</button></header><div v-if="draftModal.decision" class="admin-start-stepnav" role="tablist"><button type="button" :class="{active:draftStep==='material'}" @click="draftStep='material'">1 补充资料</button><button type="button" :class="{active:draftStep==='dispatch'}" @click="draftStep='dispatch'">2 分发部门</button><button type="button" :class="{active:draftStep==='receipts'}" @click="draftStep='receipts'">3 回执与决定</button></div><p class="muted">当前角色均由超级管理员兼任。提交后先形成 Tool 操作建议，每个动作仍需本人在确认卡中确认。</p>
<template v-if="draftStep==='material'"><div v-if="sourceContext" class="admin-start-source"><div class="admin-start-source-head"><strong>来源中标邮件</strong><FileMaterial v-if="sourceContext.source_file" :file="{...sourceContext.source_file,file_id:sourceContext.source_file.id}" @error="error=$event"/></div><p v-if="sourceContext.warnings?.length" class="document-warning">{{sourceContext.warnings.join('；')}}</p><div v-if="Object.keys(sourceContext.fields||{}).length" class="admin-start-candidates"><span v-for="(rows,key) in sourceContext.fields" :key="key"><b>{{draftFieldMeta.find((field:any)=>field.key===key)?.label||key}}</b>：<em v-for="row in rows" :key="row.source_block_ids.join('-')">{{row.value}}（第{{row.page_number}}页）</em></span></div><p v-else class="muted">当前邮件尚未提取到可定位的业务字段，请查看原文后补充。</p></div><div v-if="draftModal.decision&&sourceContext?.projects?.length" class="admin-start-projects"><strong>选择系统项目</strong><label v-for="project in sourceContext.projects" :key="project.id" class="admin-start-project"><input type="radio" name="admin-start-project" :checked="selectedProjectId===project.id" :disabled="busy||archived" @change="chooseProject(project)"/><span><b>{{project.code}} · {{project.name}}</b><small>{{project.customer?.name||'未维护客户'}} · 匹配：{{project.matched_by.join('、')||'人工候选'}}</small></span></label></div><p v-else-if="draftModal.decision&&sourceContext" class="document-warning">承接已确认，但当前没有匹配到系统项目候选。请先在项目主数据中建立项目，再返回这里选择；内部模具编号可人工填写。</p><div class="admin-start-form"><template v-for="field in draftFieldMeta" :key="field.key"><label v-if="draftModal.decision||!['project_name','project_number','customer_name','internal_mold_numbers','effective_date'].includes(field.key)">{{field.label}}<input v-model="draftMaterial[field.key]" :type="isAdminStartDateField(field.key)?'date':'text'" :readonly="['project_name','project_number','customer_name'].includes(field.key)" :disabled="busy||archived||field.key==='project_id'||field.key==='project_version'" :placeholder="isAdminStartDateField(field.key)?'请选择日期':field.label"/></label></template></div><label class="admin-start-reason">补充依据<textarea v-model="draftReason" rows="2" :disabled="busy||archived" placeholder="请填写合同、邮件或内部核对依据"/></label></template>
<template v-else-if="draftStep==='dispatch'"><div class="admin-start-departments"><label v-for="option in departmentOptions" :key="option.key" class="check-label"><input type="checkbox" v-model="dispatchSelection[option.key]" :disabled="busy||archived"/>{{option.label}}</label></div><p class="muted">分发后各部门会收到送达记录；是否确认收到由回执步骤逐部门记录。</p><label class="admin-start-reason">分发依据<textarea v-model="draftReason" rows="2" :disabled="busy||archived" placeholder="请填写分发范围依据"/></label></template>
<template v-else><div v-if="(draftModal.departments||[]).length" class="admin-start-receipts"><div v-for="ack in draftModal.departments" :key="ack.department_key" class="admin-start-receipt-row"><span>{{departmentOptions.find(o=>o.key===ack.department_key)?.label||ack.department_key}}</span><strong :class="ack.status==='ACKNOWLEDGED'?'ok':'pending'">{{ack.status==='ACKNOWLEDGED'?'已收到':'未收到'}}</strong><button v-if="ack.status!=='ACKNOWLEDGED'" type="button" :disabled="busy||archived" @click="submitAck(ack.department_key)">标记已收到</button></div></div><p v-else class="muted">尚未分发部门；请先到“分发部门”步骤选择接收部门。</p><p class="muted">回执状态仅展示，不阻塞最终决定。</p><label class="admin-start-reason">回执/决定依据<textarea v-model="draftReason" rows="2" :disabled="busy||archived" placeholder="标记回执或做最终决定时填写依据"/></label></template>
<p v-if="draftError" class="document-error" role="alert">{{draftError}}</p><div class="admin-start-actions"><template v-if="draftStep==='material'"><button type="button" :disabled="busy||archived" @click="submitDraftAction()">保存补充资料</button><button v-if="draftModal.decision" type="button" :disabled="busy||archived" @click="draftStep='dispatch'">下一步：分发部门</button></template><template v-else-if="draftStep==='dispatch'"><button type="button" :disabled="busy||archived" @click="draftStep='material'">上一步</button><button type="button" class="primary" :disabled="busy||archived" @click="submitDispatch">确认分发</button></template><template v-else><button type="button" :disabled="busy||archived" @click="draftStep='material'">查看资料</button></template><template v-if="!draftModal.decision"><button type="button" :disabled="busy||archived" @click="submitDraftAction('INTERNAL_ACCEPTED')">内部承接</button><button type="button" :disabled="busy||archived" @click="submitDraftAction('FULL_OUTSOURCE_ACCEPTED')">整套委外</button><button type="button" class="danger" :disabled="busy||archived" @click="submitDraftAction('REJECTED')">拒绝</button></template></div></section></div><div v-if="intent" class="modal-shade"><section class="modal" role="dialog" aria-modal="true" aria-label="本人确认文档操作"><h3>请核对本次文档操作</h3><dl><template v-for="(v,k) in intent.display" :key="String(k)"><dt>{{k}}</dt><dd>{{value(v)}}</dd></template></dl><p>只有点击“确认执行”才会应用上述类型或重新排队。不会创建正式合同或自动批准业务。</p><p v-if="error" role="alert">{{error}}</p><div class="actions"><button :disabled="busy" @click="intent=null">返回修改</button><button class="primary" :disabled="busy||archived" @click="confirm">确认执行</button></div></section></div></Teleport>
</template>
<style scoped>
.document-activity{max-width:860px;margin:20px auto;padding:0 20px}.document-task{margin:16px 0;padding:16px;border:1px solid var(--border);border-radius:12px;background:var(--surface)}header{display:flex;justify-content:space-between;gap:12px}small,.muted{color:var(--muted);font-size:12px;line-height:1.6}.document-file{padding:10px 0;border-bottom:1px solid var(--border)}.document-confirm-fields{display:flex;flex-wrap:wrap;gap:12px;align-items:center}.document-confirm-fields p{width:100%;margin:4px 0}label{display:flex;align-items:center;gap:8px}input,select{max-width:220px;padding:6px}.document-error{color:var(--error,#b4534b)}.document-success{color:#7fc9a1}.document-warning{color:#a66d00}.admin-start-next-step{margin:14px 0 4px;padding:12px;border:1px solid #5b6b7d;border-radius:8px;background:color-mix(in srgb,var(--accent) 7%,var(--surface));}.admin-start-next-step header{align-items:center}.admin-start-next-step header span{color:var(--accent);font-size:12px}.admin-start-next-step p{margin:7px 0 0;font-size:12px;line-height:1.7}.admin-start-open{margin-top:10px;padding:7px 12px;background:var(--accent);color:#fff;border-color:var(--accent)}.admin-start-modal{width:min(720px,calc(100vw - 32px));max-height:90vh;overflow:auto}.admin-start-modal header{align-items:flex-start}.admin-start-modal h2{margin:3px 0 0;font-size:19px}.admin-start-form{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:18px 0}.admin-start-form label,.admin-start-reason{display:flex;flex-direction:column;align-items:stretch;gap:6px;color:var(--muted);font-size:12px}.admin-start-form input,.admin-start-reason textarea{max-width:none;width:100%;box-sizing:border-box}.admin-start-source{margin:16px 0;padding:12px;border:1px solid var(--border);border-radius:8px;background:color-mix(in srgb,var(--accent) 5%,var(--surface))}.admin-start-source-head{display:flex;flex-direction:column;gap:8px}.admin-start-candidates{display:flex;flex-direction:column;gap:6px;margin-top:10px;font-size:12px}.admin-start-candidates span{display:flex;gap:8px;flex-wrap:wrap}.admin-start-candidates em{font-style:normal;color:var(--text)}.admin-start-projects{display:flex;flex-direction:column;gap:8px;margin:14px 0}.admin-start-project{align-items:flex-start;padding:9px;border:1px solid var(--border);border-radius:8px}.admin-start-project>span{display:flex;flex-direction:column;gap:4px}.admin-start-project small{display:block}.admin-start-molds{display:flex;flex-wrap:wrap;gap:10px;margin-top:4px}.admin-start-actions{display:flex;justify-content:flex-end;flex-wrap:wrap;gap:8px;margin-top:14px}.admin-start-actions .danger{background:#6b302d;border-color:#8e4944;color:#fff}.admin-start-stepnav{display:flex;gap:6px;margin:14px 0 4px}.admin-start-stepnav button{flex:1;padding:8px 6px;font-size:12px;border-radius:8px;background:var(--surface);border:1px solid var(--border);color:var(--muted)}.admin-start-stepnav button.active{background:var(--accent);color:#fff;border-color:var(--accent)}.admin-start-departments{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}.admin-start-departments label{flex-direction:row;align-items:center;font-size:13px;color:var(--text)}.admin-start-receipts{display:flex;flex-direction:column;gap:8px;margin:14px 0}.admin-start-receipt-row{display:flex;align-items:center;gap:12px;padding:9px 11px;border:1px solid var(--border);border-radius:8px;background:var(--surface)}.admin-start-receipt-row span{flex:1}.admin-start-receipt-row strong.ok{color:#7fc9a1}.admin-start-receipt-row strong.pending{color:#c9942a}.admin-start-receipt-row button{padding:5px 10px;font-size:12px}.document-fields-trigger{display:flex;max-width:100%;margin:10px 0;padding:4px 0;border:0;background:transparent;color:var(--accent);text-align:left;white-space:normal;overflow-wrap:anywhere}.document-fields-trigger:hover{text-decoration:underline}dl{display:grid;grid-template-columns:90px 1fr;gap:10px}dt{color:var(--muted)}dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}
</style>
