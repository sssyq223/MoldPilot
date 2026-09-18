<script setup lang="ts">
import {computed} from 'vue'
import {workflowUi} from '@domain-pack/uiPolicy'
const props = defineProps<{ modelValue: any; contract?: any }>()
const emit = defineEmits<{ 'update:modelValue': [value: any] }>()
type Choice={value:string;label:string;field:any;table?:string;aggregate?:'SUM'|'COUNT'}
const choices=computed<Choice[]>(()=>{
  if(!props.contract)return (workflowUi.fields as Array<[string,string]>).map(([value,label])=>({value,label,field:{key:value,type:workflowUi.numericFields.includes(value)?'decimal':'text'}}))
  const result:Choice[]=(props.contract.fields||[]).map((field:any)=>({value:`field:${field.key}`,label:`表头 / ${field.label}`,field}))
  for(const table of props.contract.tables||[]){
    for(const field of table.fields||[]){
      result.push({value:`table:${table.key}:${field.key}`,label:`${table.label} / ${field.label}（任一明细）`,field,table:table.key})
      if(['decimal','money'].includes(field.type))result.push({value:`agg:${table.key}:SUM:${field.key}`,label:`${table.label} / ${field.label.endsWith('合计')?field.label:`${field.label}合计`}`,field,table:table.key,aggregate:'SUM'})
    }
    result.push({value:`agg:${table.key}:COUNT`,label:`${table.label} / 明细数量`,field:{key:'',type:'decimal'},table:table.key,aggregate:'COUNT'})
  }
  return result
})
const selectedValue=computed(()=>props.modelValue?.table&&props.modelValue?.aggregate?`agg:${props.modelValue.table}:${props.modelValue.aggregate}${props.modelValue.field?':'+props.modelValue.field:''}`:props.modelValue?.table&&props.modelValue?.condition?.field?`table:${props.modelValue.table}:${props.modelValue.condition.field}`:props.modelValue?.field?(props.contract?`field:${props.modelValue.field}`:props.modelValue.field):'')
const selectedChoice=computed(()=>choices.value.find(item=>item.value===selectedValue.value)||choices.value[0])
const leaf=()=>props.modelValue?.table&&props.modelValue?.condition?props.modelValue.condition:props.modelValue
const optionsFor=()=>props.contract?{}:workflowUi.selectOptions[leaf()?.field]||{}
function conditionFor(choice:Choice|undefined){
  if(!choice)return {field:workflowUi.defaultField,op:'eq',value:''}
  if(choice.table&&choice.aggregate)return {table:choice.table,aggregate:choice.aggregate,...(choice.aggregate==='COUNT'?{}:{field:choice.field.key}),op:'gt',value:''}
  const condition={field:choice.field.key,op:'eq',value:choice.field.type==='boolean'?false:''}
  return choice.table?{table:choice.table,quantifier:'ANY',condition}:condition
}
const newCondition=()=>conditionFor(choices.value[0])
function changeField(value:string) { emit('update:modelValue',conditionFor(choices.value.find(item=>item.value===value))) }
function setGroup(mode: string) {
  if (mode === 'single') emit('update:modelValue', newCondition())
  else emit('update:modelValue', {[mode]:[newCondition()]})
}
function mode() { return props.modelValue.all ? 'all' : props.modelValue.any ? 'any' : 'single' }
</script>
<template>
  <div class="form-stack rule-condition">
    <label>条件组合<select :value="mode()" @change="setGroup(($event.target as HTMLSelectElement).value)"><option value="single">单个条件</option><option value="all">全部满足</option><option value="any">任一满足</option></select></label>
    <template v-if="mode() !== 'single'">
      <div v-for="(_child,index) in modelValue[mode()]" :key="index" class="surface form-stack">
        <RuleEditor v-model="modelValue[mode()][index]" :contract="contract" />
        <button v-if="modelValue[mode()].length > 1" type="button" @click="modelValue[mode()].splice(index,1)">删除子条件</button>
      </div>
      <button type="button" @click="modelValue[mode()].push(newCondition())">添加子条件</button>
    </template>
    <div v-else class="form-grid">
      <label>判断字段<select :value="selectedValue" @change="changeField(($event.target as HTMLSelectElement).value)"><option v-for="item in choices" :key="item.value" :value="item.value">{{item.label}}</option></select></label>
      <label>比较方式<select v-model="leaf().op"><option value="eq">等于</option><option value="ne">不等于</option><template v-if="['decimal','money','date'].includes(selectedChoice?.field.type)"><option value="gt">大于</option><option value="gte">大于等于</option><option value="lt">小于</option><option value="lte">小于等于</option></template><option v-if="selectedChoice?.field.type==='text'" value="contains">包含</option><option value="in">属于列表</option></select></label>
      <label v-if="selectedChoice?.field.type==='boolean'">比较值<select v-model="leaf().value"><option :value="true">是</option><option :value="false">否</option></select></label>
      <label v-else-if="Object.keys(optionsFor()).length">比较值<select :multiple="leaf().op==='in'" v-model="leaf().value" required><option v-for="(label,code) in optionsFor()" :key="code" :value="code">{{label}}</option></select></label>
      <label v-else-if="leaf().op === 'in'">比较值（用逗号分隔）<input :value="Array.isArray(leaf().value) ? leaf().value.join('、') : leaf().value" @input="leaf().value=($event.target as HTMLInputElement).value.split(/[,，、]/).map(v=>v.trim())" required /></label>
      <label v-else>比较值<input v-model="leaf().value" :type="selectedChoice?.field.type==='date'?'date':'text'" required /></label>
      <label v-if="selectedChoice?.field.type==='money'">币种<input v-model="leaf().currency" maxlength="3" placeholder="CNY" required/></label>
    </div>
  </div>
</template>
