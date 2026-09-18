<script setup lang="ts">
import {computed,ref} from 'vue'
import {FileSpreadsheet, Upload} from 'lucide-vue-next'
import {api,post} from '../api'
import MaterialFieldList from './MaterialFieldList.vue'
const emit=defineEmits<{changed:[];error:[message:string]}>()
const opened=ref(false),templates=ref<any[]>([]),editor=ref<any>(null),selected=ref<any>(null),busy=ref(false),notice=ref('')
const importing=ref(false),importResult=ref<any>(null),pendingMapping=ref<any>(null)
const previewSheetKey=ref('')
const types:Record<string,string>={text:'文本',decimal:'数值',money:'金额／单价',boolean:'是／否',date:'日期'}
const previewSheets=computed(()=>selected.value?[...((selected.value.contract.fields||[]).length?[{key:'__header',label:'表头字段',fields:selected.value.contract.fields}]:[]),...(selected.value.contract.tables||[])]:[])
const activePreviewSheet=computed(()=>previewSheets.value.find((sheet:any)=>sheet.key===previewSheetKey.value)||previewSheets.value[0])
async function load(){const all:any[]=[];for(let offset=0;;offset+=100){const page=await api(`/material-templates?offset=${offset}`);all.push(...page);if(page.length<100)break}templates.value=all}
async function toggle(){opened.value=!opened.value;if(opened.value)try{await load()}catch(e:any){emit('error',e.message)}}
function beginImport(){selected.value=null;editor.value=null;importResult.value=null;pendingMapping.value=null;importing.value=true}
function edit(t:any,copy=false){importing.value=false;importResult.value=null;pendingMapping.value=null;selected.value=null;editor.value=JSON.parse(JSON.stringify({...t,id:copy?null:t.id,template_key:copy?`materials_${crypto.randomUUID().replaceAll('-','')}`:t.template_key}))}
function showTemplate(t:any){selected.value=t;editor.value=null;importing.value=false;previewSheetKey.value=(t.contract.fields||[]).length?'__header':t.contract.tables?.[0]?.key||''}
function excelColumn(index:number){let value=index+1,result='';while(value){value--;result=String.fromCharCode(65+value%26)+result;value=Math.floor(value/26)}return result}
function exampleValue(field:any,row:number){
  const label=String(field.label||''),number=row+1
  if(field.type==='boolean')return row%2?'否':'是'
  if(field.type==='date')return `2026-09-${String(18+row).padStart(2,'0')}`
  if(field.type==='decimal'||field.type==='money')return field.type==='money'?`${(number*1000).toFixed(2)}`:String(number)
  if(/币种/.test(label))return'CNY'
  if(/编号|代码|编码|料号/.test(label))return`SAMPLE-${String(number).padStart(3,'0')}`
  if(/名称|品名/.test(label))return`示例名称 ${number}`
  if(/规格|型号/.test(label))return`规格-${number}`
  if(/品牌/.test(label))return'示例品牌'
  if(/备注|说明/.test(label))return row===0?'示例数据':''
  return`示例 ${number}`
}
function addTable(){editor.value.contract.tables.push({key:'t_'+crypto.randomUUID().replaceAll('-',''),label:'',fields:[]})}
async function importExcel(event:Event){
  const input=event.target as HTMLInputElement,file=input.files?.[0];input.value=''
  if(!file)return
  if(!file.name.toLowerCase().endsWith('.xlsx'))return emit('error','请选择 XLSX 格式的 Excel 文件')
  busy.value=true
  try{
    const uploaded=await api(`/files?filename=${encodeURIComponent(file.name)}&request_key=${crypto.randomUUID()}`,{method:'POST',body:file,headers:{'Content-Type':file.type||'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}})
    const inferred=await post('/material-templates/infer-xlsx',{file_id:uploaded.id})
    importResult.value=inferred
    editor.value={id:null,template_key:'materials_'+crypto.randomUUID().replaceAll('-',''),name:inferred.name,contract:inferred.contract}
    pendingMapping.value={templateId:null,name:`${inferred.name} 默认 Excel 映射`,mapping:inferred.mapping}
    importing.value=false
    notice.value=`已从 ${file.name} 识别 ${inferred.contract.tables.length} 张明细表、${inferred.contract.tables.reduce((n:number,t:any)=>n+t.fields.length,0)} 个字段，请核对后保存`
  }catch(e:any){emit('error',e.message)}finally{busy.value=false}
}
async function save(){busy.value=true;try{const t=editor.value;const result=t.id?await api(`/material-templates/${t.id}`,{method:'PUT',body:JSON.stringify({name:t.name,contract:t.contract,expected_hash:t.edit_hash})}):await post('/material-templates',{template_key:t.template_key,name:t.name,contract:t.contract});if(pendingMapping.value)pendingMapping.value.templateId=result.id;showTemplate(result);notice.value=`资料模板第 ${result.version} 版草稿已保存`;await load();emit('changed')}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
function mappingForContract(mapping:any,contract:any){
  const fieldKeys=new Set((contract.fields||[]).map((field:any)=>field.key))
  const fields=Object.fromEntries(Object.entries(mapping.fields||{}).filter(([key])=>fieldKeys.has(key)))
  const tableDefs=new Map<string,Set<string>>((contract.tables||[]).map((table:any)=>[table.key,new Set<string>(table.fields.map((field:any)=>field.key))]))
  const tables=Object.fromEntries(Object.entries(mapping.tables||{}).filter(([key])=>tableDefs.has(key)).map(([key,value]:[string,any])=>{
    const columns=Object.fromEntries(Object.entries(value.columns||{}).filter(([field])=>tableDefs.get(key)?.has(field)))
    return [key,{...value,columns}]
  }))
  return {...(mapping.sheet?{sheet:mapping.sheet}:{}),...(Object.keys(fields).length?{fields}:{}),tables}
}
async function publish(t:any){busy.value=true;try{const published=await post(`/material-templates/${t.id}/publish`);const mappingPublished=pendingMapping.value?.templateId===t.id;if(mappingPublished&&pendingMapping.value){await post(`/material-templates/${t.id}/xlsx-mappings`,{name:pendingMapping.value.name,mapping:mappingForContract(pendingMapping.value.mapping,published.contract),expected_template_hash:published.package_hash});pendingMapping.value=null}showTemplate(published);notice.value=mappingPublished?`资料模板第 ${t.version} 版及 Excel 字段映射已发布`:`资料模板第 ${t.version} 版已发布`;await load();emit('changed')}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
</script>
<template>
<div class="workflow-tool-group workflow-material-tool">
<button class="workflow-tool-button" @click="toggle"><FileSpreadsheet :size="14"/>{{opened?'收起资料模板':'管理资料模板'}}</button>
<section v-if="opened" class="surface form-stack workflow-tool-panel material-template-panel" aria-label="资料模板管理">
  <div class="section-heading"><div><h3>Excel 资料模板</h3><p class="muted workflow-tool-help">上传传统 Excel，识别结果经核对发布后即可用于审批条件。</p></div><button class="material-import-trigger" @click="beginImport"><FileSpreadsheet :size="15"/>新增资料模板</button></div>
  <p v-if="notice" role="status" class="material-import-notice">{{notice}}</p>
  <section v-if="importing" class="material-import-box">
    <FileSpreadsheet :size="28"/>
    <div><strong>选择 Excel 原件</strong><p>系统将识别工作表、表头行、明细列和基础数据类型，不执行公式、宏或外部链接。</p></div>
    <label class="material-file-button" :class="{disabled:busy}"><Upload :size="15"/>{{busy?'正在解析…':'选择 XLSX 文件'}}<input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" :disabled="busy" @change="importExcel"/></label>
    <button type="button" @click="importing=false">取消</button>
  </section>
  <form v-if="editor" class="form-stack material-contract-editor" @submit.prevent="save">
    <div v-if="importResult" class="material-import-summary">
      <strong><FileSpreadsheet :size="15"/>{{importResult.file.filename}}</strong>
      <span v-for="sheet in importResult.sheets.filter((s:any)=>s.status==='PARSED')" :key="sheet.name">{{sheet.name}} · 表头第 {{sheet.header_row}} 行 · {{sheet.field_count}} 个字段</span>
      <p v-for="warning in importResult.warnings" :key="warning" class="warning">{{warning}}</p>
    </div>
    <label>资料模板名称<input v-model="editor.name" required maxlength="150"/></label>
    <fieldset v-if="editor.contract.fields.length"><legend>表头字段</legend><MaterialFieldList :fields="editor.contract.fields"/></fieldset>
    <section v-for="(t,i) in editor.contract.tables" :key="t.key" class="surface form-stack material-table-editor">
      <div class="section-heading"><h4>明细表 {{Number(i)+1}}</h4><button type="button" @click="editor.contract.tables.splice(i,1)">删除明细表</button></div>
      <label>明细表名称<input v-model="t.label" required maxlength="100"/></label><MaterialFieldList :fields="t.fields"/>
    </section>
    <button type="button" @click="addTable">增加明细表</button>
    <p class="muted small">字段类型由样本值推断。请重点核对数量、金额、日期和“是／否”字段；发布后审批条件将直接使用这里的字段。</p>
    <div class="actions"><button type="button" @click="editor=null;importResult=null">取消资料编辑</button><button class="primary" :disabled="busy">{{editor.id?'保存资料草稿':'保存资料新版本'}}</button></div>
  </form>
  <div class="material-template-grid">
    <div v-for="t in templates" :key="t.id" class="grant-row"><span>{{t.name}} · 第 {{t.version}} 版 · {{t.status==='PUBLISHED'?'已发布':'草稿'}}</span><button @click="showTemplate(t)">查看资料版本</button></div>
  </div>
</section>
</div>
<Teleport to="body"><div v-if="selected" class="modal-shade material-preview-shade" @click.self="selected=null">
  <section class="modal material-preview-modal" role="dialog" aria-modal="true" aria-label="资料模板详情">
    <div class="section-heading material-preview-heading"><div><h4>{{selected.name}}</h4><p>第 {{selected.version}} 版 · {{selected.status==='PUBLISHED'?'已发布':'草稿'}} · {{previewSheets.length}} 张工作表</p></div><button class="icon-button" type="button" aria-label="关闭资料模板预览" @click="selected=null">×</button></div>
    <div v-if="activePreviewSheet" class="material-excel-preview">
      <div class="material-excel-bar"><span>fx</span><strong>{{activePreviewSheet.label}}</strong><small>示例数据仅展示字段结构，不参与审批</small></div>
      <div class="material-excel-scroll">
        <table class="material-excel-grid">
          <thead><tr class="material-excel-columns"><th class="material-excel-corner"></th><th v-for="(_field,index) in activePreviewSheet.fields" :key="index">{{excelColumn(Number(index))}}</th></tr><tr><th class="material-excel-row-number">1</th><th v-for="field in activePreviewSheet.fields" :key="field.key"><strong>{{field.label}}</strong><small>{{types[field.type]}}{{field.unit?' · '+field.unit:''}}</small></th></tr></thead>
          <tbody><tr v-for="row in 3" :key="row"><th class="material-excel-row-number">{{row+1}}</th><td v-for="field in activePreviewSheet.fields" :key="field.key">{{exampleValue(field,row-1)}}</td></tr></tbody>
        </table>
      </div>
      <div class="material-excel-tabs"><button v-for="sheet in previewSheets" :key="sheet.key" type="button" :class="{active:activePreviewSheet.key===sheet.key}" @click="previewSheetKey=sheet.key">{{sheet.label}}</button><span>{{activePreviewSheet.fields.length}} 列</span></div>
    </div>
    <p v-else class="muted">当前模板没有可预览的字段。</p>
    <div class="actions material-preview-actions"><button type="button" @click="selected=null">关闭</button><button @click="edit(selected,selected.status==='PUBLISHED')">{{selected.status==='PUBLISHED'?'复制资料新版本':'修改资料草稿'}}</button><button v-if="selected.status==='DRAFT'" :disabled="busy" @click="publish(selected)">发布资料模板</button></div>
  </section>
</div></Teleport>
</template>
