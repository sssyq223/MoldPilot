<script setup lang="ts">
import BusinessFacts from './BusinessFacts.vue'
defineProps<{detail:any}>()
</script>
<template><section class="surface"><h3>提交审批的业务材料</h3><BusinessFacts :value="detail.snapshot.detail"/></section></template>
