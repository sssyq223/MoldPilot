<script setup lang="ts">
defineProps<{value:any;highlightsOnly?:boolean}>()
</script>
<template><pre class="business-facts">{{JSON.stringify(value,null,2)}}</pre></template>
