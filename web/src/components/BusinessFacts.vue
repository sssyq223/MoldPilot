<script setup lang="ts">
import {fieldName,valueText} from '../uiText'
defineProps<{value:any}>()
const hidden=new Set(['id','subject_id','plan_id','created_at','source_line_id','case_id','department_id','plan_subject_id','source_pause_subject_id','pause_id','task_id'])

</script>
<template><div class="business-facts"><template v-for="(item,key) in value" :key="String(key)"><section v-if="!hidden.has(String(key))">
  <template v-if="Array.isArray(item)"><h4>{{fieldName(String(key))}}</h4><div v-for="(row,index) in item" :key="index" class="surface"><BusinessFacts v-if="row&&typeof row==='object'" :value="row"/><span v-else>{{valueText(String(key),row)}}</span></div><p v-if="!item.length" class="muted">暂无记录</p></template>
  <BusinessFacts v-else-if="item&&typeof item==='object'" :value="item"/>
  <div v-else class="fact-line"><span class="muted">{{fieldName(String(key))}}</span><span>{{valueText(String(key),item)}}</span></div>
</section></template></div></template>
