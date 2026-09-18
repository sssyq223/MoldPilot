<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Plus, Trash2, GitBranch, Play, UserPlus, X } from 'lucide-vue-next'
import { api, post } from '../api'
import RuleEditor from './RuleEditor.vue'
import WorkflowCategoryPanel from './WorkflowCategoryPanel.vue'
import MaterialTemplatePanel from './MaterialTemplatePanel.vue'
import WorkflowCanvas from './WorkflowCanvas.vue'
import {workflowUi} from '@domain-pack/uiPolicy'
const emit = defineEmits<{error:[message:string]}>()
const templates=ref<any[]>([]), users=ref<any[]>([])
const editing=ref(false), busy=ref(false), name=ref(''), key=ref('')
const nodes=ref<any[]>([]), simulation=ref<any>(null), materialContract=ref<any>(null)
const selectedNodeIndex=ref(0)
const canvasPanel=ref<'simulation'|'add-sign'|''>(''),addSignQuery=ref('')
const editId=ref(''), editHash=ref(''), selected=ref<any>(null), historyKey=ref(''), history=ref<any[]>([]), historyNext=ref<number|null>(null), notice=ref('')
const incidents=ref<any[]>([]),incidentsOpen=ref(false),incidentReasons=ref<Record<string,string>>({})
const flowCategories=ref<any[]>([]),categoryId=ref(''),categoryFilter=ref(''),legacyCopy=ref(false),categoryFilterOpen=ref(false)
const categoryName=(id:string)=>flowCategories.value.find(c=>c.id===id)?.name||'待整理'
const categoryFilterLabel=computed(()=>categoryFilter.value?categoryName(categoryFilter.value):'全部类别')
function pickCategoryFilter(value:string){categoryFilter.value=value;categoryFilterOpen.value=false}
async function refreshCategories(){try{flowCategories.value=await api('/workflow-categories')}catch(e:any){emit('error',e.message)}}
const assignmentGroups=ref<any[]>([])
const materialTemplates=ref<any[]>([]),materialTemplateId=ref('')
async function refreshMaterials(){const all:any[]=[];for(let offset=0;;offset+=100){const page=await api(`/material-templates?offset=${offset}`);all.push(...page);if(page.length<100)break}materialTemplates.value=all}
function selectMaterial(){const t=materialTemplates.value.find(t=>t.id===materialTemplateId.value);materialContract.value=t?JSON.parse(JSON.stringify(t.contract)):null;simulation.value=null}
function assignmentMode(n:any,dynamic:boolean){n.users=[];if(dynamic)n.assignment={roles:[],departments:[],department_heads_only:false};else delete n.assignment}
function assignmentNames(n:any){if(!n.assignment)return (n.users||[]).map((id:string)=>users.value.find(u=>u.id===id)?.display_name||'人员信息暂不可用').join('、');const names=(ids:string[])=>ids.map(id=>assignmentGroups.value.find(g=>g.id===id)?.name||'人员规则暂不可用').join('、');return [n.assignment.roles.length?'角色：'+names(n.assignment.roles):'',n.assignment.departments.length?(n.assignment.department_heads_only?'部门负责人：':'部门：')+names(n.assignment.departments):''].filter(Boolean).join('；同时满足')}
function candidateNames(n:any){if(!n.assignment)return assignmentNames(n);const r=n.assignment;let ids:string[]|null=null;for(const [field,head] of [['roles',false],['departments',r.department_heads_only]] as const){if(!r[field].length)continue;const selected=assignmentGroups.value.filter(g=>g.active&&r[field].includes(g.id));const list:string[]=selected.flatMap(g=>g.members.filter((m:any)=>!head||m.is_head).map((m:any)=>m.user_id));ids=ids===null?list:ids.filter(id=>list.includes(id))}return [...new Set(ids||[])].filter(id=>users.value.find(u=>u.id===id)?.active).map(id=>users.value.find(u=>u.id===id)?.display_name).join('、')||'暂无有效候选人员'}
const groups=computed(()=>Object.values(templates.value.filter(t=>!categoryFilter.value||t.category_id===categoryFilter.value).reduce((all:Record<string,any>,t:any)=>{if(!all[t.process_key])all[t.process_key]=t;return all},{})))
const selectedNodeEntry=computed(()=>nodes.value[selectedNodeIndex.value]?[{node:nodes.value[selectedNodeIndex.value],index:selectedNodeIndex.value}]:[])
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
function modeName(mode:string){return mode==='ALL'?'全部人员同意（会签）':mode==='ANY'?'任一人员同意（或签）':'候选人领取后办理'}
function setNodeMode(n:any,mode:string){n.mode=mode;if(mode==='CLAIM'){delete n.agent_auto_approval;delete n.agent_auto_policy}}
function toggleAgentAuto(n:any,enabled:boolean){n.agent_auto_approval=enabled;if(!enabled)delete n.agent_auto_policy}
function setAgentAutoPolicy(n:any,enabled:boolean){if(enabled){const condition:any=newCondition();if(condition.table)condition.condition.op='lte';else condition.op='lte';n.agent_auto_policy={condition}}else delete n.agent_auto_policy}
function toggleAddSign(n:any,enabled:boolean){if(enabled)n.add_sign_policy={timings:['PRE','POST'],users:[]};else delete n.add_sign_policy}
function selectCanvasNode(index:number){selectedNodeIndex.value=index;canvasPanel.value='add-sign';addSignQuery.value=''}
function toggleCanvasPanel(panel:'simulation'|'add-sign'){canvasPanel.value=canvasPanel.value===panel?'':panel;if(panel==='add-sign')addSignQuery.value=''}
function setAddSignTiming(n:any,timing:'PRE'|'POST'){
  const timings=n.add_sign_policy?.timings
  if(!timings)return
  if(timings.includes(timing)){if(timings.length===1)return emit('error','前加签和后加签至少保留一种');n.add_sign_policy.timings=timings.filter((item:string)=>item!==timing)}
  else n.add_sign_policy.timings=[...timings,timing]
}
function addAddSignUser(n:any,userId:string){if(!n.add_sign_policy||n.add_sign_policy.users.includes(userId))return;n.add_sign_policy.users.push(userId);addSignQuery.value=''}
function removeAddSignUser(n:any,userId:string){if(n.add_sign_policy)n.add_sign_policy.users=n.add_sign_policy.users.filter((id:string)=>id!==userId)}
function toggleSla(n:any,enabled:boolean){if(enabled)n.sla={due_hours:24,remind_before_hours:2};else delete n.sla}
function normalizeReturnPolicy(n:any){if(!n.return_policy)n.return_policy={targets:['applicant']};return n}
function returnTargets(i:number){return [{key:'applicant',name:'申请人修改'},...nodes.value.slice(0,i).map(n=>({key:n.key,name:n.name}))]}
function returnTargetNames(n:any,i:number){const options=Object.fromEntries(returnTargets(i).map(item=>[item.key,item.name]));return (n.return_policy?.targets||['applicant']).map((target:string)=>options[target]||'目标已失效').join('、')}
async function refreshIncidents(){incidents.value=await api('/workflow-incidents')}
async function retryIncident(item:any){const reason=(incidentReasons.value[item.id]||'').trim();if(!reason)return emit('error','请填写本次恢复原因');busy.value=true;try{const result=await post(`/workflow-incidents/${item.id}/retry`,{expected_version:item.version,reason});notice.value=result.incident?'重试完成，但阻塞原因仍未消除':'流程节点已恢复并重新生成待办';incidentReasons.value[item.id]='';await refreshIncidents()}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
function displayTime(value:string){return new Date(value).toLocaleString('zh-CN',{hour12:false})}
async function load(){const all:any[]=[];for(let offset=0;;offset+=100){const page=await api(`/workflows?offset=${offset}&limit=100`);all.push(...page);if(page.length<100)break}templates.value=all;const people=await api('/workflows/assignment-catalog');users.value=people.users;assignmentGroups.value=people.groups;await refreshCategories();await refreshMaterials();await refreshIncidents()}
onMounted(async()=>{try{await load()}catch(e:any){emit('error',e.message)}})
watch([nodes,sample,categoryId],()=>{simulation.value=null},{deep:true})
function create(){editId.value='';editHash.value='';selected.value=null;name.value='';key.value='flow_'+crypto.randomUUID().replaceAll('-','');categoryId.value='';legacyCopy.value=false;materialContract.value=null;materialTemplateId.value='';nodes.value=[freshNode(1)];selectedNodeIndex.value=0;canvasPanel.value='';editing.value=true}
async function view(t:any){try{selected.value=await api(`/workflows/${t.id}`);editing.value=false}catch(e:any){emit('error',e.message)}}
async function openHistory(t:any,more=false){try{const r=await api(`/workflows/history/${encodeURIComponent(t.process_key)}${more?'?before_version='+historyNext.value:''}`);historyKey.value=t.process_key;history.value=more?[...history.value,...r.items]:r.items;historyNext.value=r.next_before}catch(e:any){emit('error',e.message)}}
async function copy(t:any,edit=false){try{const d=await api(`/workflows/${t.id}`);editId.value=edit?d.id:'';editHash.value=d.edit_hash;categoryId.value=d.category_id||'';legacyCopy.value=d.business_type!=='generic';name.value=d.name;key.value=d.process_key;materialTemplateId.value=d.material_template_id||'';nodes.value=JSON.parse(JSON.stringify(d.config.nodes)).map(normalizeReturnPolicy);materialContract.value=d.config.material_contract?JSON.parse(JSON.stringify(d.config.material_contract)):null;selected.value=null;selectedNodeIndex.value=0;canvasPanel.value='';editing.value=true}catch(e:any){emit('error',e.message)}}
const configuration=()=>({business_type:'generic',nodes:nodes.value,...(materialContract.value?{material_contract:materialContract.value}:{})})
async function save(){busy.value=true;try{const r=editId.value?await api(`/workflows/${editId.value}`,{method:'PUT',body:JSON.stringify({name:name.value,config:configuration(),category_id:categoryId.value,material_template_id:materialTemplateId.value||null,expected_hash:editHash.value})}):await post('/workflows',{process_key:key.value,name:name.value,config:configuration(),category_id:categoryId.value,material_template_id:materialTemplateId.value||null});notice.value=`第 ${r.version} 版草稿已保存，尚未发布`;editing.value=false;await load();await openHistory({process_key:key.value});await view(r)}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
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
function routing(n:any,i:number,enabled:boolean){if(enabled){n.routes=[{condition:newCondition(),target:targets(i)[0].key}];n.default_target=targets(i)[0].key}else{delete n.routes;delete n.default_target}}
async function simulate(){busy.value=true;try{simulation.value=await post('/workflows/simulate',{config:configuration(),snapshot:simulationSnapshot()})}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
</script>
<template>
  <div class="section-heading"><div><h2>审批流程配置</h2><p class="muted">配置人员、条件、办理时限和路线，保存版本并重复使用。</p></div><div class="actions"><button type="button" @click="incidentsOpen=!incidentsOpen">流程事件{{incidents.length?' · '+incidents.length:''}}</button><button class="workflow-create-button" @click="create"><Plus :size="16"/>新建模板</button></div></div>
  <div class="workflow-topbar">
    <MaterialTemplatePanel @changed="refreshMaterials" @error="emit('error',$event)"/>
    <WorkflowCategoryPanel :categories="flowCategories" @changed="refreshCategories" @error="emit('error',$event)"/>
    <label class="workflow-category-filter"><span>按类别查看</span><div class="workflow-filter-select" :class="{open:categoryFilterOpen}"><button type="button" class="workflow-filter-select-button" @click="categoryFilterOpen=!categoryFilterOpen"><span>{{categoryFilterLabel}}</span><i aria-hidden="true"></i></button><div v-if="categoryFilterOpen" class="workflow-filter-select-menu"><button type="button" :class="{active:!categoryFilter}" @click="pickCategoryFilter('')">全部类别</button><button v-for="c in flowCategories" :key="c.id" type="button" :class="{active:categoryFilter===c.id}" @click="pickCategoryFilter(c.id)">{{c.name}}</button></div></div></label>
  </div>
  <section v-if="incidentsOpen" class="surface form-stack workflow-incident-center" aria-label="流程事件中心">
    <div class="section-heading"><div><h3>流程事件中心</h3><p class="muted">超时只会提醒，不会自动同意；人员配置修复后可重新解析当前节点。</p></div><button type="button" :disabled="busy" @click="refreshIncidents">刷新</button></div>
    <p v-if="!incidents.length" class="muted">当前没有阻塞或超时的运行中审批。</p>
    <article v-for="item in incidents" :key="item.id" class="workflow-incident-row">
      <div><strong>{{item.definition.name}} · {{item.node?.name||'流程结束节点'}}</strong><p><span v-if="item.incident==='ASSIGNMENT_BLOCKED'">审批人员解析阻塞</span><span v-else-if="item.incident==='TIMER_FAILED'">定时事件处理失败</span><span v-else-if="item.incident">{{item.incident}}</span><span v-if="item.overdue">{{item.incident?'；':''}}办理已超时</span></p><small v-if="item.deadline?.due_at" class="muted">应办时间 {{displayTime(item.deadline.due_at)}} · 第 {{item.definition.version}} 版</small></div>
      <div v-if="item.retryable" class="workflow-incident-retry"><input v-model="incidentReasons[item.id]" maxlength="500" placeholder="填写修复内容和恢复原因" aria-label="恢复原因"/><button type="button" :disabled="busy" @click="retryIncident(item)">重新解析人员</button></div>
      <p v-else class="muted">该事件不改变审批决定；请先处理对应运行环境或等待审批人办理。</p>
    </article>
  </section>
  <p v-if="notice" role="status">{{notice}}</p>
  <section v-if="selected" class="surface workflow-viewer" aria-label="流程详情">
    <div class="section-heading workflow-viewer-head"><div><h3>{{selected.name}}</h3><div class="workflow-viewer-meta"><span>第 {{selected.version}} 版</span><span>{{selected.status==='PUBLISHED'?'已发布':'草稿'}}</span><span>{{categoryName(selected.category_id)}}</span><span>{{selected.instance_count}} 个实例</span></div></div><button @click="selected=null">关闭</button></div>
    <WorkflowCanvas :nodes="selected.config.nodes" :users="users" :groups="assignmentGroups" :selected-index="-1" :storage-key="selected.process_key" read-only/>
    <div class="actions workflow-viewer-actions"><button @click="copy(selected,selected.status==='DRAFT')">{{selected.status==='DRAFT'?'修改当前草稿':'修改并另存新版本'}}</button><button v-if="selected.status==='DRAFT'" class="primary" :disabled="busy" @click="publish(selected)">校验并发布</button></div>
  </section>
  <section v-if="historyKey" class="surface form-stack workflow-history-panel" aria-label="版本历史"><div class="section-heading"><h3>{{history[0]?.name||'审批流程'}} · 版本历史</h3><button @click="historyKey=''">收起历史</button></div><div v-for="t in history" :key="t.id" class="section-heading"><span>第 {{t.version}} 版 · {{t.status==='PUBLISHED'?'已发布':'草稿'}} · {{t.name}}</span><div class="actions"><button @click="view(t)">查看第 {{t.version}} 版</button><button @click="copy(t,t.status==='DRAFT')">{{t.status==='DRAFT'?'修改草稿':'修改为新版本'}}</button></div></div><button v-if="historyNext" @click="openHistory({process_key:historyKey},true)">加载更早版本</button><p class="muted">新版本保存后出现在这里；草稿需发布后才可发起审批。旧实例继续使用发起时的版本。</p></section>
  <form v-if="editing" class="form-stack" @submit.prevent="save">
    <h3>{{editId?'修改当前草稿':'新版本草稿编辑'}}</h3>
    <div class="workflow-meta-grid">
      <label>流程类别<select v-model="categoryId" required><option value="" disabled>请选择类别</option><option v-for="c in flowCategories.filter(x=>x.active)" :key="c.id" :value="c.id">{{c.name}}</option></select></label>
      <label>模板名称<input v-model="name" required maxlength="150"/></label>
      <label>绑定资料模板版本<select v-model="materialTemplateId" @change="selectMaterial"><option value="">暂不绑定</option><option v-for="t in materialTemplates.filter(x=>x.status==='PUBLISHED')" :key="t.id" :value="t.id">{{t.name}} · 第 {{t.version}} 版</option></select></label>
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
          <label class="workflow-overlay-switch"><input type="checkbox" :checked="!!selectedNode.add_sign_policy" @change="toggleAddSign(selectedNode,($event.target as HTMLInputElement).checked)"/><span><b>允许加签</b><small>审批人办理时可增加复核人</small></span></label>
          <template v-if="selectedNode.add_sign_policy">
            <div class="workflow-add-sign-timings"><button type="button" :class="{active:selectedNode.add_sign_policy.timings.includes('PRE')}" @click="setAddSignTiming(selectedNode,'PRE')">前加签<small>新增人员先审</small></button><button type="button" :class="{active:selectedNode.add_sign_policy.timings.includes('POST')}" @click="setAddSignTiming(selectedNode,'POST')">后加签<small>本人同意后再审</small></button></div>
            <div class="workflow-add-sign-pool"><span>可选加签人</span><div class="workflow-add-sign-chips"><button v-for="userId in selectedNode.add_sign_policy.users" :key="userId" type="button" :title="'移除 '+(users.find(user=>user.id===userId)?.display_name||'人员')" @click="removeAddSignUser(selectedNode,userId)">{{users.find(user=>user.id===userId)?.display_name||'人员不可用'}}<X :size="11"/></button><small v-if="!selectedNode.add_sign_policy.users.length">尚未选择候选人</small></div><div class="workflow-add-sign-search"><UserPlus :size="14"/><input v-model="addSignQuery" type="search" placeholder="搜索姓名、账号或部门"/></div><button v-for="user in addSignCandidates" :key="user.id" type="button" class="workflow-add-sign-result" @click="addAddSignUser(selectedNode,user.id)"><span>{{user.display_name}}</span><small>{{user.department||user.username}}</small></button></div>
          </template>
        </aside>
      </template>
    </WorkflowCanvas>
    <template v-for="{node:n,index:i} in selectedNodeEntry" :key="n.key">
      <details class="surface workflow-node-inspector" :aria-label="'审批节点 '+(i+1)">
        <summary class="workflow-node-inspector-summary"><span><strong>{{n.name}}</strong><small>{{modeName(n.mode)}} · {{assignmentNames(n)||'未配置审批人员'}}</small></span><span>高级设置</span></summary>
        <div class="form-stack workflow-node-inspector-body">
        <div v-if="nodes.length>1" class="workflow-node-editor-actions"><button type="button" class="subtle" aria-label="删除节点" @click="removeNode(i)"><Trash2 :size="14"/>删除当前节点</button></div>
        <div class="form-grid"><label>节点名称<input v-model="n.name" required/></label></div>
        <label>审批方式<select :value="n.mode" @change="setNodeMode(n,($event.target as HTMLSelectElement).value)"><option value="ALL">全部人员同意（会签）</option><option value="ANY">任一人员同意（或签）</option><option value="CLAIM">候选人领取后办理</option></select></label>
        <p v-if="n.mode==='CLAIM'" class="muted small">进入节点时只生成一个候选任务，不会为每位候选人建立审批票；首位成功领取者取得唯一责任席位，其他候选人的入口立即关闭。</p>
        <label class="check-label"><input type="checkbox" :checked="!!n.sla" @change="toggleSla(n,($event.target as HTMLInputElement).checked)"/>设置节点办理时限与提前提醒</label>
        <div v-if="n.sla" class="form-grid"><label>办理时限（小时）<input v-model.number="n.sla.due_hours" type="number" min="1" max="8760" required/></label><label>提前提醒（小时）<input v-model.number="n.sla.remind_before_hours" type="number" min="0" :max="Math.max(0,n.sla.due_hours-1)" required/></label></div>
        <p v-if="n.sla" class="muted small">时间记录持久化在 PostgreSQL，服务重启后会补扫遗漏事件。填 0 表示不提前提醒；到期仅提醒和进入事件中心，不会自动同意或替用户提交决定。</p>
        <label class="check-label"><input v-model="n.allow_transfer" type="checkbox"/>允许当前审批人转交本人的审批席位</label>
        <p class="muted small">转交只更换当前席位负责人；系统会在准备和确认时重新校验目标人员的业务读取与审批权限。</p>
        <label class="check-label"><input v-model="n.allow_proxy" type="checkbox"/>允许管理员为此节点配置人工审批代理</label>
        <p class="muted small">代理不改变席位负责人，也不增加票数；决定会同时记录原责任人与实际操作人，代理人仍须具备当前业务权限。</p>
        <label class="check-label"><input type="checkbox" :checked="!!n.agent_auto_approval" :disabled="n.mode==='CLAIM'" @change="toggleAgentAuto(n,($event.target as HTMLInputElement).checked)"/>允许审批人本人授权后由 Agent 自动同意该节点</label>
        <p class="muted small">只建议用于低风险、资料齐全时可例行同意的节点。Agent 不会自动驳回，也不能绕过审批人员、业务权限、资料版本或必须驳回条件。</p>
        <div v-if="n.agent_auto_approval" class="surface form-stack">
          <strong>自动审批安全条件</strong>
          <p class="muted small">可选。设置后条件必须明确满足才会自动同意；条件不满足或资料不足时继续人工处理。</p>
          <template v-if="n.agent_auto_policy"><RuleEditor v-model="n.agent_auto_policy.condition" :contract="materialContract"/><button type="button" @click="setAgentAutoPolicy(n,false)">移除安全条件</button></template>
          <button v-else type="button" class="subtle" @click="setAgentAutoPolicy(n,true)">增加安全条件</button>
        </div>
        <label>人员来源<select :value="n.assignment?'RULE':'USERS'" @change="assignmentMode(n,($event.target as HTMLSelectElement).value==='RULE')"><option value="USERS">指定人员</option><option value="RULE">按角色或部门选择</option></select></label>
        <fieldset v-if="!n.assignment"><legend>审批人员</legend><label class="check-label" v-for="u in users.filter(x=>x.active)" :key="u.id"><input v-model="n.users" type="checkbox" :value="u.id"/>{{u.display_name}}</label></fieldset>
        <template v-else>
          <fieldset><legend>角色（可多选）</legend><label v-for="g in assignmentGroups.filter(x=>x.kind==='ROLE'&&x.active)" :key="g.id" class="check-label"><input v-model="n.assignment.roles" type="checkbox" :value="g.id"/>{{g.name}} · {{g.members.length}} 位成员</label></fieldset>
          <fieldset><legend>部门（可多选）</legend><label v-for="g in assignmentGroups.filter(x=>x.kind==='DEPARTMENT'&&x.active)" :key="g.id" class="check-label"><input v-model="n.assignment.departments" type="checkbox" :value="g.id"/>{{g.name}}</label></fieldset>
          <label class="check-label"><input v-model="n.assignment.department_heads_only" type="checkbox"/>只选择所选部门的负责人</label>
          <p class="muted">同类多选取并集；同时配置角色和部门时必须同时满足。当前候选：{{candidateNames(n)}}。</p>
        </template>
        <p class="muted small">配置人员不会自动授予业务权限。会签人员失效时等待处理，不能减少签名人数后放行。</p>
        <fieldset><legend>允许退回到</legend><label v-for="target in returnTargets(i)" :key="target.key" class="check-label"><input v-model="n.return_policy.targets" type="checkbox" :value="target.key"/>{{target.name}}</label></fieldset>
        <p class="muted small">只能选择申请人或当前节点之前的责任节点。退回会结束当前审批轮并记录目标；修改后重新提交创建新轮次并从首个节点完整重审。</p>
        <div v-for="(r,j) in n.reject_rules" :key="j" class="surface form-stack"><strong>必须驳回条件 {{Number(j)+1}}</strong><RuleEditor v-model="r.condition" :contract="materialContract"/><label>驳回原因<input v-model="r.reason" required/></label><button type="button" @click="n.reject_rules.splice(j,1)">删除驳回条件</button></div>
        <button type="button" class="subtle" @click="n.reject_rules.push({condition:newCondition(),reason:''})">增加必须驳回条件</button>
        <label class="check-label"><input type="checkbox" :checked="!!n.routes" @change="routing(n,i,($event.target as HTMLInputElement).checked)"/>按条件选择后续节点</label>
        <template v-if="n.routes">
          <div v-for="(r,j) in n.routes" :key="j" class="surface form-stack"><strong>条件分支 {{Number(j)+1}}</strong><RuleEditor v-model="r.condition" :contract="materialContract"/><label>命中后前往<select v-model="r.target"><option v-for="t in targets(i)" :key="t.key" :value="t.key">{{t.name}}</option></select></label><button v-if="n.routes.length>1" type="button" @click="n.routes.splice(j,1)">删除分支</button></div>
          <button type="button" @click="n.routes.push({condition:newCondition(),target:targets(i)[0].key})">增加条件分支</button>
          <label>全部条件未命中时前往<select v-model="n.default_target"><option v-for="t in targets(i)" :key="t.key" :value="t.key">{{t.name}}</option></select></label>
          <p class="muted small">明细字段可判断任一行，数值字段可按合计判断；命中多个分支或资料缺失时阻塞，不走默认出口。金额条件请同时限定币种。</p>
        </template><p v-else class="muted small">全部通过后 → {{nodes[i+1]?.name || '审批结束'}}</p>
        </div>
      </details>
    </template>
    <div class="actions"><button type="button" @click="editing=false">取消</button><button class="primary" :disabled="busy">{{editId?'保存当前草稿':'保存为新版本草稿'}}</button></div>
  </form>
  <article v-for="t in groups" :key="t.id" class="surface workflow-summary-card"><div class="section-heading"><h3><GitBranch :size="15"/>{{t.name}}</h3><span class="status">{{t.status==='PUBLISHED'?'已发布':'草稿'}} · 第 {{t.version}} 版</span></div><div class="flow-preview"><span>发起</span><template v-for="n in t.config?.nodes" :key="n.key"><span class="muted">→</span><span>{{n.name}}{{n.routes?'（条件路由）':''}}</span></template></div><p class="muted">类别：{{categoryName(t.category_id)}}；已发布版本保持不变</p><div class="actions"><button @click="view(t)">查看流程</button><button @click="openHistory(t)">版本历史</button><button @click="copy(t,t.status==='DRAFT')">{{t.status==='DRAFT'?'修改当前草稿':'修改并另存新版本'}}</button><button v-if="t.status==='DRAFT'" class="primary" :disabled="busy" @click="publish(t)">校验并发布</button></div></article>
</template>
