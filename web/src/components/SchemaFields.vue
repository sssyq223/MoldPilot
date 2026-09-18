<script setup lang="ts">
import {labels,optionNames,resolve} from '@domain-pack/businessForms'
import {fieldName,currencyNames} from '@domain-pack/uiText'
const props=defineProps<{schema:any;root:any;options?:Record<string,any[]>}>()
const model=defineModel<any>({required:true})
function field(s:any){return resolve(s,props.root)}
function add(key:string,s:any){if(!model.value[key])model.value[key]=[];model.value[key].push(field(s.items).type==='object'?{}:'')}
function listValue(key:string,value:string){model.value[key]=value.split(',').map(v=>v.trim()).filter(Boolean)}
</script>
<template><div class="form-stack schema-fields"><template v-for="(raw,key) in schema.properties" :key="String(key)">
  <section v-if="field(raw).type==='array'" class="surface form-stack"><div class="section-heading"><strong>{{fieldName(String(key))}}</strong><button type="button" v-if="field(field(raw).items).type==='object'" @click="add(String(key),field(raw))">增加一项</button></div>
    <template v-if="field(field(raw).items).type==='object'"><div v-for="(_,index) in model[key]||[]" :key="index" class="form-stack"><SchemaFields v-model="model[key][index]" :schema="field(field(raw).items)" :root="root" :options="options"/><button type="button" @click="model[key].splice(index,1)">移除此项</button></div></template>
    <label v-else>使用英文逗号分隔<input :value="(model[key]||[]).join(',')" @input="listValue(String(key),($event.target as HTMLInputElement).value)"/></label>
  </section>
  <label v-else-if="field(raw).type==='boolean'" class="check-label"><input type="checkbox" v-model="model[key]"/>{{fieldName(String(key))}}</label>
  <label v-else>{{fieldName(String(key))}}<span v-if="schema.required?.includes(key)" class="muted small">必填</span>
    <select v-if="String(key)==='currency'" v-model="model[key]" :required="schema.required?.includes(key)"><option value="">请选择币种</option><option v-for="(label,code) in currencyNames" :key="code" :value="code">{{label}}</option></select>
    <select v-else-if="field(raw).enum" v-model="model[key]" :required="schema.required?.includes(key)"><option value="">请选择</option><option v-for="choice in field(raw).enum" :key="choice" :value="choice">{{optionNames[choice]||choice}}</option></select>
    <select v-else-if="options?.[String(key)]" v-model="model[key]" :required="schema.required?.includes(key)"><option value="">请选择</option><option v-for="item in options[String(key)]" :key="item.id" :value="item.id">{{item.label??item.name??item.number??item.code}}</option></select>
    <textarea v-else-if="['evidence','reason','problem','solution','condition','customer_evidence'].includes(String(key))" v-model="model[key]" rows="3" :required="schema.required?.includes(key)"/>
    <input v-else v-model="model[key]" :type="field(raw).format==='date'?'date':['amount','quantity','accepted_quantity','rejected_quantity','unit_price'].includes(String(key))?'number':'text'" step="any" :required="schema.required?.includes(key)" :placeholder="String(key)==='currency'?'例如 CNY':''"/>
  </label>
</template></div></template>
