<script setup lang="ts">
import { computed } from 'vue'
import { shanghai } from '../api'
import { erpDesignParameterCell, erpDesignReadOnlyTableNeedsDisclosure } from '../erpDesignPreview'
import { erpDesignGroupKeywordColumns, type ErpDesignGroupKeywords } from '../erpDesignGroupKeywords'
import ErpReadOnlyDetailCard from './ErpReadOnlyDetailCard.vue'

const props = defineProps<{ result: ErpDesignGroupKeywords }>()
const summary = computed(() => `共 ${props.result.total} 条 · 第 ${props.result.pageNum} 页，已返回 ${props.result.rows.length} 条${props.result.hasNext ? ' · 还有下一页' : ''}`)
const sourceNote = computed(() => `ERP 分组关键词${props.result.asOf ? ` · 查询于 ${shanghai(props.result.asOf)}` : ''}`)
</script>

<template>
  <ErpReadOnlyDetailCard
    title="ERP 分组关键词"
    ready-title="ERP 分组关键词表已就绪"
    :context="result.keywordText ? `筛选：${result.keywordText}` : ''"
    :summary="summary"
    :source-note="sourceNote"
    table-label="ERP 分组关键词表"
    empty-text="ERP 未找到匹配的分组关键词。"
    view-label="查看关键词表"
    :rows="result.rows"
    :columns="erpDesignGroupKeywordColumns"
    :cell="erpDesignParameterCell"
    :defer="erpDesignReadOnlyTableNeedsDisclosure(result.rows.length)"
  />
</template>
