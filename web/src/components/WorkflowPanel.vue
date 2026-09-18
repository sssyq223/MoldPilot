<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Plus, ArrowDown, Trash2, GitBranch } from 'lucide-vue-next'
import { api, post } from '../api'
import RuleEditor from './RuleEditor.vue'
import WorkflowCategoryPanel from './WorkflowCategoryPanel.vue'
import MaterialTemplatePanel from './MaterialTemplatePanel.vue'
import {ruleText,routeName} from '@domain-pack/uiText'
import {workflowUi} from '@domain-pack/uiPolicy'
const emit = defineEmits<{error:[message:string]}>()
const templates=ref<any[]>([]), users=ref<any[]>([])
const editing=ref(false), busy=ref(false), name=ref(''), key=ref('')
const nodes=ref<any[]>([]), simulation=ref<any>(null), materialContract=ref<any>(null)
const editId=ref(''), editHash=ref(''), selected=ref<any>(null), historyKey=ref(''), history=ref<any[]>([]), historyNext=ref<number|null>(null), notice=ref('')
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
const sample=ref<Record<string,any>>(Object.fromEntries(workflowUi.simulationFields.map((field:any)=>[field.key,field.value])))
const newCondition=()=>({field:workflowUi.defaultField,op:'gt',value:''})
const outcomes:Record<string,string>={ROUTE_VALID:'路径校验通过',MUST_REJECT:'命中必须驳回条件',RULE_DATA_MISSING:'驳回判断资料不足',ROUTE_DATA_MISSING:'分支判断资料不足',ROUTE_AMBIGUOUS:'同时命中多个分支'}
const freshNode=(i:number)=>({key:'review_'+i,name:'审批节点 '+i,users:[],mode:'ALL',reject_rules:[]})
function toggleAgentAuto(n:any,enabled:boolean){n.agent_auto_approval=enabled;if(!enabled)delete n.agent_auto_policy}
function setAgentAutoPolicy(n:any,enabled:boolean){if(enabled)n.agent_auto_policy={condition:{...newCondition(),op:'lte'}};else delete n.agent_auto_policy}
async function load(){const all:any[]=[];for(let offset=0;;offset+=100){const page=await api(`/workflows?offset=${offset}&limit=100`);all.push(...page);if(page.length<100)break}templates.value=all;const people=await api('/workflows/assignment-catalog');users.value=people.users;assignmentGroups.value=people.groups;await refreshCategories();await refreshMaterials()}
onMounted(async()=>{try{await load()}catch(e:any){emit('error',e.message)}})
watch([nodes,sample,categoryId],()=>{simulation.value=null},{deep:true})
function create(){editId.value='';editHash.value='';selected.value=null;name.value='';key.value='flow_'+crypto.randomUUID().replaceAll('-','');categoryId.value='';legacyCopy.value=false;materialContract.value=null;materialTemplateId.value='';nodes.value=[freshNode(1)];editing.value=true}
async function view(t:any){try{selected.value=await api(`/workflows/${t.id}`);editing.value=false}catch(e:any){emit('error',e.message)}}
async function openHistory(t:any,more=false){try{const r=await api(`/workflows/history/${encodeURIComponent(t.process_key)}${more?'?before_version='+historyNext.value:''}`);historyKey.value=t.process_key;history.value=more?[...history.value,...r.items]:r.items;historyNext.value=r.next_before}catch(e:any){emit('error',e.message)}}
async function copy(t:any,edit=false){try{const d=await api(`/workflows/${t.id}`);editId.value=edit?d.id:'';editHash.value=d.edit_hash;categoryId.value=d.category_id||'';legacyCopy.value=d.business_type!=='generic';name.value=d.name;key.value=d.process_key;materialTemplateId.value=d.material_template_id||'';nodes.value=JSON.parse(JSON.stringify(d.config.nodes));materialContract.value=d.config.material_contract?JSON.parse(JSON.stringify(d.config.material_contract)):null;selected.value=null;editing.value=true}catch(e:any){emit('error',e.message)}}
const configuration=()=>({business_type:'generic',nodes:nodes.value,...(materialContract.value?{material_contract:materialContract.value}:{})})
async function save(){busy.value=true;try{const r=editId.value?await api(`/workflows/${editId.value}`,{method:'PUT',body:JSON.stringify({name:name.value,config:configuration(),category_id:categoryId.value,material_template_id:materialTemplateId.value||null,expected_hash:editHash.value})}):await post('/workflows',{process_key:key.value,name:name.value,config:configuration(),category_id:categoryId.value,material_template_id:materialTemplateId.value||null});notice.value=`第 ${r.version} 版草稿已保存，尚未发布`;editing.value=false;await load();await openHistory({process_key:key.value});await view(r)}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
async function publish(t:any){busy.value=true;try{await post(`/workflows/${t.id}/publish`);notice.value=`第 ${t.version} 版已发布，已有审批实例继续使用原版本`;await load();if(historyKey.value===t.process_key)await openHistory(t);if(selected.value?.id===t.id)await view(t)}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
function addNode(){let i=nodes.value.length+1;while(nodes.value.some(n=>n.key==='review_'+i))i++;nodes.value.push(freshNode(i))}
function removeNode(i:number){const target=nodes.value[i].key;if(nodes.value.some((n,j)=>j!==i&&(n.default_target===target||n.routes?.some((r:any)=>r.target===target)))){emit('error','请先修改指向该节点的分支，再删除节点');return}nodes.value.splice(i,1)}
function targets(i:number){return [...nodes.value.slice(i+1).map(n=>({key:n.key,name:n.name})),{key:'end',name:'审批结束'}]}
function routing(n:any,i:number,enabled:boolean){if(enabled){n.routes=[{condition:newCondition(),target:targets(i)[0].key}];n.default_target=targets(i)[0].key}else{delete n.routes;delete n.default_target}}
const nodeName=(target:string)=>routeName(target,nodes.value)
async function simulate(){busy.value=true;try{const snapshot=Object.fromEntries(Object.entries(sample.value).filter(([,v])=>v!==''));simulation.value=await post('/workflows/simulate',{config:configuration(),snapshot})}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
</script>
<template>
  <div class="section-heading"><div><h2>审批流程配置</h2><p class="muted">配置人员、条件和路线，保存版本并重复使用。</p></div><button class="workflow-create-button" @click="create"><Plus :size="16"/>新建模板</button></div>
  <div class="workflow-topbar">
    <MaterialTemplatePanel @changed="refreshMaterials" @error="emit('error',$event)"/>
    <WorkflowCategoryPanel :categories="flowCategories" @changed="refreshCategories" @error="emit('error',$event)"/>
    <label class="workflow-category-filter"><span>按类别查看</span><div class="workflow-filter-select" :class="{open:categoryFilterOpen}"><button type="button" class="workflow-filter-select-button" @click="categoryFilterOpen=!categoryFilterOpen"><span>{{categoryFilterLabel}}</span><i aria-hidden="true"></i></button><div v-if="categoryFilterOpen" class="workflow-filter-select-menu"><button type="button" :class="{active:!categoryFilter}" @click="pickCategoryFilter('')">全部类别</button><button v-for="c in flowCategories" :key="c.id" type="button" :class="{active:categoryFilter===c.id}" @click="pickCategoryFilter(c.id)">{{c.name}}</button></div></div></label>
  </div>
  <p v-if="notice" role="status">{{notice}}</p>
  <section v-if="selected" class="surface form-stack" aria-label="流程详情">
    <div class="section-heading"><h3>{{selected.name}} · 第 {{selected.version}} 版 · {{selected.status==='PUBLISHED'?'已发布':'草稿'}}</h3><button @click="selected=null">关闭详情</button></div>
    <p class="muted">已关联 {{selected.instance_count}} 个审批实例</p><p>流程类别：{{categoryName(selected.category_id)}}</p>
    <ol><li v-for="(n,i) in selected.config.nodes" :key="n.key" class="form-stack surface"><strong>{{n.name}} · {{n.mode==='ALL'?'全部人员同意（会签）':'任一人员同意（或签）'}}</strong>
      <p>审批人员：{{assignmentNames(n)}}</p><p v-if="n.assignment" class="muted">当前候选人员：{{candidateNames(n)}}。实际进入节点时校验业务与材料读取权限。</p>
      <p v-if="n.agent_auto_approval" class="muted">Agent 自动审批：允许审批人本人授权后自动同意该节点；未授权时仍人工处理。</p>
      <p v-if="n.agent_auto_policy" class="muted">自动审批安全条件：{{ruleText(n.agent_auto_policy.condition)}}。不满足或资料不足时保持人工审批。</p>
      <h4>必须驳回条件</h4><p v-if="!n.reject_rules.length" class="muted">未设置</p><div v-for="(r,j) in n.reject_rules" :key="j"><p>{{ruleText(r.condition)}}</p><p>驳回原因：{{r.reason}}</p></div>
      <h4>后续流转</h4><template v-if="n.routes"><p v-for="(r,j) in n.routes" :key="j">当{{ruleText(r.condition)}} → {{routeName(r.target,selected.config.nodes)}}</p><p>全部条件未命中 → {{routeName(n.default_target,selected.config.nodes)}}</p><small class="muted">资料缺失或同时命中多个分支时，等待处理。</small></template><p v-else>审批通过 → {{selected.config.nodes[Number(i)+1]?.name||'审批结束'}}</p>
    </li></ol><div class="actions"><button @click="copy(selected,selected.status==='DRAFT')">{{selected.status==='DRAFT'?'修改当前草稿':'修改并另存新版本'}}</button><button v-if="selected.status==='DRAFT'" :disabled="busy" @click="publish(selected)">校验并发布</button></div>
  </section>
  <section v-if="historyKey" class="surface form-stack" aria-label="版本历史"><div class="section-heading"><h3>{{history[0]?.name||'审批流程'}} · 版本历史</h3><button @click="historyKey=''">收起历史</button></div><div v-for="t in history" :key="t.id" class="section-heading"><span>第 {{t.version}} 版 · {{t.status==='PUBLISHED'?'已发布':'草稿'}} · {{t.name}}</span><div class="actions"><button @click="view(t)">查看第 {{t.version}} 版</button><button @click="copy(t,t.status==='DRAFT')">{{t.status==='DRAFT'?'修改草稿':'修改为新版本'}}</button></div></div><button v-if="historyNext" @click="openHistory({process_key:historyKey},true)">加载更早版本</button><p class="muted">新版本保存后出现在这里；草稿需发布后才可发起审批。旧实例继续使用发起时的版本。</p></section>
  <form v-if="editing" class="form-stack" @submit.prevent="save">
    <h3>{{editId?'修改当前草稿':'新版本草稿编辑'}}</h3>
    <label>流程类别<select v-model="categoryId" required><option value="" disabled>请选择类别</option><option v-for="c in flowCategories.filter(x=>x.active)" :key="c.id" :value="c.id">{{c.name}}</option></select></label>
    <p v-if="legacyCopy" class="muted">此次保存采用通用模板配置。原已发布版本及已有实例保持原规则。</p>
    <div class="form-grid"><label>模板名称<input v-model="name" required maxlength="150"/></label></div>
    <label>绑定资料模板版本<select v-model="materialTemplateId" @change="selectMaterial"><option value="">暂不绑定</option><option v-for="t in materialTemplates.filter(x=>x.status==='PUBLISHED')" :key="t.id" :value="t.id">{{t.name}} · 第 {{t.version}} 版</option></select></label>
    <p v-if="materialTemplateId" class="muted">已绑定明确的字段版本。资料上传、核对及动态条件页面正在补齐，当前这类模板尚不能正式发起。</p>
    <div class="flow-start">申请提交</div>
    <template v-for="(n,i) in nodes" :key="i"><ArrowDown class="flow-arrow" :size="18"/>
      <section class="surface form-stack" :aria-label="'审批节点 '+(i+1)">
        <div class="section-heading"><strong>审批节点 {{i+1}}</strong><button v-if="nodes.length>1" type="button" class="icon-button" aria-label="删除节点" @click="removeNode(i)"><Trash2 :size="16"/></button></div>
        <div class="form-grid"><label>节点名称<input v-model="n.name" required/></label></div>
        <label>审批方式<select v-model="n.mode"><option value="ALL">全部人员同意（会签）</option><option value="ANY">任一人员同意（或签）</option></select></label>
        <label class="check-label"><input type="checkbox" :checked="!!n.agent_auto_approval" @change="toggleAgentAuto(n,($event.target as HTMLInputElement).checked)"/>允许审批人本人授权后由 Agent 自动同意该节点</label>
        <p class="muted small">只建议用于低风险、资料齐全时可例行同意的节点。Agent 不会自动驳回，也不能绕过审批人员、业务权限、资料版本或必须驳回条件。</p>
        <div v-if="n.agent_auto_approval" class="surface form-stack">
          <strong>自动审批安全条件</strong>
          <p class="muted small">可选。设置后条件必须明确满足才会自动同意；条件不满足或资料不足时继续人工处理。</p>
          <template v-if="n.agent_auto_policy"><RuleEditor v-model="n.agent_auto_policy.condition"/><button type="button" @click="setAgentAutoPolicy(n,false)">移除安全条件</button></template>
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
        <div v-for="(r,j) in n.reject_rules" :key="j" class="surface form-stack"><strong>必须驳回条件 {{Number(j)+1}}</strong><RuleEditor v-model="r.condition"/><label>驳回原因<input v-model="r.reason" required/></label><button type="button" @click="n.reject_rules.splice(j,1)">删除驳回条件</button></div>
        <button type="button" class="subtle" @click="n.reject_rules.push({condition:newCondition(),reason:''})">增加必须驳回条件</button>
        <label class="check-label"><input type="checkbox" :checked="!!n.routes" @change="routing(n,i,($event.target as HTMLInputElement).checked)"/>按条件选择后续节点</label>
        <template v-if="n.routes">
          <div v-for="(r,j) in n.routes" :key="j" class="surface form-stack"><strong>条件分支 {{Number(j)+1}}</strong><RuleEditor v-model="r.condition"/><label>命中后前往<select v-model="r.target"><option v-for="t in targets(i)" :key="t.key" :value="t.key">{{t.name}}</option></select></label><button v-if="n.routes.length>1" type="button" @click="n.routes.splice(j,1)">删除分支</button></div>
          <button type="button" @click="n.routes.push({condition:newCondition(),target:targets(i)[0].key})">增加条件分支</button>
          <label>全部条件未命中时前往<select v-model="n.default_target"><option v-for="t in targets(i)" :key="t.key" :value="t.key">{{t.name}}</option></select></label>
          <p class="muted small">多明细按“任一明细满足”判断；命中多个分支或缺少资料时阻塞，不走默认出口。金额条件请同时限定币种。</p>
        </template><p v-else class="muted small">全部通过后 → {{nodes[i+1]?.name || '审批结束'}}</p>
      </section>
    </template>
    <button type="button" @click="addNode">增加审批节点</button>
    <section class="surface form-stack" aria-label="路径模拟"><h3>保存前模拟</h3><p class="muted">填写测试值检查路径，不创建真实单据或待办。留空可验证资料不足时是否阻塞。</p>
      <div class="form-grid"><label v-for="field in workflowUi.simulationFields" :key="field.key">{{field.label}}<select v-if="field.options" v-model="sample[field.key]"><option v-if="field.emptyLabel" value="">{{field.emptyLabel}}</option><option v-for="(label,code) in field.options" :key="code" :value="code">{{label}}</option></select><input v-else v-model="sample[field.key]"/></label></div>
      <button type="button" :disabled="busy" @click="simulate">模拟流转</button>
      <div v-if="simulation" role="status" class="form-stack"><strong>{{outcomes[simulation.outcome] || '需要检查流程配置'}}</strong><ol><li v-for="step in simulation.path" :key="step.key">{{step.name}}<span v-if="step.target"> → {{nodeName(step.target)}}</span><p v-for="r in step.rejection_reasons" :key="r">必须驳回：{{r}}</p><p v-for="r in step.missing_rules" :key="r">资料不足：{{r}}</p></li></ol><small class="muted">模拟不代表人员当前授权或实际业务前置已通过。</small></div>
    </section>
    <div class="actions"><button type="button" @click="editing=false">取消</button><button class="primary" :disabled="busy">{{editId?'保存当前草稿':'保存为新版本草稿'}}</button></div>
  </form>
  <article v-for="t in groups" :key="t.id" class="surface"><div class="section-heading"><h3><GitBranch :size="17"/>{{t.name}}</h3><span class="status">{{t.status==='PUBLISHED'?'已发布':'草稿'}} · 第 {{t.version}} 版</span></div><div class="flow-preview"><span>发起</span><template v-for="n in t.config?.nodes" :key="n.key"><span class="muted">→</span><span>{{n.name}}{{n.routes?'（条件路由）':''}}</span></template></div><p class="muted">类别：{{categoryName(t.category_id)}}；已发布版本保持不变</p><div class="actions"><button @click="view(t)">查看流程</button><button @click="openHistory(t)">版本历史</button><button @click="copy(t,t.status==='DRAFT')">{{t.status==='DRAFT'?'修改当前草稿':'修改并另存新版本'}}</button><button v-if="t.status==='DRAFT'" class="primary" :disabled="busy" @click="publish(t)">校验并发布</button></div></article>
</template>
