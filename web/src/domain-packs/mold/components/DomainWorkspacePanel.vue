<script setup lang="ts">
import ContactPanel from './ContactPanel.vue'
defineProps<{panel:string;targetId?:string}>()
const emit=defineEmits<{approval:[id:string];error:[message:string];open:[key:string]}>()
</script>
<template>
 <ContactPanel v-if="panel==='contacts'" :key="targetId" :initial-id="targetId||''" @approval="emit('approval',$event)" @error="emit('error',$event)"/>
 <div v-else class="empty workspace-empty"><h3>{{panel==='approvals'?'暂无审批材料':'材料总览'}}</h3><p>{{panel==='approvals'?'从消息通知或会话中的审批建议打开具体审批，节点、依据和操作记录会显示在这里。':'从会话结果选择业务材料后，工作区会自动切换到对应内容。'}}</p><div class="workspace-empty-cards"><button type="button" :class="{active:panel==='approvals'}" @click="emit('open','approvals')"><strong>审批材料</strong><small>待审批事项、流程节点、附件和审批记录</small></button><button type="button" :class="{active:panel==='contacts'}" @click="emit('open','contacts')"><strong>联络单材料</strong><small>工程联络单、处理方案、附件和协作进度</small></button></div></div>
</template>
