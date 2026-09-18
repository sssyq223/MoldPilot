<script setup lang="ts">
import {Bot,Folder,ShoppingCart,ArrowRight} from 'lucide-vue-next'
defineProps<{capabilities:any}>()
const emit=defineEmits<{prompt:[value:string]}>()
</script>
<template><div class="welcome"><div class="agent-mark"><Bot :size="28"/></div><h1>今天，我们一起完成什么？</h1><p>描述你的目标，我会在你的权限范围内调用工具、<br/>核对资料，并把需要你决定的事项交给你。</p><div class="suggestions"><button v-if="capabilities.tools.some((t:any)=>t.key==='query_projects')" @click="emit('prompt','查询我有权限查看的项目及当前状态')"><Folder :size="17"/>查看我的项目<ArrowRight :size="14"/></button><button v-if="capabilities.tools.some((t:any)=>t.key==='query_purchase_requests')" @click="emit('prompt','查询我负责范围内的采购申请，核对明细和审批进度')"><ShoppingCart :size="17"/>核对采购申请<ArrowRight :size="14"/></button></div><small>从会话开始办理，待审批事项在消息通知中查看。</small></div></template>
