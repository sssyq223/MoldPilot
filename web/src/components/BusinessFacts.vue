<script setup lang="ts">
import {fieldName,valueText} from '../uiText'
const props=defineProps<{value:any}>()
const hidden=new Set(['id','subject_id','plan_id','created_at','source_line_id','case_id','department_id','plan_subject_id','source_pause_subject_id','pause_id','task_id','closure_case_id','source_termination_subject_id','opened_by','closed_by','updated_by','analysis'])
const milestoneNames:Record<string,string>={design:'设计/工艺/出图',purchase:'采购',machining:'加工',assembly:'装配',trial:'试模/调试',delivery:'交付/验收'}
function tasks(){return props.value?.analysis?.tasks||[]}
function milestoneCoverage(){return props.value?.analysis?.milestone_coverage}
function statusName(value:string){return valueText('status',value)}
function milestoneList(items:any[]){return (items||[]).map(item=>item.name||item.key).filter(Boolean).join('、')||'—'}

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
</section><template v-for="(item,key) in value" :key="String(key)"><section v-if="!hidden.has(String(key))&&item!==null&&item!==undefined&&item!==''">
  <template v-if="Array.isArray(item)"><h4>{{fieldName(String(key))}}</h4><div v-for="(row,index) in item" :key="index" class="surface"><BusinessFacts v-if="row&&typeof row==='object'" :value="row"/><span v-else>{{valueText(String(key),row)}}</span></div><p v-if="!item.length" class="muted">暂无记录</p></template>
  <BusinessFacts v-else-if="item&&typeof item==='object'" :value="item"/>
  <div v-else class="fact-line"><span class="muted">{{fieldName(String(key))}}</span><span>{{valueText(String(key),item)}}</span></div>
</section></template></div></template>
