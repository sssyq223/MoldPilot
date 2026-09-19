<script setup lang="ts">
import {fieldName,valueText} from '../uiText'
const props=defineProps<{value:any;highlightsOnly?:boolean;contractNumber?:string}>()
const hidden=new Set(['id','subject_id','plan_id','created_at','source_line_id','case_id','department_id','plan_subject_id','source_pause_subject_id','pause_id','task_id','closure_case_id','source_termination_subject_id','opened_by','closed_by','updated_by','analysis','attachments','source_snapshot','source_summary','erp_order_material','source_system','source_resource_type','source_resource_id','source_resource_version','source_as_of','source_snapshot_hash'])
const milestoneNames:Record<string,string>={design:'设计/工艺/出图',purchase:'采购',machining:'加工',assembly:'装配',trial:'试模/调试',delivery:'交付/验收'}
function tasks(){return props.value?.analysis?.tasks||[]}
function milestoneCoverage(){return props.value?.analysis?.milestone_coverage}
function revisionImpact(){return props.value?.analysis?.revision_impact}
function planChangeCandidates(){return props.value?.analysis?.plan_change_candidates||[]}
function statusName(value:string){return valueText('status',value)}
function milestoneList(items:any[]){return (items||[]).map(item=>item.name||item.key).filter(Boolean).join('、')||'—'}
function designRevisionLabel(){
 const impact=revisionImpact()||{}
 const previous=impact.previous_design?.drawing_revision||impact.previous_design?.number||'上一版'
 const latest=impact.latest_design?.drawing_revision||impact.latest_design?.number||'当前版'
 return `${previous} → ${latest}`
}
function taskList(tasks:any[]){return (tasks||[]).map(task=>task.name||task.key).filter(Boolean).join('、')||'—'}
function changeSummary(){
 const summary=revisionImpact()?.summary||{}
 return `新增 ${summary.added||0}，移除 ${summary.removed||0}，变化 ${summary.changed||0}，未变 ${summary.unchanged||0}`
}
function displayValue(key:string,value:any){
 const currency=String(props.value?.currency||'')
 const amount=Number(value)
 if((key==='amount'||key.endsWith('_amount'))&&currency&&Number.isFinite(amount)){
  try{return new Intl.NumberFormat('zh-CN',{style:'currency',currency,minimumFractionDigits:2,maximumFractionDigits:2}).format(amount)}catch{}
 }
 if(key==='currency'&&currency)return `${valueText(key,value)}（${currency}）`
 return valueText(key,value)
}

</script>
<template><div class="business-facts"><section v-if="tasks().length" class="surface">
  <h4>项目大节点 / 计划任务表</h4>
  <div class="table-scroll"><table><thead><tr><th>节点</th><th>状态</th><th>计划开始</th><th>计划结束</th><th>前置</th></tr></thead><tbody>
    <tr v-for="task in tasks()" :key="task.id||task.key"><td>{{task.name||task.key}}</td><td>{{statusName(task.status)}}</td><td>{{task.planned_start||'—'}}</td><td>{{task.planned_end||'—'}}</td><td>{{(task.prerequisites||[]).join('、')||'—'}}</td></tr>
  </tbody></table></div>
  <div v-if="milestoneCoverage()" class="business-facts">
    <div class="fact-line"><span class="muted">已覆盖大节点</span><span>{{Object.entries(milestoneCoverage().covered||{}).map(([key,items])=>`${milestoneNames[key]||key}：${milestoneList(items as any[])}`).join('；')||'—'}}</span></div>
    <div class="fact-line"><span class="muted">缺少大节点</span><span>{{(milestoneCoverage().missing||[]).map((key:string)=>milestoneNames[key]||key).join('、')||'—'}}</span></div>
    <p class="muted small">{{milestoneCoverage().note}}</p>
  </div>
</section><section v-if="revisionImpact()" class="surface">
  <h4>设计改版影响</h4>
  <div class="fact-line"><span class="muted">比较状态</span><span>{{revisionImpact().status}}</span></div>
  <div class="fact-line"><span class="muted">图纸版本</span><span>{{designRevisionLabel()}}</span></div>
  <div class="fact-line"><span class="muted">差异汇总</span><span>{{changeSummary()}}</span></div>
  <div class="fact-line"><span class="muted">路线变化</span><span>{{revisionImpact().summary?.route_changed||0}}</span></div>
  <div class="fact-line"><span class="muted">任务关联变化</span><span>{{revisionImpact().summary?.task_link_changed||0}}</span></div>
  <div v-if="revisionImpact().affected_plan_tasks?.length" class="fact-line"><span class="muted">影响计划任务</span><span>{{taskList(revisionImpact().affected_plan_tasks)}}</span></div>
  <p v-if="revisionImpact().limitations?.length" class="muted small">{{revisionImpact().limitations.join(' ')}}</p>
</section><section v-if="planChangeCandidates().length" class="surface">
  <h4>计划复核候选</h4>
  <div v-for="candidate in planChangeCandidates()" :key="candidate.source+candidate.candidate_status" class="business-facts">
    <div class="fact-line"><span class="muted">候选状态</span><span>{{candidate.candidate_status}}</span></div>
    <div class="fact-line"><span class="muted">下一步工具</span><span>{{(candidate.recommended_next_tools||[]).join(' → ')||'—'}}</span></div>
    <div class="fact-line"><span class="muted">计划基线</span><span>{{candidate.plan_change_prepare_seed?.previous_plan_number||candidate.plan_change_prepare_seed?.previous_id||'待查询确认'}}</span></div>
    <div class="fact-line"><span class="muted">受影响节点</span><span>{{(candidate.plan_change_prepare_seed?.candidate_task_keys||[]).join('、')||'—'}}</span></div>
    <p v-if="candidate.evidence_gaps?.length" class="muted small">缺口：{{candidate.evidence_gaps.join('；')}}</p>
    <p class="muted small">{{candidate.guardrail}}</p>
  </div>
</section><template v-if="!props.highlightsOnly"><template v-for="(item,key) in value" :key="String(key)"><section v-if="!hidden.has(String(key))&&item!==null&&item!==undefined&&item!==''">
  <template v-if="Array.isArray(item)"><h4>{{fieldName(String(key))}}</h4><div v-for="(row,index) in item" :key="index" class="surface"><BusinessFacts v-if="row&&typeof row==='object'" :value="row" :contract-number="props.contractNumber"/><span v-else>{{displayValue(String(key),row)}}</span></div><p v-if="!item.length" class="muted">暂无记录</p></template>
  <BusinessFacts v-else-if="item&&typeof item==='object'" :value="item" :contract-number="props.contractNumber"/>
  <div v-else class="fact-line"><span class="muted">{{fieldName(String(key))}}</span><span>{{String(key)==='contract_id'?(props.contractNumber||'当前合同'):displayValue(String(key),item)}}</span></div>
</section></template></template></div></template>
