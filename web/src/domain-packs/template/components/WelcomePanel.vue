<script setup lang="ts">
import {Bot} from 'lucide-vue-next'
defineProps<{capabilities:any}>()
defineEmits<{prompt:[value:string]}>()
</script>
<template><div class="welcome"><div class="agent-mark"><Bot :size="28"/></div><h1>今天，我们一起完成什么？</h1><p>描述你的目标，我会在当前权限范围内调用能力、核对资料，并将需要决定的事项交给你。</p><small>从一个任务开始，执行记录会保存在当前会话。</small></div></template>
