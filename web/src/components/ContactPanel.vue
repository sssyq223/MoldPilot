<script setup lang="ts">
import {ref,watch} from 'vue'
import {api,shanghai} from '../api'
import {statusName,numberText} from '../uiText'
import FileMaterial from './FileMaterial.vue'
const props=defineProps<{initialId?:string}>()
const emit=defineEmits<{error:[message:string],approval:[id:string]}>()
const selected=ref<any>(null),loading=ref(false),failed=ref(false)
let request=0
const status:Record<string,string>={UNASSIGNED:'待部门分派',ASSIGNED:'待处理人反馈',RESPONDED:'待复验',VERIFIED:'复验合格',CANCELLED:'已撤销'}
const events:Record<string,string>={NOTE:'过程记录',TASK_CREATED:'新增协作事项',ASSIGNED:'分派处理人',RESPONDED:'处理反馈',ATTACHMENT_ADDED:'关联附件版本',RESOLUTION_SUBMITTED:'提交方案审批',REVIEWER_SET:'指定验收负责人',TASK_CANCELLED:'撤销协作事项',TASK_REVIEWED:'复验处理结果',CLOSED:'人工关闭'}
async function load(){
 const token=++request;selected.value=null;failed.value=false
 if(!props.initialId){loading.value=false;return}
 loading.value=true
 try{const result=await api('/contacts/'+props.initialId);if(token===request)selected.value=result}
 catch(e:any){if(token===request){failed.value=true;emit('error',e.message)}}
 finally{if(token===request)loading.value=false}
}
watch(()=>props.initialId,load,{immediate:true})
</script>
<template>
<h2>联络单材料</h2>
<p v-if="loading" role="status">正在读取当前联络单…</p>
<p v-else-if="!initialId" class="empty">从会话结果选择需要查看的联络单。</p>
<button v-if="failed" @click="load">重新读取材料</button>
<section v-if="selected" aria-label="联络单详情" class="contact-material">
 <header><h3>{{selected.title}}</h3><p class="muted">发起人：{{selected.creator_name}} · {{shanghai(selected.created_at)}}</p><p class="muted">{{selected.collaboration_status==='CLOSED'?'已人工关闭':selected.mode==='HISTORY'?'历史补录':'线上协作中'}}</p></header>
 <p class="preserve">{{selected.description}}</p>
 <h3>附件材料</h3><p v-if="!selected.attachments?.length" class="muted">暂无已关联附件。</p><FileMaterial v-for="file in selected.attachments||[]" :key="file.id" :file="file" @error="emit('error',$event)"/>
 <p v-if="selected.reviewer_name" class="muted">指定验收负责人：{{selected.reviewer_name}}</p>
 <p v-if="selected.closed_at" class="muted">关闭人：{{selected.closed_by_name}} · {{shanghai(selected.closed_at)}}</p>
 <h3>处理方案与审批</h3><p v-if="!selected.resolutions?.length" class="muted">暂无当前权限可见的处理方案审批记录。</p>
 <article v-for="plan in selected.resolutions||[]" :key="plan.id" class="surface"><strong>{{numberText(plan.number)}} · {{statusName(plan.status)}}</strong><p class="preserve">{{plan.solution}}</p><button v-if="plan.instance_id" @click="emit('approval',plan.instance_id)">查看方案审批材料与节点</button></article>
 <h3>协作进度</h3><p v-if="!selected.tasks.length" class="muted">暂无线上协作事项。</p>
 <article v-for="t in selected.tasks" :key="t.id" class="surface">
  <strong>{{t.title}}</strong><p>{{t.department_name}} → {{t.assignee_name||'待指定处理人'}} · {{status[t.status]}}</p>
  <p v-if="!t.department_active" class="muted">责任部门已停用。</p><p v-if="t.response" class="preserve">{{t.response}}</p>
 </article>
 <p class="muted">协作反馈与正式审批分别记录；已反馈不表示批准或关闭。</p>
 <h3>过程记录</h3><p v-if="!selected.records.length" class="muted">暂无过程记录。</p>
 <article v-for="r in selected.records" :key="r.id" class="audit-row">
  <strong>{{events[r.kind]||'协作记录'}} · {{r.author_name}}</strong>
  <small>发生：{{shanghai(r.occurred_at)}} · 录入：{{shanghai(r.recorded_at)}}</small>
  <p v-if="r.detail.source==='OFFLINE'">线下补录 · {{r.detail.participants}}</p>
  <p v-if="r.detail.department_name">{{r.detail.department_name}} · {{r.detail.title}}</p>
  <p v-if="r.detail.assignee_name">处理人：{{r.detail.assignee_name}}</p>
  <p v-if="r.kind==='ATTACHMENT_ADDED'">{{r.detail.title}} · 第 {{r.detail.version}} 版 · {{r.detail.filename}}</p>
  <p v-if="r.kind==='TASK_REVIEWED'">{{r.detail.decision==='PASS'?'复验合格':'退回整改'}} · {{r.detail.evidence}}</p>
  <p v-if="r.kind==='REVIEWER_SET'">验收负责人：{{r.detail['验收负责人']}} · {{r.detail['指定依据']}}</p>
  <p v-if="r.kind==='CLOSED'">{{r.detail['关闭依据']}}</p>
  <p v-if="r.kind==='TASK_CANCELLED'">{{r.detail['撤销事项']}} · {{r.detail['撤销依据']}}</p>
  <p v-if="r.detail['说明']" class="muted">{{r.detail['说明']}}</p>
  <p v-if="r.kind==='RESOLUTION_SUBMITTED'&&r.detail['处理方案']">{{r.detail['处理方案']}} · {{r.detail['审批流程']}} · 第 {{r.detail['流程版本']}} 版</p>
  <p class="preserve">{{r.detail.content||r.detail.reason}}</p>
 </article>
</section>
</template>
<style scoped>
.contact-material{display:flex;flex-direction:column;gap:12px}
.contact-material h3,.contact-material p{margin:0}
.contact-material .surface{padding:16px;display:grid;gap:10px}
.contact-material .audit-row p{margin-top:8px}
</style>
