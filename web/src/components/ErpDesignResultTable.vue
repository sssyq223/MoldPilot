<script setup lang="ts">
import ErpReadOnlyDetailCard from './ErpReadOnlyDetailCard.vue'
import { erpDesignResultCell, type ErpDesignResultTable } from '../erpDesignResultTables'

defineProps<{ result: ErpDesignResultTable }>()
</script>

<template>
  <ErpReadOnlyDetailCard
    :title="result.title"
    :ready-title="`${result.title}已就绪`"
    :context="result.context"
    :summary="result.summary"
    :source-note="result.sourceNote"
    :table-label="result.title"
    empty-text="ERP 未返回可展示的记录。"
    view-label="查看完整表格"
    :rows="result.rows"
    :columns="result.columns"
    :cell="result.renderCell ?? erpDesignResultCell"
    :defer="result.defer"
  />
</template>
