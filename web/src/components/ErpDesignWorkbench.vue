<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ArrowLeft, Database, Download, Eye, GitCompare, Pencil, Plus, RefreshCw, Search, Trash2, Upload, X } from 'lucide-vue-next'
import { api, post, shanghai } from '../api'
import ErpDrawingPreview from './ErpDrawingPreview.vue'

type TabKey='orders'|'drawings'|'densities'|'hardware'
const props=defineProps<{permissions:string[]}>()
const emit=defineEmits<{close:[]}>()
const tabs:{key:TabKey;label:string;description:string}[]=[
 {key:'orders',label:'设计订单',description:'请购、审批与订单明细'},
 {key:'drawings',label:'图纸版本',description:'版本主线、详情和对比'},
 {key:'densities',label:'材质密度',description:'设计核价基础资料'},
 {key:'hardware',label:'标准件',description:'厂内标准件图纸目录'},
]
const active=ref<TabKey>('orders')
const rows=ref<any[]>([]),total=ref(0),page=ref(1),pageSize=ref(20)
const loading=ref(false),saving=ref(false),error=ref(''),notice=ref('')
const keyword=ref(''),status=ref(''),partCode=ref(''),version=ref(''),historyStatus=ref('')
const detail=ref<any|null>(null),detailTitle=ref(''),detailLoading=ref(false)
const selectedDrawingIds=ref<number[]>([]),compareResult=ref<any|null>(null)
const densityEditor=ref<{id:number|null;materialMark:string;density:string}|null>(null)
const previewTarget=ref<{url:string;row:any}|null>(null)
const hardwareInput=ref<HTMLInputElement|null>(null),hardwareUploading=ref(false)
const canWrite=computed(()=>props.permissions.includes('design_route.execute'))
const pageCount=computed(()=>Math.max(1,Math.ceil(total.value/pageSize.value)))

function dataValue(response:any){
 let value=response?.data??response
 for(let index=0;index<3;index++){
  if(value&&typeof value==='object'&&!Array.isArray(value)&&value.data&&typeof value.data==='object'&&!Array.isArray(value.data)&&!value.rows&&!value.records&&!value.list)value=value.data
  else break
 }
 return value
}
function listValue(response:any){
 const value=dataValue(response)||{}
 const found=Array.isArray(value)?value:(value.rows||value.records||value.list||[])
 return {rows:Array.isArray(found)?found:[],total:Number(value.total??value.count??found.length??0)}
}
function query(){
 const base:any={pageNum:page.value,pageSize:pageSize.value}
 if(active.value==='orders'){
  if(keyword.value.trim())base.keyword=keyword.value.trim()
  if(status.value)base.status=status.value
 }else if(active.value==='drawings'){
  if(keyword.value.trim())base.moldCode=keyword.value.trim()
  if(partCode.value.trim())base.partCode=partCode.value.trim()
  if(version.value.trim())base.version=Number(version.value)||version.value.trim()
  if(historyStatus.value)base.historyStatus=historyStatus.value
 }else if(active.value==='densities'){
  if(keyword.value.trim())base.materialMark=keyword.value.trim()
 }else if(keyword.value.trim())base.keyword=keyword.value.trim()
 return base
}
function endpoint(){return active.value==='orders'?'/erp-design-workspace/orders/query':active.value==='drawings'?'/erp-design-workspace/drawing-versions/query':active.value==='densities'?'/erp-design-workspace/densities/query':'/erp-design-workspace/standard-hardware/query'}
async function load(){
 loading.value=true;error.value='';notice.value=''
 try{const value=listValue(await post(endpoint(),{query:query()}));rows.value=value.rows;total.value=value.total}
 catch(e:any){error.value=e?.message||'ERP 数据加载失败';rows.value=[];total.value=0}
 finally{loading.value=false}
}
function search(){page.value=1;load()}
function reset(){keyword.value='';status.value='';partCode.value='';version.value='';historyStatus.value='';page.value=1;load()}
function previous(){if(page.value>1){page.value--;load()}}
function next(){if(page.value<pageCount.value){page.value++;load()}}
function idOf(row:any){return Number(row.requestId??row.drawingId??row.id??0)}
function text(value:any){return value===undefined||value===null||value===''?'-':String(value)}
function date(value:any){return value?shanghai(String(value)):'-'}
function fileSize(value:any){const size=Number(value||0);if(!size)return '-';return size>=1024?`${(size/1024).toFixed(1)} MB`:`${size.toFixed(0)} KB`}
async function openDetail(row:any){
 const id=idOf(row);if(!id)return
 detailLoading.value=true;detail.value={};detailTitle.value=active.value==='orders'?'设计订单详情':'图纸版本详情'
 try{detail.value=dataValue(await api(active.value==='orders'?`/erp-design-workspace/orders/${id}`:`/erp-design-workspace/drawing-versions/${id}`))}
 catch(e:any){detail.value={error:e?.message||'详情加载失败'}}finally{detailLoading.value=false}
}
function primitiveEntries(value:any){
 if(!value||typeof value!=='object')return []
 return Object.entries(value).filter(([,item])=>item===null||['string','number','boolean'].includes(typeof item)).slice(0,24)
}
function detailRows(value:any){
 if(!value||typeof value!=='object')return []
 for(const key of ['items','details','orderItems','versions','changes','rows'])if(Array.isArray(value[key]))return value[key]
 return []
}
const labels:Record<string,string>={requestNo:'请购单号',orderNo:'订单号',moldNo:'模具号',moldCode:'模具号',partCode:'零件号',typeLabel:'类型',sourceLabel:'来源',statusLabel:'状态',approvalStatus:'审批状态',currentStageLabel:'当前节点',currentVersion:'当前版本',currentStatus:'版本状态',fileName:'文件名',submittedName:'提交人',approvedName:'审批人',effectiveAt:'生效时间',createdAt:'创建时间',updatedAt:'更新时间',materialMark:'材质',density:'密度',standardCode:'标准件编码',relativePath:'相对路径',extension:'扩展名',fileSize:'文件大小',fileSizeKb:'文件大小（KB）',totalAmount:'总金额',amountDisplay:'金额'}
function label(key:string){return labels[key]||key.replace(/([A-Z])/g,' $1')}

