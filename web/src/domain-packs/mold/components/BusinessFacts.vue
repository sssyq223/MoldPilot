<script setup lang="ts">
import {capabilityName,fieldName,valueText} from '../uiText'
const props=defineProps<{value:any;highlightsOnly?:boolean;contractNumber?:string}>()
const hidden=new Set(['id','subject_id','plan_id','created_at','source_line_id','case_id','department_id','plan_subject_id','source_pause_subject_id','pause_id','task_id','closure_case_id','source_termination_subject_id','opened_by','closed_by','updated_by','analysis','attachments','source_snapshot','source_summary','erp_order_material','source_system','source_resource_type','source_resource_id','source_resource_version','source_as_of','source_snapshot_hash'])
const milestoneNames:Record<string,string>={design:'设计/工艺/出图',purchase:'采购',machining:'加工',assembly:'装配',trial:'试模/调试',delivery:'交付/验收'}
function tasks(){return props.value?.analysis?.tasks||[]}
function kickoffLifecycle(){return props.value?.analysis?.kickoff_lifecycle}
function executionLifecycle(){return props.value?.analysis?.execution_lifecycle}
function milestoneCoverage(){return props.value?.analysis?.milestone_coverage}
function revisionImpact(){return props.value?.analysis?.revision_impact}
function planChangeCandidates(){return props.value?.analysis?.plan_change_candidates||[]}
function statusName(value:string){return valueText('status',value)}
const phaseNames:Record<string,string>={REJECTED:'已拒绝承接',ACCEPTANCE:'承接确认',START_PREPARATION:'正式开工准备',PLAN_APPROVAL:'基线计划审批',EXECUTION:'项目执行',PLAN_HANDOFF:'基线计划交接',DESIGN_ENGINEERING:'设计与工艺',PROCUREMENT:'采购执行',FULL_OUTSOURCE:'整套委外协同',MANUFACTURING_QUALITY:'制造与质检',ASSEMBLY_TRIAL:'装配与试模',DELIVERY_ACCEPTANCE:'交付与客户验收',EXECUTION_COMPLETED:'执行链路已完成',EXECUTION_VISIBILITY_GAP:'执行链路可见性不足'}
const stageFactNames:Record<string,string>={latest_acceptance:'承接',latest_rejection:'拒单',effective_contract:'合同',latest_internal_start:'开工通知',active_plan:'基线计划',latest_effective_design:'生效设计',pending_count:'待审',open_count:'进行中',workflow_count:'可选流程',history_count:'历史合同',late_expected_count:'逾期补齐',task_count:'计划任务',project_status:'项目状态',can_prepare:'可准备开工',missing_milestones:'缺少大节点',route_counts:'路线构成',warning_count:'风险提示',execution_mode:'加工方式',process_task_count:'工序任务',started_count:'已开工',done_count:'已完工',has_effective_price:'有效价格',has_design_procurement_need:'采购需求',has_purchase_request:'采购申请',has_purchase_order:'采购订单',has_unshipped_order_line:'未完全发货',has_effective_contract:'生效合同',has_signed_contract_file:'签署文件',has_supplier_progress_policy:'供应商上报规则',has_supplier_progress_report:'供应商节点上报',has_supplier_shipment_or_receipt:'供应商发货/收货',has_independent_quality_report:'独立质检报告',has_assembly_plan_node:'装配节点',has_assembly_order:'装配工单',has_assembly_done:'装配完工',has_trial_request:'试模安排',has_trial_result:'试模报告',has_trial_passed:'试模通过',has_delivery_plan_node:'交付节点',has_stock_out_movement:'出库记录',has_customer_signature:'客户签收',has_customer_acceptance:'客户验收',has_structured_logistics_price:'物流价格依据'}
function compactSubject(value:any){return [value?.number,value?.contract_number,value?.status&&statusName(value.status)].filter(Boolean).join(' · ')||'—'}
function stageObjectText(key:string,value:any){
 if(key==='route_counts')return Object.entries(value||{}).map(([route,count])=>`${valueText('route',route)} ${count}`).join('、')||'—'
 return compactSubject(value)
}
function stageFactText(facts:any){
 const parts:string[]=[]
 for(const [key,value] of Object.entries(facts||{})){
  if(value===null||value===undefined||value===''||(Array.isArray(value)&&!value.length))continue
  const label=stageFactNames[key]||fieldName(key)
  const text=typeof value==='object'&&!Array.isArray(value)?stageObjectText(key,value):Array.isArray(value)?value.map(item=>key==='missing_milestones'?(milestoneNames[item]||item):item).join('、'):typeof value==='boolean'?(value?'是':'否'):key==='project_status'?statusName(String(value)):key.endsWith('_count')?String(value):valueText(key,value)
  parts.push(`${label} ${text}`)
 }
 return parts.join('；')||'尚无可展示事实'
}
function stageNextTool(stage:any){return stage?.action_tool||stage?.query_tool}
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
<template><div class="business-facts"><section v-if="kickoffLifecycle()" class="surface">
  <h4>项目启动链路</h4>
  <div class="fact-line"><span class="muted">当前阶段</span><span>{{phaseNames[kickoffLifecycle().phase]||kickoffLifecycle().phase}}</span></div>
  <div class="table-scroll"><table><thead><tr><th>业务阶段</th><th>状态</th><th>已知事实</th><th>阻塞或说明</th><th>下一步能力</th></tr></thead><tbody>
    <tr v-for="stage in kickoffLifecycle().stages||[]" :key="stage.key">
      <td>{{stage.name}}</td><td>{{statusName(stage.state)}}</td><td>{{stageFactText(stage.facts)}}</td>
      <td>{{(stage.blockers||[]).join('；')||(stage.parallel?'可与主线并行办理':'—')}}</td>
      <td>{{stageNextTool(stage)?capabilityName(stageNextTool(stage)):'—'}}</td>
    </tr>
  </tbody></table></div>
  <div v-if="kickoffLifecycle().recommended_next_steps?.length" class="business-facts">
    <h4>建议下一步</h4>
    <div v-for="item in kickoffLifecycle().recommended_next_steps" :key="item.kind+item.stage" class="fact-line">
      <span class="muted">{{item.kind==='PRIMARY'?'主线':'并行'}}</span>
      <span>{{capabilityName(item.tool)}} · {{item.reason}}{{item.requires_user_confirmation?'（需本人确认）':''}}</span>
    </div>
  </div>
  <p v-if="kickoffLifecycle().access_gaps?.length" class="muted small">未读取：{{kickoffLifecycle().access_gaps.join('、')}}</p>
  <p v-if="kickoffLifecycle().guardrails?.length" class="muted small">{{kickoffLifecycle().guardrails.join(' ')}}</p>
