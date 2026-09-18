<script setup lang="ts">
defineProps<{initialId?:string}>()
defineEmits<{error:[message:string];approval:[id:string]}>()
</script>
<template><section class="surface"><p class="muted">当前业务包未安装协作面板。</p></section></template>