async function orderAction(row:any,operation:'delete'|'approve'|'resubmit'){
 const names={delete:'删除',approve:'审批通过',resubmit:'重新提交'}
 if(!window.confirm(`确认${names[operation]}设计订单 ${row.requestNo||row.orderNo||idOf(row)} 吗？此操作将直接写入 ERP。`))return
 saving.value=true;error.value=''
 try{await post(`/erp-design-workspace/orders/${idOf(row)}/action`,{operation,approval_version:row.approvalVersion||row.approval_version||undefined,confirm:true});notice.value=`ERP 设计订单已${names[operation]}`;await load()}
 catch(e:any){error.value=e?.message||'ERP 订单操作失败'}finally{saving.value=false}
}
function editDensity(row?:any){densityEditor.value={id:row?.id??null,materialMark:String(row?.materialMark??''),density:String(row?.density??'')}}
async function saveDensity(){
 if(!densityEditor.value)return
 const form=densityEditor.value
 if(!form.materialMark.trim()||!Number(form.density)){error.value='请填写材质和有效密度';return}
 saving.value=true;error.value=''
 try{await post('/erp-design-workspace/densities/manage',{operation:form.id?'update':'create',id:form.id,material_mark:form.materialMark.trim(),density:Number(form.density),confirm:true});densityEditor.value=null;notice.value='材质密度已写入 ERP';await load()}
 catch(e:any){error.value=e?.message||'材质密度保存失败'}finally{saving.value=false}
}
async function deleteDensity(row:any){
 if(!window.confirm(`确认从 ERP 删除材质 ${row.materialMark} 的密度配置吗？`))return
 saving.value=true;error.value=''
 try{await post('/erp-design-workspace/densities/manage',{operation:'delete',id:Number(row.id),confirm:true});notice.value='材质密度已删除';await load()}
 catch(e:any){error.value=e?.message||'材质密度删除失败'}finally{saving.value=false}
}
function toggleDrawing(id:number){selectedDrawingIds.value=selectedDrawingIds.value.includes(id)?selectedDrawingIds.value.filter(value=>value!==id):selectedDrawingIds.value.length>=2?[selectedDrawingIds.value[1],id]:[...selectedDrawingIds.value,id]}
async function compareDrawings(){
 if(selectedDrawingIds.value.length!==2)return
 saving.value=true;error.value=''
 try{compareResult.value=dataValue(await post('/erp-design-workspace/drawing-versions/compare',{from_version_id:selectedDrawingIds.value[0],to_version_id:selectedDrawingIds.value[1]}))}
 catch(e:any){error.value=e?.message||'图纸版本对比失败'}finally{saving.value=false}
}
function openFile(path:string){window.open(path,'_blank','noopener,noreferrer')}
function previewFile(url:string,row:any){previewTarget.value={url,row}}
async function renameHardware(row:any){
 const name=window.prompt('请输入新的标准件文件名',String(row.fileName||''));if(!name||name===row.fileName)return
 saving.value=true;error.value=''
 try{await post('/erp-design-workspace/standard-hardware/rename',{relative_path:row.relativePath,new_file_name:name,confirm:true});notice.value='标准件已重命名';await load()}
 catch(e:any){error.value=e?.message||'标准件重命名失败'}finally{saving.value=false}
}
async function deleteHardware(row:any){
 if(!window.confirm(`确认删除 ERP 标准件目录 ${row.relativePath} 吗？该操作不可撤销。`))return
 saving.value=true;error.value=''
 try{await post('/erp-design-workspace/standard-hardware/delete',{relative_path:row.relativePath,confirm:true});notice.value='标准件目录已删除';await load()}
 catch(e:any){error.value=e?.message||'标准件删除失败'}finally{saving.value=false}
}
function validateHardwareFolder(files:File[]){
 if(files.length!==2)throw new Error('文件夹内必须恰好包含一个 PRT 和一个 DWG 文件')
 const paths=files.map(file=>String((file as any).webkitRelativePath||''));const parts=paths.map(path=>path.split('/').filter(Boolean))
 if(parts.some(item=>item.length!==2))throw new Error('所选文件夹内不能包含子文件夹')
 const folders=new Set(parts.map(item=>item[0]));if(folders.size!==1)throw new Error('每次只能上传一个文件夹')
 const extensions=files.map(file=>file.name.slice(file.name.lastIndexOf('.')).toLowerCase())
 if(new Set(extensions).size!==2||!extensions.includes('.prt')||!extensions.includes('.dwg'))throw new Error('文件夹内必须包含 PRT、DWG 各一个')
 const stems=new Set(files.map((file,index)=>file.name.slice(0,-extensions[index].length).toLowerCase()));if(stems.size!==1)throw new Error('PRT、DWG 必须使用相同文件名')
 const folder=[...folders][0],stem=[...stems][0];if(!/^R-BZ-.+/i.test(folder)||!/^R-BZ-.+/i.test(stem)||folder.toLowerCase()!==stem)throw new Error('文件夹和两个文件必须同名，并以 R-BZ- 开头')
 return folder
}
function csrf(){return document.cookie.split('; ').find(value=>value.startsWith('agent_csrf='))?.split('=')[1]||''}
async function storeFile(file:File,conversationId?:string){
 const params=new URLSearchParams({filename:file.name,request_key:crypto.randomUUID()});if(conversationId)params.set('conversation_id',conversationId)
 const response=await fetch(`/api/files?${params}`,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':csrf()},body:file})
 const body=await response.json().catch(()=>({}));if(!response.ok)throw new Error(body.error?.message||`文件上传失败（${response.status}）`);return body
}
async function uploadHardware(event:Event){
 const input=event.target as HTMLInputElement,files=Array.from(input.files||[]);if(!files.length)return
 hardwareUploading.value=true;error.value=''
 try{
  const folder=validateHardwareFolder(files);const uploaded:any[]=[];let conversationId:string|undefined
  for(const file of files){const value=await storeFile(file,conversationId);conversationId=value.conversation_id;uploaded.push(value)}
  await post('/erp-design-workspace/standard-hardware/upload',{file_ids:uploaded.map(value=>value.id),folder_name:folder,confirm:true});notice.value='标准件文件夹已上传，ERP 将自动生成预览文件';await load()
 }catch(e:any){error.value=e?.message||'标准件文件夹上传失败'}finally{hardwareUploading.value=false;input.value=''}
}
watch(active,()=>{page.value=1;rows.value=[];selectedDrawingIds.value=[];error.value='';notice.value='';load()})
onMounted(load)
</script>

<template>
 <main class="erp-workbench">
  <header class="erp-head">
   <div class="erp-title"><button class="erp-icon-button" aria-label="返回智能工作台" @click="emit('close')"><ArrowLeft :size="19"/></button><span class="erp-symbol"><Database :size="21"/></span><div><h1>ERP 设计管理</h1><p>D:\work2\management-system 实时数据 · 不保存业务副本</p></div></div>
   <button class="erp-button quiet" :disabled="loading" @click="load"><RefreshCw :size="15"/>刷新</button>
  </header>
  <div class="erp-layout">
   <nav class="erp-nav" aria-label="ERP 设计模块">
    <button v-for="tab in tabs" :key="tab.key" :class="{active:active===tab.key}" @click="active=tab.key"><strong>{{tab.label}}</strong><small>{{tab.description}}</small></button>
   </nav>
   <section class="erp-content">
    <div class="erp-content-title"><div><h2>{{tabs.find(tab=>tab.key===active)?.label}}</h2><p>{{tabs.find(tab=>tab.key===active)?.description}}，结果直接来自 ERP。</p></div><button v-if="active==='densities'&&canWrite" class="erp-button primary" @click="editDensity()"><Plus :size="15"/>新增密度</button><button v-if="active==='hardware'&&canWrite" class="erp-button primary" :disabled="hardwareUploading" @click="hardwareInput?.click()"><Upload :size="15"/>{{hardwareUploading?'上传中…':'上传标准件文件夹'}}</button><input v-if="active==='hardware'" ref="hardwareInput" hidden type="file" webkitdirectory multiple accept=".prt,.dwg" @change="uploadHardware"/></div>
    <form class="erp-filters" @submit.prevent="search">
     <label class="erp-search"><Search :size="15"/><input v-model="keyword" :placeholder="active==='orders'?'单号、模具号或关键词':active==='drawings'?'模具号':active==='densities'?'材质牌号':'标准件编码或文件名'"/></label>
     <template v-if="active==='orders'"><select v-model="status"><option value="">全部状态</option><option value="draft">草稿</option><option value="pending">审批中</option><option value="approved">已通过</option><option value="completed">已完成</option><option value="rejected">已退回</option></select></template>
     <template v-if="active==='drawings'"><input v-model="partCode" placeholder="零件号"/><input v-model="version" class="short" placeholder="版本"/><select v-model="historyStatus"><option value="">全部证据状态</option><option value="complete">完整</option><option value="incomplete">证据不完整</option></select></template>
     <button class="erp-button primary" type="submit">查询</button><button class="erp-button quiet" type="button" @click="reset">重置</button>
     <button v-if="active==='drawings'" class="erp-button quiet compare" type="button" :disabled="selectedDrawingIds.length!==2||saving" @click="compareDrawings"><GitCompare :size="15"/>对比所选（{{selectedDrawingIds.length}}/2）</button>
    </form>
    <p v-if="notice" class="erp-notice">{{notice}}</p><p v-if="error" class="erp-error" role="alert">{{error}}</p>
    <div class="erp-table-wrap">
     <table v-if="active==='orders'" class="erp-table"><thead><tr><th>请购/订单号</th><th>模具号</th><th>类型</th><th>明细</th><th>状态</th><th>当前节点</th><th>金额</th><th>创建时间</th><th>操作</th></tr></thead><tbody><tr v-for="row in rows" :key="idOf(row)"><td><strong>{{row.requestNo||row.orderNo||'-'}}</strong></td><td>{{text(row.moldNo)}}</td><td>{{text(row.typeLabel||row.sourceLabel)}}</td><td>{{text(row.itemCount)}}</td><td><span class="erp-tag">{{text(row.statusLabel)}}</span></td><td>{{text(row.currentStageLabel)}}</td><td>{{text(row.amountDisplay||row.totalAmount)}}</td><td>{{date(row.createdAt)}}</td><td class="actions"><button @click="openDetail(row)">详情</button><button v-if="canWrite&&row.canDesignApprove" :disabled="saving" @click="orderAction(row,'approve')">审批</button><button v-if="canWrite&&row.canResubmit" :disabled="saving" @click="orderAction(row,'resubmit')">重提</button><button v-if="canWrite&&row.canDelete" class="danger" :disabled="saving" @click="orderAction(row,'delete')">删除</button></td></tr></tbody></table>
     <table v-else-if="active==='drawings'" class="erp-table"><thead><tr><th class="check"></th><th>模具号</th><th>零件号</th><th>当前版本</th><th>状态</th><th>文件名</th><th>提交 / 审批</th><th>生效时间</th><th>操作</th></tr></thead><tbody><tr v-for="row in rows" :key="idOf(row)"><td><input type="checkbox" :checked="selectedDrawingIds.includes(idOf(row))" :aria-label="`选择图纸 ${row.partCode||idOf(row)}`" @change="toggleDrawing(idOf(row))"/></td><td>{{text(row.moldCode)}}</td><td><strong>{{text(row.partCode)}}</strong></td><td>V{{text(row.currentVersion)}}</td><td><span class="erp-tag">{{text(row.currentStatus||row.historyStatus)}}</span></td><td>{{text(row.fileName||row.dwgFileName||row.dxfFileName)}}</td><td>{{text(row.submittedName)}} / {{text(row.approvedName)}}</td><td>{{date(row.effectiveAt)}}</td><td class="actions"><button @click="openDetail(row)">详情</button><button @click="previewFile(`/api/erp-design-workspace/drawing-versions/${idOf(row)}/preview`,row)"><Eye :size="14"/>预览</button><button @click="openFile(`/api/erp-design-workspace/drawing-versions/${idOf(row)}/download`)"><Download :size="14"/>下载</button></td></tr></tbody></table>
     <table v-else-if="active==='densities'" class="erp-table"><thead><tr><th>ID</th><th>材质</th><th>密度</th><th>创建时间</th><th>更新时间</th><th v-if="canWrite">操作</th></tr></thead><tbody><tr v-for="row in rows" :key="row.id"><td>{{row.id}}</td><td><strong>{{text(row.materialMark)}}</strong></td><td>{{text(row.density)}}</td><td>{{date(row.createdAt)}}</td><td>{{date(row.updatedAt)}}</td><td v-if="canWrite" class="actions"><button @click="editDensity(row)"><Pencil :size="14"/>编辑</button><button class="danger" :disabled="saving" @click="deleteDensity(row)"><Trash2 :size="14"/>删除</button></td></tr></tbody></table>
     <table v-else class="erp-table"><thead><tr><th>标准件编码</th><th>文件名</th><th>路径</th><th>类型</th><th>大小</th><th>更新时间</th><th>操作</th></tr></thead><tbody><tr v-for="row in rows" :key="row.relativePath"><td><strong>{{text(row.standardCode)}}</strong></td><td>{{text(row.fileName)}}</td><td class="path">{{text(row.relativePath)}}</td><td>{{text(row.extension)}}</td><td>{{fileSize(row.fileSize)}}</td><td>{{date(row.updatedAt)}}</td><td class="actions"><button @click="previewFile(`/api/erp-design-workspace/standard-hardware/preview?relative_path=${encodeURIComponent(row.relativePath)}`,row)"><Eye :size="14"/>预览</button><button @click="openFile(`/api/erp-design-workspace/standard-hardware/download?relative_path=${encodeURIComponent(row.relativePath)}`)"><Download :size="14"/>下载目录</button><button v-if="canWrite" @click="renameHardware(row)"><Pencil :size="14"/>重命名</button><button v-if="canWrite" class="danger" @click="deleteHardware(row)"><Trash2 :size="14"/>删除</button></td></tr></tbody></table>
     <div v-if="loading" class="erp-empty">正在从 ERP 读取…</div><div v-else-if="!rows.length" class="erp-empty">没有符合条件的 ERP 记录。</div>
    </div>
    <footer class="erp-pagination"><span>共 {{total}} 条 · 第 {{page}} / {{pageCount}} 页</span><div><button class="erp-button quiet" :disabled="page<=1||loading" @click="previous">上一页</button><button class="erp-button quiet" :disabled="page>=pageCount||loading" @click="next">下一页</button></div></footer>
   </section>
  </div>
  <div v-if="detail!==null" class="erp-modal-shade" @click.self="detail=null"><section class="erp-modal"><header><div><h2>{{detailTitle}}</h2><p>以下内容实时读取自 ERP。</p></div><button class="erp-icon-button" @click="detail=null"><X :size="18"/></button></header><div v-if="detailLoading" class="erp-empty">正在加载详情…</div><template v-else><div class="erp-detail-grid"><div v-for="entry in primitiveEntries(detail)" :key="entry[0]"><span>{{label(String(entry[0]))}}</span><strong>{{text(entry[1])}}</strong></div></div><div v-if="detailRows(detail).length" class="erp-detail-lines"><h3>明细（{{detailRows(detail).length}}）</h3><article v-for="(item,index) in detailRows(detail)" :key="index"><strong>第 {{index+1}} 项</strong><span v-for="entry in primitiveEntries(item).slice(0,8)" :key="entry[0]">{{label(String(entry[0]))}}：{{text(entry[1])}}</span></article></div></template></section></div>
  <div v-if="compareResult" class="erp-modal-shade" @click.self="compareResult=null"><section class="erp-modal"><header><div><h2>图纸版本对比</h2><p>版本 {{selectedDrawingIds[0]}} 与 {{selectedDrawingIds[1]}}</p></div><button class="erp-icon-button" @click="compareResult=null"><X :size="18"/></button></header><div class="erp-detail-grid"><div v-for="entry in primitiveEntries(compareResult)" :key="entry[0]"><span>{{label(String(entry[0]))}}</span><strong>{{text(entry[1])}}</strong></div></div><div v-for="(items,key) in compareResult" :key="key"><div v-if="Array.isArray(items)" class="erp-detail-lines"><h3>{{label(String(key))}}（{{items.length}}）</h3><article v-for="(item,index) in items" :key="index"><span v-for="entry in primitiveEntries(item).slice(0,10)" :key="entry[0]">{{label(String(entry[0]))}}：{{text(entry[1])}}</span></article></div></div></section></div>
  <div v-if="densityEditor" class="erp-modal-shade" @click.self="densityEditor=null"><form class="erp-modal compact" @submit.prevent="saveDensity"><header><div><h2>{{densityEditor.id?'编辑材质密度':'新增材质密度'}}</h2><p>保存后将直接写入 D 盘 ERP。</p></div><button type="button" class="erp-icon-button" @click="densityEditor=null"><X :size="18"/></button></header><label class="erp-field"><span>材质</span><input v-model="densityEditor.materialMark" required maxlength="120"/></label><label class="erp-field"><span>密度</span><input v-model="densityEditor.density" required type="number" min="0.000001" max="100" step="0.000001"/></label><footer><button type="button" class="erp-button quiet" @click="densityEditor=null">取消</button><button class="erp-button primary" :disabled="saving">{{saving?'保存中…':'确认写入 ERP'}}</button></footer></form></div>
  <ErpDrawingPreview v-if="previewTarget" :row="previewTarget.row" :preview-url="previewTarget.url" @close="previewTarget=null"/>
 </main>
</template>

<style scoped>
.erp-workbench{min-height:100vh;background:var(--surface,#f6f7f9);color:var(--text,#17202a);display:flex;flex-direction:column}.erp-head{height:72px;padding:0 26px;border-bottom:1px solid var(--border,#dfe3e8);background:var(--panel,#fff);display:flex;align-items:center;justify-content:space-between}.erp-title{display:flex;align-items:center;gap:13px}.erp-title h1,.erp-content h2,.erp-modal h2{margin:0;font-size:20px}.erp-title p,.erp-content-title p,.erp-modal header p{margin:3px 0 0;color:var(--muted,#6b7280);font-size:12px}.erp-symbol{width:38px;height:38px;border-radius:11px;background:#e8f0ff;color:#2962d9;display:grid;place-items:center}.erp-icon-button{width:36px;height:36px;border:1px solid var(--border,#dfe3e8);border-radius:9px;background:var(--panel,#fff);color:inherit;display:grid;place-items:center;cursor:pointer}.erp-layout{display:grid;grid-template-columns:218px minmax(0,1fr);flex:1;min-height:0}.erp-nav{padding:20px 12px;border-right:1px solid var(--border,#dfe3e8);background:var(--panel,#fff);display:flex;flex-direction:column;gap:7px}.erp-nav button{text-align:left;border:0;border-radius:10px;background:transparent;padding:12px 13px;color:inherit;cursor:pointer}.erp-nav button:hover,.erp-nav button.active{background:#eef4ff;color:#1d55b7}.erp-nav strong,.erp-nav small{display:block}.erp-nav small{font-size:11px;margin-top:4px;color:var(--muted,#6b7280)}.erp-content{padding:25px 28px 34px;min-width:0;overflow:auto}.erp-content-title{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:18px}.erp-filters{display:flex;align-items:center;gap:9px;flex-wrap:wrap;background:var(--panel,#fff);border:1px solid var(--border,#dfe3e8);border-radius:12px;padding:12px;margin-bottom:12px}.erp-filters input,.erp-filters select,.erp-field input{height:36px;border:1px solid var(--border,#d4d9e0);background:var(--panel,#fff);color:inherit;border-radius:8px;padding:0 11px;outline:none;min-width:130px}.erp-filters input:focus,.erp-field input:focus{border-color:#4b7bec;box-shadow:0 0 0 3px #4b7bec1c}.erp-filters .short{width:80px;min-width:80px}.erp-search{display:flex;align-items:center;gap:7px;border:1px solid var(--border,#d4d9e0);border-radius:8px;padding-left:10px;background:var(--panel,#fff)}.erp-search input{border:0;min-width:235px;padding-left:0}.erp-button{height:36px;border:1px solid var(--border,#d4d9e0);border-radius:8px;padding:0 13px;background:var(--panel,#fff);color:inherit;display:inline-flex;align-items:center;justify-content:center;gap:6px;cursor:pointer}.erp-button.primary{background:#2d63d7;border-color:#2d63d7;color:white}.erp-button.quiet:hover{background:#f2f5f9}.erp-button:disabled{opacity:.48;cursor:not-allowed}.erp-filters .compare{margin-left:auto}.erp-notice,.erp-error{padding:10px 12px;border-radius:9px;font-size:13px;margin:10px 0}.erp-notice{background:#eaf8f0;color:#17633a}.erp-error{background:#fff0f0;color:#b42318}.erp-table-wrap{position:relative;overflow:auto;border:1px solid var(--border,#dfe3e8);border-radius:12px;background:var(--panel,#fff);min-height:330px}.erp-table{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}.erp-table th{position:sticky;top:0;z-index:1;background:#f6f8fb;color:#5d6673;text-align:left;font-weight:600}.erp-table th,.erp-table td{padding:11px 12px;border-bottom:1px solid var(--border,#e7e9ed);vertical-align:middle}.erp-table tbody tr:hover{background:#f8faff}.erp-table .path{max-width:270px;overflow:hidden;text-overflow:ellipsis}.erp-tag{display:inline-flex;background:#edf3ff;color:#2859b7;border-radius:99px;padding:3px 8px;font-size:12px}.actions{display:flex;align-items:center;gap:8px}.actions button{border:0;background:transparent;color:#245bc3;padding:3px 0;display:inline-flex;align-items:center;gap:3px;cursor:pointer}.actions button.danger{color:#c2342a}.erp-empty{padding:70px 20px;text-align:center;color:var(--muted,#6b7280)}.erp-table+.erp-empty{position:absolute;inset:43px 0 0;background:color-mix(in srgb,var(--panel,#fff) 88%,transparent)}.erp-pagination{display:flex;justify-content:space-between;align-items:center;color:var(--muted,#6b7280);font-size:12px;margin-top:12px}.erp-pagination>div{display:flex;gap:8px}.erp-modal-shade{position:fixed;inset:0;z-index:80;background:#11182770;display:grid;place-items:center;padding:28px}.erp-modal{width:min(900px,calc(100vw - 56px));max-height:calc(100vh - 56px);overflow:auto;border-radius:15px;background:var(--panel,#fff);box-shadow:0 25px 70px #0003;padding:20px}.erp-modal.compact{width:min(470px,calc(100vw - 40px))}.erp-modal header{display:flex;align-items:flex-start;justify-content:space-between;padding-bottom:15px;border-bottom:1px solid var(--border,#e3e6eb);margin-bottom:16px}.erp-detail-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.erp-detail-grid>div{border:1px solid var(--border,#e3e6eb);border-radius:9px;padding:10px;min-width:0}.erp-detail-grid span{display:block;color:var(--muted,#6b7280);font-size:11px;margin-bottom:5px}.erp-detail-grid strong{display:block;overflow-wrap:anywhere;font-size:13px}.erp-detail-lines{margin-top:18px}.erp-detail-lines h3{font-size:14px;margin:0 0 9px}.erp-detail-lines article{display:flex;gap:10px;flex-wrap:wrap;padding:10px 0;border-top:1px solid var(--border,#e3e6eb);font-size:12px}.erp-field{display:grid;gap:6px;margin:14px 0}.erp-field span{font-size:13px;font-weight:600}.erp-modal footer{display:flex;justify-content:flex-end;gap:9px;margin-top:20px}@media(max-width:900px){.erp-layout{grid-template-columns:1fr}.erp-nav{border-right:0;border-bottom:1px solid var(--border,#dfe3e8);flex-direction:row;overflow:auto}.erp-nav button{min-width:150px}.erp-content{padding:18px}.erp-detail-grid{grid-template-columns:1fr 1fr}.erp-head{padding:0 14px}.erp-title p{display:none}}
</style>
