<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { Plus, Trash2, GitBranch, Play, UserPlus, X, Search } from 'lucide-vue-next'
import { api, post } from '../api'
import WorkflowCategoryPanel from './WorkflowCategoryPanel.vue'
import MaterialTemplatePanel from './MaterialTemplatePanel.vue'
import WorkflowCalendarPanel from './WorkflowCalendarPanel.vue'
import WorkflowCanvas from './WorkflowCanvas.vue'
import RuleEditor from './RuleEditor.vue'
import {workflowUi} from '@domain-pack/uiPolicy'
import {permissionName} from '@domain-pack/uiText'
const emit = defineEmits<{error:[message:string]}>()
const templates=ref<any[]>([]), users=ref<any[]>([])
const editing=ref(false), busy=ref(false), name=ref(''), key=ref('')
const nodes=ref<any[]>([]), simulation=ref<any>(null), materialContract=ref<any>(null)
const selectedNodeIndex=ref(0)
const canvasPanel=ref<'simulation'|'add-sign'|''>(''),addSignQuery=ref('')
const editId=ref(''), editHash=ref(''), selected=ref<any>(null), historyKey=ref(''), history=ref<any[]>([]), historyNext=ref<number|null>(null), notice=ref('')
const editorPanel=ref<HTMLElement|null>(null)
const incidents=ref<any[]>([]),incidentsOpen=ref(false),incidentReasons=ref<Record<string,string>>({})
const flowCategories=ref<any[]>([]),categoryId=ref(''),categoryFilter=ref(''),templateQuery=ref(''),legacyCopy=ref(false),categoryFilterOpen=ref(false),metaSelectOpen=ref<'category'|'material'|''>('')
const categoryName=(id:string)=>flowCategories.value.find(c=>c.id===id)?.name||'待整理'
const categoryFilterLabel=computed(()=>categoryFilter.value?categoryName(categoryFilter.value):'全部类别')
const categoryEditLabel=computed(()=>categoryId.value?categoryName(categoryId.value):'请选择类别')
const materialEditLabel=computed(()=>materialTemplateId.value?(materialTemplates.value.find(t=>t.id===materialTemplateId.value)?.name||'资料模板不可用'):'暂不绑定')
function pickCategoryFilter(value:string){categoryFilter.value=value;categoryFilterOpen.value=false}
function pickEditCategory(value:string){categoryId.value=value;metaSelectOpen.value=''}
function pickEditMaterial(value:string){materialTemplateId.value=value;metaSelectOpen.value='';selectMaterial()}
async function refreshCategories(){try{flowCategories.value=await api('/workflow-categories')}catch(e:any){emit('error',e.message)}}
const assignmentGroups=ref<any[]>([])
const assignmentDomain=ref<any>({roles:[],scopes:[],responsibility_dimensions:[]}),assignmentPreview=ref<any>(null),previewScopeValues=ref<Record<string,string>>({}),previewing=ref(false)
const materialTemplates=ref<any[]>([]),materialTemplateId=ref('')
const workflowCalendars=ref<any[]>([])
const publishedCalendars=computed(()=>workflowCalendars.value.filter(item=>item.status==='PUBLISHED'))
async function refreshMaterials(){const all:any[]=[];for(let offset=0;;offset+=100){const page=await api(`/material-templates?offset=${offset}`);all.push(...page);if(page.length<100)break}materialTemplates.value=all}
async function refreshCalendars(){const all:any[]=[];for(let offset=0;;offset+=100){const page=await api(`/workflow-calendars?offset=${offset}&limit=100`);all.push(...page);if(page.length<100)break}workflowCalendars.value=all}
function selectMaterial(){const t=materialTemplates.value.find(t=>t.id===materialTemplateId.value);materialContract.value=t?JSON.parse(JSON.stringify(t.contract)):null;simulation.value=null}
const groups=computed(()=>{const query=templateQuery.value.trim().toLocaleLowerCase();return Object.values(templates.value.filter(t=>(!categoryFilter.value||t.category_id===categoryFilter.value)&&(!query||[t.name,t.process_key,categoryName(t.category_id),...(t.config?.nodes||[]).map((node:any)=>node.name)].some(value=>String(value||'').toLocaleLowerCase().includes(query)))).reduce((all:Record<string,any>,t:any)=>{if(!all[t.process_key])all[t.process_key]=t;return all},{}))})
const selectedNode=computed(()=>nodes.value[selectedNodeIndex.value]||null)
const addSignCandidates=computed(()=>{
  const node=selectedNode.value,query=addSignQuery.value.trim().toLocaleLowerCase()
  if(!node?.add_sign_policy||!query)return[]
  const selected=new Set(node.add_sign_policy.users||[])
  return users.value.filter(user=>user.active&&!selected.has(user.id)&&[user.display_name,user.username,user.department].some(value=>String(value||'').toLocaleLowerCase().includes(query))).slice(0,6)
})
const sample=ref<Record<string,any>>(Object.fromEntries(workflowUi.simulationFields.map((field:any)=>[field.key,field.value])))
type SimulationInput={id:string;key?:string;table?:string;kind:'root'|'row'|'sum'|'count';label:string;type:string;options?:Record<string,string>;emptyLabel?:string;currency?:string;currencyField?:string}
const simulationInputs=computed<SimulationInput[]>(()=>{
  const inputs=new Map<string,SimulationInput>()
  const tableDefinition=(key:string)=>materialContract.value?.tables?.find((table:any)=>table.key===key)
  const add=(input:SimulationInput)=>{if(!inputs.has(input.id))inputs.set(input.id,input)}
  const visit=(rule:any,tableKey?:string)=>{
    if(!rule||typeof rule!=='object')return
    if(Array.isArray(rule.all)||Array.isArray(rule.any)){for(const child of (rule.all||rule.any))visit(child,tableKey);return}
    if(rule.table){
      const table=tableDefinition(rule.table),tableLabel=table?.label||'明细表'
      if(rule.aggregate==='COUNT'){add({id:`count:${rule.table}`,table:rule.table,kind:'count',label:`${tableLabel} / 明细数量`,type:'decimal'});if(rule.filter)visit(rule.filter,rule.table);return}
      if(rule.aggregate){
        const field=table?.fields?.find((item:any)=>item.key===rule.field)||{key:rule.field,label:rule.field,type:'decimal'}
        add({id:`sum:${rule.table}:${field.key}`,key:field.key,table:rule.table,kind:'sum',label:`${tableLabel} / ${field.label}${String(field.label).endsWith('合计')?'':'合计'}`,type:field.type,currency:rule.currency,currencyField:field.currency_field})
        if(rule.filter)visit(rule.filter,rule.table)
        return
      }
      visit(rule.condition,rule.table);return
    }
    if(!rule.field)return
    if(materialContract.value){
      const table=tableKey?tableDefinition(tableKey):null
      const field=(table?.fields||materialContract.value.fields||[]).find((item:any)=>item.key===rule.field)||{key:rule.field,label:rule.field,type:'text'}
      const id=tableKey?`row:${tableKey}:${field.key}`:`field:${field.key}`
      add({id,key:field.key,table:tableKey,kind:tableKey?'row':'root',label:tableKey?`${table.label} / ${field.label}`:field.label,type:field.type,currency:rule.currency,currencyField:field.currency_field})
    }else{
      const field=workflowUi.simulationFields.find((item:any)=>item.key===rule.field)||{key:rule.field,label:rule.field,value:''}
      add({id:field.key,key:field.key,kind:'root',label:field.label.replace(/^测试/,''),type:workflowUi.numericFields.includes(field.key)?'decimal':'text',options:field.options,emptyLabel:field.emptyLabel})
    }
  }
  for(const node of nodes.value){for(const route of node.routes||[])visit(route.condition);for(const reject of node.reject_rules||[])visit(reject.condition)}
  return [...inputs.values()]
})
function simulationSnapshot(){
  if(!materialContract.value)return Object.fromEntries(simulationInputs.value.map(input=>[input.key,sample.value[input.id]]).filter(([,value])=>value!==''&&value!==undefined))
  const material:any={fields:{},tables:{}},rowCounts=new Map<string,number>()
  for(const input of simulationInputs.value){
    const value=sample.value[input.id]
    if(!input.table||value===''||value===undefined)continue
    if(input.kind==='count')rowCounts.set(input.table,Math.max(0,Math.min(5000,Number.parseInt(String(value),10)||0)))
    else if(!rowCounts.has(input.table))rowCounts.set(input.table,1)
  }
  for(const [table,count] of rowCounts)material.tables[table]=Array.from({length:count},(_,index)=>({id:`simulation-${index+1}`,values:{}}))
  for(const input of simulationInputs.value){
    const value=sample.value[input.id]
    if(value===''||value===undefined)continue
    if(input.kind==='root'&&input.key){material.fields[input.key]=value;if(input.currencyField)material.fields[input.currencyField]=input.currency||'CNY';continue}
    if(!input.table||!input.key||input.kind==='count')continue
    const rows=material.tables[input.table]||(material.tables[input.table]=[{id:'simulation-1',values:{}}]),fieldKey=input.key
    rows.forEach((row:any,index:number)=>{row.values[fieldKey]=input.kind==='sum'&&index>0?'0':value;if(input.currencyField)row.values[input.currencyField]=input.currency||'CNY'})
  }
  return {material_data:material}
}
function newCondition(){
  const header=materialContract.value?.fields?.[0]
  if(header)return {field:header.key,op:['decimal','money','date'].includes(header.type)?'gt':'eq',value:''}
  const table=materialContract.value?.tables?.[0],field=table?.fields?.[0]
  if(table&&field)return {table:table.key,quantifier:'ANY',condition:{field:field.key,op:['decimal','money','date'].includes(field.type)?'gt':'eq',value:''}}
  return {field:workflowUi.defaultField,op:'gt',value:''}
}
const outcomes:Record<string,string>={ROUTE_VALID:'路径校验通过',MUST_REJECT:'命中必须驳回条件',RULE_DATA_MISSING:'驳回判断资料不足',ROUTE_DATA_MISSING:'分支判断资料不足',ROUTE_AMBIGUOUS:'同时命中多个分支'}
const freshNode=(i:number):any=>({key:'review_'+i,name:'审批节点 '+i,users:[],mode:'ALL',reject_rules:[],allow_transfer:false,allow_proxy:false,return_policy:{targets:['applicant']}})
function setNodeMode(node:any,mode:string){node.mode=mode;if(mode==='QUORUM')node.required_approvals=Math.max(1,Number(node.required_approvals)||2);else delete node.required_approvals;if(mode==='CLAIM'){node.agent_auto_approval=false;delete node.agent_auto_policy}}
function toggleAddSign(n:any,enabled:boolean){if(enabled)n.add_sign_policy={timings:['PRE','POST'],users:[]};else delete n.add_sign_policy}
function selectCanvasNode(index:number){selectedNodeIndex.value=index;canvasPanel.value='add-sign';addSignQuery.value='';assignmentPreview.value=null}
function toggleCanvasPanel(panel:'simulation'|'add-sign'){canvasPanel.value=canvasPanel.value===panel?'':panel;if(panel==='add-sign')addSignQuery.value=''}
function setAddSignTiming(n:any,timing:'PRE'|'POST'){
  const timings=n.add_sign_policy?.timings
  if(!timings)return
  if(timings.includes(timing)){if(timings.length===1)return emit('error','前加签和后加签至少保留一种');n.add_sign_policy.timings=timings.filter((item:string)=>item!==timing)}
  else n.add_sign_policy.timings=[...timings,timing]
}
function addAddSignUser(n:any,userId:string){if(!n.add_sign_policy||n.add_sign_policy.users.includes(userId))return;n.add_sign_policy.users.push(userId);addSignQuery.value=''}
function removeAddSignUser(n:any,userId:string){if(n.add_sign_policy)n.add_sign_policy.users=n.add_sign_policy.users.filter((id:string)=>id!==userId)}
function toggleDynamicAssignment(n:any,enabled:boolean){if(enabled){n.users=[];n.assignment={roles:[],departments:[],department_heads_only:false,domain_roles:[],business_permissions:[],responsibility_scope:[]}}else{delete n.assignment;n.users=[]}assignmentPreview.value=null}
function toggleAssignmentValue(n:any,key:'roles'|'departments'|'domain_roles'|'business_permissions'|'responsibility_scope',value:string,enabled:boolean){if(!n.assignment)return;const current:string[]=n.assignment[key]||[];n.assignment[key]=enabled?[...new Set([...current,value])]:current.filter(item=>item!==value);if(key==='departments'&&!n.assignment.departments.length)n.assignment.department_heads_only=false;if(key==='business_permissions'&&!n.assignment.business_permissions.length)n.assignment.responsibility_scope=[];assignmentPreview.value=null}
function addBusinessPermission(n:any,event:Event){const select=event.target as HTMLSelectElement,key=select.value;if(key){toggleAssignmentValue(n,'business_permissions',key,true);if(!n.assignment.responsibility_scope.length)for(const dimension of assignmentDomain.value.responsibility_dimensions?.filter((item:any)=>item.default)||[])toggleAssignmentValue(n,'responsibility_scope',dimension.key,true)}select.value=''}
function assignmentGapReason(reason:any){return `${reason.permission?permissionName(reason.permission)+'：':''}${reason.message}`}
function assignmentDimension(key:string){return assignmentDomain.value.responsibility_dimensions?.find((item:any)=>item.key===key)||{key,name:key,values:key===assignmentDomain.value.context_key?assignmentDomain.value.scopes:[]}}
function assignmentDimensionOptions(key:string){return assignmentDimension(key).values||[]}
function assignmentOptionLabel(option:any){return option.code&&option.name&&option.code!==option.name?`${option.code} · ${option.name}`:option.name||option.code||option.id}
function assignmentPreviewDimensions(n:any){const keys=[...(n.assignment?.responsibility_scope||[])];if(n.assignment?.domain_roles?.length&&assignmentDomain.value.context_key&&!keys.includes(assignmentDomain.value.context_key))keys.unshift(assignmentDomain.value.context_key);return [...new Set(keys)] as string[]}
function initializePreviewContext(){const next={...previewScopeValues.value};for(const dimension of assignmentDomain.value.responsibility_dimensions||[]){const options=dimension.values||[];if(!options.some((option:any)=>option.id===next[dimension.key]))next[dimension.key]=options[0]?.id||''}previewScopeValues.value=next}
async function previewAssignment(){if(!selectedNode.value?.assignment)return;const assignment=selectedNode.value.assignment,context:Record<string,string>={};for(const key of assignmentPreviewDimensions(selectedNode.value)){const value=previewScopeValues.value[key];if(!value)return emit('error',`请选择要预览的${assignmentDimension(key).name||'业务范围'}`);context[key]=value}previewing.value=true;try{assignmentPreview.value=await post('/workflows/assignment-preview',{assignment,context})}catch(e:any){emit('error',e.message);assignmentPreview.value=null}finally{previewing.value=false}}
function toggleSla(n:any,enabled:boolean){if(enabled)n.sla={due_hours:24,remind_before_hours:2};else delete n.sla}
function toggleSlaRecipient(n:any,key:'cc_user_ids'|'escalation_user_ids',userId:string,enabled:boolean){
  if(!n.sla)return
  const current:string[]=Array.isArray(n.sla[key])?n.sla[key]:[]
  const next=enabled?[...new Set([...current,userId])]:current.filter(id=>id!==userId)
  if(next.length)n.sla[key]=next
  else delete n.sla[key]
}
function normalizeReturnPolicy(n:any){if(!n.return_policy)n.return_policy={targets:['applicant']};if(n.assignment)n.assignment={roles:n.assignment.roles||[],departments:n.assignment.departments||[],department_heads_only:!!n.assignment.department_heads_only,domain_roles:n.assignment.domain_roles||[],business_permissions:n.assignment.business_permissions||[],responsibility_scope:n.assignment.responsibility_scope||[]};return n}
async function refreshIncidents(){incidents.value=await api('/workflow-incidents')}
async function retryIncident(item:any){const reason=(incidentReasons.value[item.id]||'').trim();if(!reason)return emit('error','请填写本次恢复原因');busy.value=true;try{const result=await post(`/workflow-incidents/${item.id}/retry`,{expected_version:item.version,reason});notice.value=result.incident?'重试完成，但阻塞原因仍未消除':item.incident==='TIMER_FAILED'?`已重新排队 ${result.requeued_timers} 个定时事件`:'流程节点已恢复并重新生成待办';incidentReasons.value[item.id]='';await refreshIncidents()}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
function displayTime(value:string){return new Date(value).toLocaleString('zh-CN',{hour12:false})}
async function load(){const all:any[]=[];for(let offset=0;;offset+=100){const page=await api(`/workflows?offset=${offset}&limit=100`);all.push(...page);if(page.length<100)break}templates.value=all;const people=await api('/workflows/assignment-catalog');users.value=people.users;assignmentGroups.value=people.groups;assignmentDomain.value=people.domain||{roles:[],scopes:[],responsibility_dimensions:[]};initializePreviewContext();await refreshCategories();await refreshMaterials();await refreshCalendars();await refreshIncidents()}
onMounted(async()=>{try{await load()}catch(e:any){emit('error',e.message)}})
watch([nodes,sample,categoryId],()=>{simulation.value=null},{deep:true})
async function reveal(panel:{value:HTMLElement|null}){await nextTick();panel.value?.scrollIntoView({behavior:'smooth',block:'start'})}
function closeWorkflowDialog(){selected.value=null;historyKey.value=''}
async function create(){editId.value='';editHash.value='';selected.value=null;historyKey.value='';name.value='';key.value='flow_'+crypto.randomUUID().replaceAll('-','');categoryId.value='';legacyCopy.value=false;materialContract.value=null;materialTemplateId.value='';nodes.value=[freshNode(1)];selectedNodeIndex.value=0;canvasPanel.value='';editing.value=true;await reveal(editorPanel)}
async function view(t:any,fromHistory=false){try{if(!fromHistory)historyKey.value='';selected.value=await api(`/workflows/${t.id}`);editing.value=false}catch(e:any){emit('error',e.message)}}
async function openHistory(t:any,more=false){try{const r=await api(`/workflows/history/${encodeURIComponent(t.process_key)}${more?'?before_version='+historyNext.value:''}`);selected.value=null;historyKey.value=t.process_key;history.value=more?[...history.value,...r.items]:r.items;historyNext.value=r.next_before}catch(e:any){emit('error',e.message)}}
async function copy(t:any,edit=false){try{const d=await api(`/workflows/${t.id}`);editId.value=edit?d.id:'';editHash.value=d.edit_hash;categoryId.value=d.category_id||'';legacyCopy.value=d.business_type!=='generic';name.value=d.name;key.value=d.process_key;materialTemplateId.value=d.material_template_id||'';nodes.value=JSON.parse(JSON.stringify(d.config.nodes)).map(normalizeReturnPolicy);materialContract.value=d.config.material_contract?JSON.parse(JSON.stringify(d.config.material_contract)):null;selected.value=null;historyKey.value='';selectedNodeIndex.value=0;canvasPanel.value='';editing.value=true;await reveal(editorPanel)}catch(e:any){emit('error',e.message)}}
const configuration=()=>({business_type:'generic',nodes:nodes.value,...(materialContract.value?{material_contract:materialContract.value}:{})})
async function save(){if(!categoryId.value)return emit('error','请选择流程类别');busy.value=true;try{const r=editId.value?await api(`/workflows/${editId.value}`,{method:'PUT',body:JSON.stringify({name:name.value,config:configuration(),category_id:categoryId.value,material_template_id:materialTemplateId.value||null,expected_hash:editHash.value})}):await post('/workflows',{process_key:key.value,name:name.value,config:configuration(),category_id:categoryId.value,material_template_id:materialTemplateId.value||null});notice.value=`第 ${r.version} 版草稿已保存，尚未发布`;editing.value=false;await load();await openHistory({process_key:key.value});await view(r)}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
async function publish(t:any){busy.value=true;try{await post(`/workflows/${t.id}/publish`);notice.value=`第 ${t.version} 版已发布，已有审批实例继续使用原版本`;await load();if(historyKey.value===t.process_key)await openHistory(t);if(selected.value?.id===t.id)await view(t)}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
function addNode(){let i=nodes.value.length+1;while(nodes.value.some(n=>n.key==='review_'+i))i++;nodes.value.push(freshNode(i));selectedNodeIndex.value=nodes.value.length-1}
function addPersonNode(userId:string){
 let i=nodes.value.length+1
 while(nodes.value.some(n=>n.key==='review_'+i))i++
 const user=users.value.find(user=>user.id===userId),node:any=freshNode(i)
 node.users=[userId]
 if(user?.display_name)node.name=`${user.display_name}审批`
 nodes.value.push(node)
 selectedNodeIndex.value=nodes.value.length-1
}
function removeNode(i:number){const target=nodes.value[i].key;if(nodes.value.some((n,j)=>j!==i&&(n.default_target===target||n.routes?.some((r:any)=>r.target===target)))){emit('error','请先修改指向该节点的分支，再删除节点');return}if(nodes.value.some((n,j)=>j>i&&n.return_policy?.targets?.includes(target))){emit('error','请先从后续节点的退回目标中移除此节点');return}nodes.value.splice(i,1);selectedNodeIndex.value=Math.max(0,Math.min(i,nodes.value.length-1))}
function assignPerson(index:number,userId:string){const node=nodes.value[index];if(!node)return;if(node.assignment){emit('error','该节点正在按角色或部门选人，请先在节点属性中切换为指定人员');return}if(!node.users.includes(userId))node.users.push(userId);selectedNodeIndex.value=index}
function unassignPerson(index:number,userId:string){const node=nodes.value[index];if(node)node.users=node.users.filter((id:string)=>id!==userId)}
function connectNodes(sourceIndex:number,targetKey:string){const node=nodes.value[sourceIndex],allowed=targets(sourceIndex);if(!node||!allowed.some(target=>target.key===targetKey)){emit('error','线路只能连接到当前节点之后的节点或结束');return}const sequential=nodes.value[sourceIndex+1]?.key||'end';if((!node.routes&&targetKey===sequential)||node.default_target===targetKey||node.routes?.some((route:any)=>route.target===targetKey)){emit('error','这条线路已经存在');return}if(!node.routes){node.routes=[{condition:newCondition(),target:targetKey}];node.default_target=sequential}else node.routes.push({condition:newCondition(),target:targetKey});selectedNodeIndex.value=sourceIndex}
function targets(i:number){return [...nodes.value.slice(i+1).map(n=>({key:n.key,name:n.name})),{key:'end',name:'审批结束'}]}
async function simulate(){busy.value=true;try{simulation.value=await post('/workflows/simulate',{config:configuration(),snapshot:simulationSnapshot()})}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
</script>
<template>
  <div class="section-heading"><div><h2>审批流程配置</h2><p class="muted">配置人员、条件、办理时限和路线，保存版本并重复使用。</p></div><div class="actions"><button type="button" class="workflow-incident-button" @click="incidentsOpen=!incidentsOpen">流程事件{{incidents.length?' · '+incidents.length:''}}</button><button class="workflow-create-button" @click="create"><Plus :size="16"/>新建模板</button></div></div>
  <div class="workflow-topbar">
    <MaterialTemplatePanel @changed="refreshMaterials" @error="emit('error',$event)"/>
    <WorkflowCategoryPanel :categories="flowCategories" @changed="refreshCategories" @error="emit('error',$event)"/>
    <WorkflowCalendarPanel :calendars="workflowCalendars" @changed="refreshCalendars" @error="emit('error',$event)"/>
    <div class="workflow-template-search"><Search :size="15"/><input v-model="templateQuery" type="search" aria-label="搜索审批流程模板" placeholder="搜索模板名称或流程节点"/></div>
    <div class="workflow-category-filter"><div class="workflow-filter-select" :class="{open:categoryFilterOpen}"><button type="button" class="workflow-filter-select-button" aria-label="按类别查看" @click="categoryFilterOpen=!categoryFilterOpen"><span>{{categoryFilterLabel}}</span><i aria-hidden="true"></i></button><div v-if="categoryFilterOpen" class="workflow-filter-select-menu"><button type="button" :class="{active:!categoryFilter}" @click="pickCategoryFilter('')">全部类别</button><button v-for="c in flowCategories" :key="c.id" type="button" :class="{active:categoryFilter===c.id}" @click="pickCategoryFilter(c.id)">{{c.name}}</button></div></div></div>
  </div>
  <section v-if="incidentsOpen" class="surface form-stack workflow-incident-center" aria-label="流程事件中心">
    <div class="section-heading"><div><h3>流程事件中心</h3><p class="muted">超时只会提醒，不会自动同意；人员配置修复后可重新解析当前节点。</p></div><button type="button" :disabled="busy" @click="refreshIncidents">刷新</button></div>
    <p v-if="!incidents.length" class="muted">当前没有阻塞或超时的运行中审批。</p>
    <article v-for="item in incidents" :key="item.id" class="workflow-incident-row">
      <div><strong>{{item.definition.name}} · {{item.node?.name||'流程结束节点'}}</strong><p><span v-if="item.incident==='ASSIGNMENT_BLOCKED'">审批人员解析阻塞</span><span v-else-if="item.incident==='TIMER_FAILED'">定时事件处理失败</span><span v-else-if="item.incident">{{item.incident}}</span><span v-if="item.overdue">{{item.incident?'；':''}}办理已超时</span></p><small v-if="item.deadline?.due_at" class="muted">应办时间 {{displayTime(item.deadline.due_at)}} · 第 {{item.definition.version}} 版</small><div v-if="item.failed_timers?.length" class="workflow-timer-failures"><span v-for="timer in item.failed_timers" :key="timer.id">{{timer.timer_key==='DUE'?'到期事件':'提醒事件'}} · 已失败 {{timer.attempts}} 次 · {{timer.last_error}}</span></div><div v-if="item.escalations?.length" class="workflow-escalation-list"><span v-for="task in item.escalations" :key="task.id">跟进人 {{task.user.display_name}} · {{task.status==='OPEN'?'待跟进':'已关闭'}}</span></div></div>
      <div v-if="item.retryable" class="workflow-incident-retry"><input v-model="incidentReasons[item.id]" maxlength="500" placeholder="填写修复内容和恢复原因" aria-label="恢复原因"/><button type="button" :disabled="busy" @click="retryIncident(item)">{{item.incident==='TIMER_FAILED'?'重新排队事件':'重新解析人员'}}</button></div>
      <p v-else class="muted">该事件不改变审批决定；请先处理对应运行环境或等待审批人办理。</p>
    </article>
  </section>
  <p v-if="notice" role="status">{{notice}}</p>
  <div v-if="selected||historyKey" class="modal-shade workflow-dialog-shade" @click.self="closeWorkflowDialog">
    <section v-if="selected" class="modal workflow-viewer workflow-dialog workflow-viewer-dialog" role="dialog" aria-modal="true" aria-label="流程详情">
      <div class="section-heading workflow-viewer-head"><div><h3>{{selected.name}}</h3><div class="workflow-viewer-meta"><span>第 {{selected.version}} 版</span><span>{{selected.status==='PUBLISHED'?'已发布':'草稿'}}</span><span>{{categoryName(selected.category_id)}}</span><span>{{selected.instance_count}} 个实例</span></div></div><div class="workflow-dialog-head-actions"><button v-if="historyKey" @click="selected=null">返回版本历史</button><button @click="closeWorkflowDialog">关闭</button></div></div>
      <WorkflowCanvas :nodes="selected.config.nodes" :users="users" :groups="assignmentGroups" :selected-index="-1" :storage-key="selected.process_key" read-only/>
      <div class="actions workflow-viewer-actions"><button @click="copy(selected,selected.status==='DRAFT')">{{selected.status==='DRAFT'?'修改当前草稿':'修改并另存新版本'}}</button><button v-if="selected.status==='DRAFT'" class="primary" :disabled="busy" @click="publish(selected)">校验并发布</button></div>
    </section>
    <section v-else class="modal form-stack workflow-history-panel workflow-dialog workflow-history-dialog" role="dialog" aria-modal="true" aria-label="版本历史"><div class="section-heading workflow-history-head"><div><h3>{{history[0]?.name||'审批流程'}} · 版本历史</h3><small class="muted">共 {{history.length}} 个版本</small></div><button @click="closeWorkflowDialog">关闭</button></div><div v-for="t in history" :key="t.id" class="section-heading workflow-history-row"><span>第 {{t.version}} 版 · {{t.status==='PUBLISHED'?'已发布':'草稿'}} · {{t.name}}</span><div class="actions"><button @click="view(t,true)">查看第 {{t.version}} 版</button><button @click="copy(t,t.status==='DRAFT')">{{t.status==='DRAFT'?'修改草稿':'修改为新版本'}}</button></div></div><button v-if="historyNext" class="workflow-history-more" @click="openHistory({process_key:historyKey},true)">加载更早版本</button><p class="muted workflow-history-note">新版本保存后出现在这里；草稿需发布后才可发起审批。旧实例继续使用发起时的版本。</p></section>
  </div>
  <form v-if="editing" ref="editorPanel" class="surface form-stack workflow-editor-card" @submit.prevent="save">
    <h3>{{editId?'修改当前草稿':'新版本草稿编辑'}}</h3>
    <div class="workflow-meta-grid">
      <label>流程类别<div class="workflow-filter-select workflow-meta-select" :class="{open:metaSelectOpen==='category'}"><button type="button" class="workflow-filter-select-button" :class="{placeholder:!categoryId}" @click="metaSelectOpen=metaSelectOpen==='category'?'':'category'"><span>{{categoryEditLabel}}</span><i aria-hidden="true"></i></button><div v-if="metaSelectOpen==='category'" class="workflow-filter-select-menu"><button v-for="c in flowCategories.filter(x=>x.active)" :key="c.id" type="button" :class="{active:categoryId===c.id}" @click="pickEditCategory(c.id)">{{c.name}}</button></div></div></label>
      <label>模板名称<input v-model="name" required maxlength="150"/></label>
      <label>绑定资料模板版本<div class="workflow-filter-select workflow-meta-select" :class="{open:metaSelectOpen==='material'}"><button type="button" class="workflow-filter-select-button" @click="metaSelectOpen=metaSelectOpen==='material'?'':'material'"><span>{{materialEditLabel}}</span><i aria-hidden="true"></i></button><div v-if="metaSelectOpen==='material'" class="workflow-filter-select-menu"><button type="button" :class="{active:!materialTemplateId}" @click="pickEditMaterial('')">暂不绑定</button><button v-for="t in materialTemplates.filter(x=>x.status==='PUBLISHED')" :key="t.id" type="button" :class="{active:materialTemplateId===t.id}" @click="pickEditMaterial(t.id)">{{t.name}} · 第 {{t.version}} 版</button></div></div></label>
    </div>
    <p v-if="legacyCopy" class="muted">此次保存采用通用模板配置。原已发布版本及已有实例保持原规则。</p>
    <p v-if="materialTemplateId" class="muted">已绑定明确的 Excel 字段版本。条件分支可直接选择表头变量、任一明细、数值合计或明细数量；正式发起时需上传并确认对应资料。</p>
    <WorkflowCanvas :nodes="nodes" :users="users" :groups="assignmentGroups" :selected-index="selectedNodeIndex" :storage-key="key" :simulation="simulation" @select="selectCanvasNode" @add="addNode" @add-person="addPersonNode" @connect="connectNodes" @assign="assignPerson" @unassign="unassignPerson" @error="emit('error',$event)">
      <template #toolbar-actions><button type="button" :class="{active:canvasPanel==='simulation'}" @click="toggleCanvasPanel('simulation')"><Play :size="14"/>画布模拟</button></template>
      <template #canvas-overlay>
        <aside v-if="canvasPanel==='simulation'" class="workflow-canvas-overlay" aria-label="画布模拟设置">
          <div class="workflow-overlay-head"><div><strong>画布模拟</strong><small>{{simulationInputs.length?'只填条件用到的值':'无需测试数据'}}</small></div><button type="button" aria-label="关闭画布模拟" @click="canvasPanel=''" ><X :size="14"/></button></div>
          <div v-if="simulationInputs.length" class="workflow-overlay-fields"><label v-for="field in simulationInputs" :key="field.id"><span>{{field.label}}</span><select v-if="field.type==='boolean'" v-model="sample[field.id]"><option value="">未提供</option><option :value="true">是</option><option :value="false">否</option></select><select v-else-if="field.options" v-model="sample[field.id]"><option value="">{{field.emptyLabel||'未提供'}}</option><option v-for="(label,code) in field.options" :key="code" :value="code">{{label}}</option></select><input v-else v-model="sample[field.id]" :type="field.type==='date'?'date':'text'" :inputmode="['decimal','money'].includes(field.type)?'decimal':undefined" :placeholder="field.kind==='sum'?'填写合计值':field.kind==='count'?'填写行数':'填写测试值'"/></label></div>
          <p v-else class="muted small">点击后将直接显示完整流转路径。</p>
          <button type="button" class="primary workflow-overlay-primary" :disabled="busy" @click="simulate">{{busy?'正在模拟…':'开始模拟'}}</button>
          <div v-if="simulation" class="workflow-simulation-result" :class="{'is-ok':simulation.outcome==='ROUTE_VALID','is-blocked':simulation.outcome!=='ROUTE_VALID'}" role="status"><strong>{{outcomes[simulation.outcome]||'需要检查流程配置'}}</strong><span>{{simulation.path.map((step:any)=>step.name).join(' → ')}}{{simulation.outcome==='ROUTE_VALID'?' → 审批结束':''}}</span><small v-for="reason in [...(simulation.path.at(-1)?.rejection_reasons||[]),...(simulation.path.at(-1)?.missing_rules||[])]" :key="reason">{{reason}}</small></div>
        </aside>
        <aside v-else-if="canvasPanel==='add-sign'&&selectedNode" class="workflow-canvas-overlay" aria-label="节点加签设置">
          <div class="workflow-overlay-head"><div><strong>{{selectedNode.name}}</strong><small>节点快捷设置</small></div><button type="button" aria-label="关闭节点快捷设置" @click="canvasPanel=''" ><X :size="14"/></button></div>
          <div class="workflow-overlay-fields"><label><span>办理方式</span><select :value="selectedNode.mode" aria-label="审批办理方式" @change="setNodeMode(selectedNode,($event.target as HTMLSelectElement).value)"><option value="ALL">全员会签</option><option value="ANY">任一人或签</option><option value="QUORUM">比例会签 K/N</option><option value="CLAIM">候选领取</option></select></label><label v-if="selectedNode.mode==='QUORUM'"><span>通过票数 K</span><input v-model.number="selectedNode.required_approvals" type="number" min="1" max="50" required aria-label="比例会签通过票数"/></label></div>
          <p v-if="selectedNode.mode==='QUORUM'" class="muted small">进入节点时冻结有效席位总数 N；同意达到 K 票才通过，剩余席位已不可能达到 K 时才驳回。</p>
          <label class="workflow-overlay-switch"><input type="checkbox" :checked="!!selectedNode.assignment" @change="toggleDynamicAssignment(selectedNode,($event.target as HTMLInputElement).checked)"/><span><b>动态人员规则</b><small>按组织和业务包领域角色解析候选人</small></span></label>
          <template v-if="selectedNode.assignment">
            <div class="workflow-assignment-rules">
              <fieldset><legend>组织角色</legend><label v-for="group in assignmentGroups.filter((item:any)=>item.active&&item.kind==='ROLE')" :key="group.id" class="check-label"><input type="checkbox" :checked="selectedNode.assignment.roles.includes(group.id)" @change="toggleAssignmentValue(selectedNode,'roles',group.id,($event.target as HTMLInputElement).checked)"/>{{group.name}}<small>{{group.members.length}} 人</small></label><small v-if="!assignmentGroups.some((item:any)=>item.active&&item.kind==='ROLE')" class="muted">尚未维护组织角色</small></fieldset>
              <fieldset><legend>部门</legend><label v-for="group in assignmentGroups.filter((item:any)=>item.active&&item.kind==='DEPARTMENT')" :key="group.id" class="check-label"><input type="checkbox" :checked="selectedNode.assignment.departments.includes(group.id)" @change="toggleAssignmentValue(selectedNode,'departments',group.id,($event.target as HTMLInputElement).checked)"/>{{group.name}}<small>{{group.members.length}} 人</small></label><label v-if="selectedNode.assignment.departments.length" class="check-label"><input v-model="selectedNode.assignment.department_heads_only" type="checkbox"/>仅部门负责人</label></fieldset>
              <fieldset v-if="assignmentDomain.roles?.length"><legend>{{assignmentDomain.label}}</legend><label v-for="role in assignmentDomain.roles" :key="role.key" class="check-label"><input type="checkbox" :checked="selectedNode.assignment.domain_roles.includes(role.key)" @change="toggleAssignmentValue(selectedNode,'domain_roles',role.key,($event.target as HTMLInputElement).checked)"/>{{role.name}}</label></fieldset>
              <fieldset v-if="assignmentDomain.capabilities?.length"><legend>业务能力与责任域</legend><div class="workflow-assignment-capabilities"><button v-for="permission in selectedNode.assignment.business_permissions" :key="permission" type="button" :aria-label="'移除业务能力'+permissionName(permission)" @click="toggleAssignmentValue(selectedNode,'business_permissions',permission,false)">{{permissionName(permission)}} ×</button><select aria-label="添加业务能力" @change="addBusinessPermission(selectedNode,$event)"><option value="">添加业务能力…</option><option v-for="capability in assignmentDomain.capabilities.filter((item:any)=>!selectedNode.assignment.business_permissions.includes(item.key))" :key="capability.key" :value="capability.key">{{permissionName(capability.key)}}</option></select></div><label v-for="dimension in assignmentDomain.responsibility_dimensions" :key="dimension.key" class="check-label"><input type="checkbox" :disabled="!selectedNode.assignment.business_permissions.length" :checked="selectedNode.assignment.responsibility_scope.includes(dimension.key)" @change="toggleAssignmentValue(selectedNode,'responsibility_scope',dimension.key,($event.target as HTMLInputElement).checked)"/>{{dimension.name}}责任域</label><small v-if="selectedNode.assignment.business_permissions.length&&!selectedNode.assignment.responsibility_scope.length" class="assignment-warning">选择业务能力后必须选择责任域</small></fieldset>
            </div>
            <p class="muted small">同一类中的多个选择取并集；同时选择组织角色、部门和{{assignmentDomain.label||'领域角色'}}时取交集。人员规则不授予业务权限。</p>
            <div class="workflow-assignment-preview"><label v-for="dimensionKey in assignmentPreviewDimensions(selectedNode)" :key="dimensionKey">{{assignmentDimension(dimensionKey).name}}<select v-model="previewScopeValues[dimensionKey]" @change="assignmentPreview=null"><option v-if="!assignmentDimensionOptions(dimensionKey).length" value="">暂无可用范围</option><option v-for="option in assignmentDimensionOptions(dimensionKey)" :key="option.id" :value="option.id">{{assignmentOptionLabel(option)}}</option></select></label><button type="button" :disabled="previewing" @click="previewAssignment">{{previewing?'正在解析…':'预览最终账号'}}</button></div>
            <div v-if="assignmentPreview" class="workflow-assignment-preview-result" role="status"><strong>当前解析到 {{assignmentPreview.count}} 位有效账号</strong><span v-for="person in assignmentPreview.users" :key="person.id">{{person.display_name}} · {{person.department||person.username}}</span><div v-if="assignmentPreview.eligibility_gaps?.length" class="workflow-assignment-gaps"><b>资格缺口 {{assignmentPreview.eligibility_gaps.length}} 人</b><span v-for="gap in assignmentPreview.eligibility_gaps" :key="gap.user_id">{{gap.display_name}} · {{gap.reasons.map(assignmentGapReason).join('；')}}</span><small>这些账号仍属于已配置候选范围，但不会获得审批席位；请维护正式授权或调整人员规则。</small></div><small v-if="selectedNode.assignment.business_permissions.length">提交实际业务材料时仍会按全部明细、字段权限和当前席位复验。</small><small v-if="!assignmentPreview.count">当前规则为空；发布或运行时将阻断，不会按零人自动通过。</small></div>
          </template>
          <label class="workflow-overlay-switch"><input type="checkbox" :checked="!!selectedNode.add_sign_policy" @change="toggleAddSign(selectedNode,($event.target as HTMLInputElement).checked)"/><span><b>允许加签</b><small>审批人办理时可增加复核人</small></span></label>
          <template v-if="selectedNode.add_sign_policy">
            <div class="workflow-add-sign-timings"><button type="button" :class="{active:selectedNode.add_sign_policy.timings.includes('PRE')}" @click="setAddSignTiming(selectedNode,'PRE')">前加签<small>新增人员先审</small></button><button type="button" :class="{active:selectedNode.add_sign_policy.timings.includes('POST')}" @click="setAddSignTiming(selectedNode,'POST')">后加签<small>本人同意后再审</small></button></div>
            <div class="workflow-add-sign-pool"><span>可选加签人</span><div class="workflow-add-sign-chips"><button v-for="userId in selectedNode.add_sign_policy.users" :key="userId" type="button" :title="'移除 '+(users.find(user=>user.id===userId)?.display_name||'人员')" @click="removeAddSignUser(selectedNode,userId)">{{users.find(user=>user.id===userId)?.display_name||'人员不可用'}}<X :size="11"/></button><small v-if="!selectedNode.add_sign_policy.users.length">尚未选择候选人</small></div><div class="workflow-add-sign-search"><UserPlus :size="14"/><input v-model="addSignQuery" type="search" placeholder="搜索姓名、账号或部门"/></div><button v-for="user in addSignCandidates" :key="user.id" type="button" class="workflow-add-sign-result" @click="addAddSignUser(selectedNode,user.id)"><span>{{user.display_name}}</span><small>{{user.department||user.username}}</small></button></div>
          </template>
          <label class="workflow-overlay-switch"><input type="checkbox" :checked="!!selectedNode.sla" @change="toggleSla(selectedNode,($event.target as HTMLInputElement).checked)"/><span><b>设置办理时限</b><small>按冻结的时间与日历版本计时</small></span></label>
          <template v-if="selectedNode.sla">
            <div class="workflow-overlay-fields workflow-sla-fields"><label><span>办理时限（小时）</span><input v-model.number="selectedNode.sla.due_hours" type="number" min="1" max="8760" required/></label><label><span>提前提醒（小时）</span><input v-model.number="selectedNode.sla.remind_before_hours" type="number" min="0" :max="Math.max(0,selectedNode.sla.due_hours-1)" required/></label><label><span>计时日历</span><select v-model="selectedNode.sla.calendar_id"><option :value="undefined">连续自然小时</option><option v-for="calendar in publishedCalendars" :key="calendar.id" :value="calendar.id">{{calendar.name}} · 第 {{calendar.version}} 版</option></select></label></div>
            <div class="workflow-sla-recipients"><fieldset><legend>到期抄送</legend><label v-for="user in users.filter(item=>item.active)" :key="user.id" class="check-label"><input type="checkbox" :checked="selectedNode.sla.cc_user_ids?.includes(user.id)" @change="toggleSlaRecipient(selectedNode,'cc_user_ids',user.id,($event.target as HTMLInputElement).checked)"/>{{user.display_name}}</label><small>只增加到期通知，不增加审批票。</small></fieldset><fieldset><legend>升级跟进</legend><label v-for="user in users.filter(item=>item.active)" :key="user.id" class="check-label"><input type="checkbox" :checked="selectedNode.sla.escalation_user_ids?.includes(user.id)" @change="toggleSlaRecipient(selectedNode,'escalation_user_ids',user.id,($event.target as HTMLInputElement).checked)"/>{{user.display_name}}</label><small>超时后生成跟进任务，但不能代替审批人作决定。</small></fieldset></div>
          </template>
          <button v-if="nodes.length>1" type="button" class="subtle workflow-overlay-delete" @click="removeNode(selectedNodeIndex);canvasPanel=''" ><Trash2 :size="14"/>删除当前节点</button>
        </aside>
      </template>
    </WorkflowCanvas>
    <div class="actions workflow-editor-actions"><button type="button" @click="editing=false">取消</button><button class="primary" :disabled="busy">{{editId?'保存当前草稿':'保存为新版本草稿'}}</button></div>
  </form>
  <p v-if="!groups.length" class="workflow-template-empty">没有匹配的审批流程模板</p>
  <div v-else class="workflow-summary-grid">
    <article v-for="t in groups" :key="t.id" class="surface workflow-summary-card"><div class="section-heading"><h3><GitBranch :size="15"/>{{t.name}}</h3><span class="status">{{t.status==='PUBLISHED'?'已发布':'草稿'}} · 第 {{t.version}} 版</span></div><div class="flow-preview"><span>发起</span><template v-for="n in t.config?.nodes" :key="n.key"><span class="muted">→</span><span>{{n.name}}{{n.routes?'（条件路由）':''}}</span></template></div><p class="muted">类别：{{categoryName(t.category_id)}}；已发布版本保持不变</p><div class="actions"><button @click="view(t)">查看流程</button><button @click="openHistory(t)">版本历史</button><button @click="copy(t,t.status==='DRAFT')">{{t.status==='DRAFT'?'修改当前草稿':'修改并另存新版本'}}</button><button v-if="t.status==='DRAFT'" class="primary" :disabled="busy" @click="publish(t)">校验并发布</button></div></article>
  </div>
</template>
