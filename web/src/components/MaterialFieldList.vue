<script setup lang="ts">
const props=defineProps<{fields:any[]}>()
const types:Record<string,string>={text:'文本',decimal:'数值',money:'金额／单价',boolean:'是／否',date:'日期'}
function add(){props.fields.push({key:'f_'+crypto.randomUUID().replaceAll('-',''),label:'',type:'text'})}
function change(f:any){if(f.type==='money')f.currency_field=props.fields.find(x=>x.type==='text'&&x.key!==f.key)?.key||'';else delete f.currency_field}
</script>
<template>
<div v-for="(f,i) in fields" :key="f.key" class="surface form-stack">
  <div class="section-heading"><strong>字段 {{i+1}}</strong><button type="button" @click="fields.splice(i,1)">删除字段</button></div>
  <label>字段名称<input v-model="f.label" required maxlength="100"/></label>
  <label>字段类型<select v-model="f.type" @change="change(f)"><option v-for="(name,key) in types" :key="key" :value="key">{{name}}</option></select></label>
  <label v-if="f.type==='decimal'">单位<input :value="f.unit||''" maxlength="30" @input="($event.target as HTMLInputElement).value?f.unit=($event.target as HTMLInputElement).value:delete f.unit"/></label>
  <label v-if="f.type==='money'">币种取自字段<select v-model="f.currency_field" required><option value="" disabled>请先配置一个币种文本字段</option><option v-for="c in fields.filter(x=>x.type==='text'&&x.key!==f.key)" :key="c.key" :value="c.key">{{c.label||'未命名文本字段'}}</option></select></label>
</div>
<button type="button" @click="add">增加字段</button>
</template>
