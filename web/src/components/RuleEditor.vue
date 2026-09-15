<script setup lang="ts">
import {categoryNames,currencyNames} from '../uiText'
const props = defineProps<{ modelValue: any }>()
const emit = defineEmits<{ 'update:modelValue': [value: any] }>()
const fields = [
  ['quantity', '明细数量'], ['amount', '总金额'], ['currency', '币种'],
  ['category', '采购类别'], ['remark', '备注'], ['project_id', '项目标识'],
]
function changeField(field: string) {
  emit('update:modelValue', { field, op: 'eq', value: '' })
}
function setGroup(mode: string) {
  if (mode === 'single') emit('update:modelValue', {field:'quantity', op:'gt', value:''})
  else emit('update:modelValue', {[mode]:[{field:'quantity', op:'gt', value:''}]})
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
      <button type="button" @click="modelValue[mode()].push({field:'quantity',op:'gt',value:''})">添加子条件</button>
    </template>
    <div v-else class="form-grid">
      <label>判断字段<select :value="modelValue.field" @change="changeField(($event.target as HTMLSelectElement).value)"><option v-for="f in fields" :key="f[0]" :value="f[0]">{{f[1]}}</option></select></label>
      <label>比较方式<select v-model="modelValue.op"><option value="eq">等于</option><option value="ne">不等于</option><template v-if="['amount','quantity'].includes(modelValue.field)"><option value="gt">大于</option><option value="gte">大于等于</option><option value="lt">小于</option><option value="lte">小于等于</option></template><option value="in">属于列表</option></select></label>
      <label v-if="['category','currency'].includes(modelValue.field)">比较值<select :multiple="modelValue.op==='in'" v-model="modelValue.value" required><option v-for="(label,code) in modelValue.field==='category'?categoryNames:currencyNames" :key="code" :value="code">{{label}}</option></select></label>
      <label v-else-if="modelValue.op === 'in'">比较值（用逗号分隔）<input :value="Array.isArray(modelValue.value) ? modelValue.value.join('、') : modelValue.value" @input="modelValue.value=($event.target as HTMLInputElement).value.split(/[,，、]/).map(v=>v.trim())" required /></label>
      <label v-else>比较值<input v-model="modelValue.value" required /></label>
    </div>
  </div>
</template>
