<script setup lang="ts">
import {workflowUi} from '@domain-pack/uiPolicy'
const props = defineProps<{ modelValue: any }>()
const emit = defineEmits<{ 'update:modelValue': [value: any] }>()
const fields=workflowUi.fields as Array<[string,string]>
const optionsFor=(field:string)=>workflowUi.selectOptions[field]||{}
const newCondition=()=>({field:workflowUi.defaultField,op:'gt',value:''})
function changeField(field: string) {
  emit('update:modelValue', { field, op: 'eq', value: '' })
}
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
      <div v-for="(child,index) in modelValue[mode()]" :key="index" class="surface form-stack">
        <RuleEditor v-model="modelValue[mode()][index]" />
        <button v-if="modelValue[mode()].length > 1" type="button" @click="modelValue[mode()].splice(index,1)">删除子条件</button>
      </div>
      <button type="button" @click="modelValue[mode()].push(newCondition())">添加子条件</button>
    </template>
    <div v-else class="form-grid">
      <label>判断字段<select :value="modelValue.field" @change="changeField(($event.target as HTMLSelectElement).value)"><option v-for="f in fields" :key="f[0]" :value="f[0]">{{f[1]}}</option></select></label>
      <label>比较方式<select v-model="modelValue.op"><option value="eq">等于</option><option value="ne">不等于</option><template v-if="workflowUi.numericFields.includes(modelValue.field)"><option value="gt">大于</option><option value="gte">大于等于</option><option value="lt">小于</option><option value="lte">小于等于</option></template><option value="in">属于列表</option></select></label>
      <label v-if="Object.keys(optionsFor(modelValue.field)).length">比较值<select :multiple="modelValue.op==='in'" v-model="modelValue.value" required><option v-for="(label,code) in optionsFor(modelValue.field)" :key="code" :value="code">{{label}}</option></select></label>
      <label v-else-if="modelValue.op === 'in'">比较值（用逗号分隔）<input :value="Array.isArray(modelValue.value) ? modelValue.value.join('、') : modelValue.value" @input="modelValue.value=($event.target as HTMLInputElement).value.split(/[,，、]/).map(v=>v.trim())" required /></label>
      <label v-else>比较值<input v-model="modelValue.value" required /></label>
    </div>
  </div>
</template>