</section><section v-if="executionLifecycle()" class="surface">
  <h4>项目执行链路</h4>
  <div class="fact-line"><span class="muted">当前阶段</span><span>{{phaseNames[executionLifecycle().phase]||executionLifecycle().phase}}</span></div>
  <div class="fact-line"><span class="muted">当前焦点</span><span>{{executionLifecycle().current_focus?.name||'待核对'}} · {{statusName(executionLifecycle().current_focus?.state)}}</span></div>
  <div class="fact-line"><span class="muted">加工方式</span><span>{{executionLifecycle().execution_mode?valueText('execution_mode',executionLifecycle().execution_mode):'当前可见范围未确定'}}</span></div>
  <div class="table-scroll"><table><thead><tr><th>执行阶段</th><th>状态</th><th>已知事实</th><th>阻塞或说明</th><th>展开能力</th></tr></thead><tbody>
    <tr v-for="stage in executionLifecycle().stages||[]" :key="stage.key">
      <td>{{stage.name}}</td><td>{{statusName(stage.state)}}</td><td>{{stageFactText(stage.facts)}}</td>
      <td>{{(stage.blockers||[]).join('；')||(stage.conditional?'按加工方式或业务事实适用':'—')}}</td>
      <td>{{stage.query_tool?capabilityName(stage.query_tool):'—'}}</td>
    </tr>
  </tbody></table></div>
  <div v-if="executionLifecycle().recommended_next_steps?.length" class="business-facts">
    <h4>建议下一步</h4>
    <div v-for="item in executionLifecycle().recommended_next_steps" :key="item.kind+item.stage" class="fact-line">
      <span class="muted">当前焦点</span><span>{{capabilityName(item.tool)}} · {{item.reason}}</span>
    </div>
  </div>
  <p v-if="executionLifecycle().access_gaps?.length" class="muted small">未读取：{{executionLifecycle().access_gaps.join('、')}}</p>
  <p v-if="executionLifecycle().guardrails?.length" class="muted small">{{executionLifecycle().guardrails.join(' ')}}</p>
</section><section v-if="tasks().length" class="surface">
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
