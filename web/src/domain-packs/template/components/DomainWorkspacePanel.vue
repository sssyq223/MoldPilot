<script setup lang="ts">
defineProps<{panel:string;targetId?:string}>()
const emit=defineEmits<{approval:[id:string];error:[message:string];open:[key:string]}>()
</script>
<template><div class="empty workspace-empty"><h3>暂无审批材料</h3><p>从消息通知或会话中的审批建议打开具体审批，节点、依据和操作记录会显示在这里。</p><div class="workspace-empty-cards"><button type="button" :class="{active:panel==='approvals'}" @click="emit('open','approvals')"><strong>审批材料</strong><small>待审批事项、流程节点、附件和审批记录</small></button></div></div></template>
