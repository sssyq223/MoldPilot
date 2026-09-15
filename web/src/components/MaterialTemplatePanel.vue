<script setup lang="ts">
import {ref} from 'vue'
import {api,post} from '../api'
import MaterialFieldList from './MaterialFieldList.vue'
const emit=defineEmits<{changed:[];error:[message:string]}>()
const opened=ref(false),templates=ref<any[]>([]),editor=ref<any>(null),selected=ref<any>(null),busy=ref(false),notice=ref('')
const types:Record<string,string>={text:'文本',decimal:'数值',money:'金额／单价',boolean:'是／否',date:'日期'}
async function load(){const all:any[]=[];for(let offset=0;;offset+=100){const page=await api(`/material-templates?offset=${offset}`);all.push(...page);if(page.length<100)break}templates.value=all}
async function toggle(){opened.value=!opened.value;if(opened.value)try{await load()}catch(e:any){emit('error',e.message)}}
function edit(t?:any,copy=false){selected.value=null;editor.value=t?JSON.parse(JSON.stringify({...t,id:copy?null:t.id})):{id:null,template_key:'materials_'+crypto.randomUUID().replaceAll('-',''),name:'',contract:{fields:[],tables:[]}}}
function addTable(){editor.value.contract.tables.push({key:'t_'+crypto.randomUUID().replaceAll('-',''),label:'',fields:[]})}
async function save(){busy.value=true;try{const t=editor.value;const result=t.id?await api(`/material-templates/${t.id}`,{method:'PUT',body:JSON.stringify({name:t.name,contract:t.contract,expected_hash:t.edit_hash})}):await post('/material-templates',{template_key:t.template_key,name:t.name,contract:t.contract});editor.value=null;selected.value=result;notice.value=`资料模板第 ${result.version} 版草稿已保存`;await load();emit('changed')}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
async function publish(t:any){busy.value=true;try{selected.value=await post(`/material-templates/${t.id}/publish`);notice.value=`资料模板第 ${t.version} 版已发布，原版本保持不变`;await load();emit('changed')}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
</script>
<template>
<button class="workflow-tool-button" @click="toggle">{{opened?'收起资料模板':'管理资料模板'}}</button>
<section v-if="opened" class="surface form-stack" aria-label="资料模板管理">
  <div class="section-heading"><h3>资料模板与字段版本</h3><button @click="edit()">新增资料模板</button></div>
  <p class="muted">设计清单、核算清单等可分别维护。表头字段描述整份资料，明细表字段描述每一行。已发布版本可被多个审批模板复用。</p>
  <p v-if="notice" role="status">{{notice}}</p>
  <form v-if="editor" class="form-stack" @submit.prevent="save">
    <label>资料模板名称<input v-model="editor.name" required maxlength="150"/></label>
    <fieldset><legend>表头字段</legend><MaterialFieldList :fields="editor.contract.fields"/></fieldset>
    <section v-for="(t,i) in editor.contract.tables" :key="t.key" class="surface form-stack">
      <div class="section-heading"><h4>明细表 {{Number(i)+1}}</h4><button type="button" @click="editor.contract.tables.splice(i,1)">删除明细表</button></div>
      <label>明细表名称<input v-model="t.label" required maxlength="100"/></label><MaterialFieldList :fields="t.fields"/>
    </section>
    <button type="button" @click="addTable">增加明细表</button>
    <div class="actions"><button type="button" @click="editor=null">取消资料编辑</button><button class="primary" :disabled="busy">{{editor.id?'保存资料草稿':'保存资料新版本'}}</button></div>
  </form>
  <section v-if="selected" class="surface form-stack" aria-label="资料模板详情">
    <h4>{{selected.name}} · 第 {{selected.version}} 版 · {{selected.status==='PUBLISHED'?'已发布':'草稿'}}</h4>
    <p v-if="!selected.contract.fields.length" class="muted">未配置表头字段</p><p v-for="f in selected.contract.fields" :key="f.key">{{f.label}} · {{types[f.type]}}{{f.unit?' · '+f.unit:''}}</p>
    <div v-for="t in selected.contract.tables" :key="t.key"><strong>{{t.label}}</strong><p v-for="f in t.fields" :key="f.key">{{f.label}} · {{types[f.type]}}{{f.unit?' · '+f.unit:''}}</p></div>
    <div class="actions"><button @click="edit(selected,selected.status==='PUBLISHED')">{{selected.status==='PUBLISHED'?'复制资料新版本':'修改资料草稿'}}</button><button v-if="selected.status==='DRAFT'" :disabled="busy" @click="publish(selected)">发布资料模板</button><button @click="selected=null">关闭资料详情</button></div>
  </section>
  <div v-for="t in templates" :key="t.id" class="grant-row"><span>{{t.name}} · 第 {{t.version}} 版 · {{t.status==='PUBLISHED'?'已发布':'草稿'}}</span><button @click="selected=t;editor=null">查看资料版本</button></div>
</section>
</template>
