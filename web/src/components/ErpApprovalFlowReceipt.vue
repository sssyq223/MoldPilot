<script setup lang="ts">
import { computed } from 'vue'
import { Check, Circle, Clock3, X } from 'lucide-vue-next'
import type { ErpDesignImportReceipt } from '../erpDesignPreview'

const props = defineProps<{ receipt: ErpDesignImportReceipt }>()

const normalizedStatus = computed(() => String(props.receipt.approvalStatus || 'PENDING').trim().toLowerCase())
const statusLabel = computed(() => {
  if (['approved', 'completed', 'passed'].includes(normalizedStatus.value)) return '已通过'
  if (['rejected', 'refused'].includes(normalizedStatus.value)) return '已驳回'
  return '审批中'
})
const currentIndexes = computed(() => {
  const steps = props.receipt.approvalSteps || []
  const currentNodeName = String(props.receipt.currentNodeName || '').trim()
  if (!currentNodeName) return [0]
  // ERP represents parallel approval groups as one display name, for
  // example “模具主管审批、总经理审批”. Mark every member of that group as
  // current instead of falling back to the first (design) node.
  const currentNames = currentNodeName.split(/[、,，]/).map(value => value.trim()).filter(Boolean)
  const indexes = steps
    .map((step, index) => currentNames.includes(String(step.nodeName || '').trim()) ? index : -1)
    .filter(index => index >= 0)
  if (indexes.length) return indexes
  const exactIndex = steps.findIndex(step => step.nodeName === currentNodeName)
  return exactIndex >= 0 ? [exactIndex] : [0]
})
const currentIndex = computed(() => currentIndexes.value[0] ?? 0)
function nodeState(index: number): 'complete' | 'current' | 'pending' | 'rejected' {
  if (['approved', 'completed', 'passed'].includes(normalizedStatus.value)) return 'complete'
  if (currentIndexes.value.includes(index) && ['rejected', 'refused'].includes(normalizedStatus.value)) return 'rejected'
  if (currentIndexes.value.includes(index)) return 'current'
  if (index < Math.min(...currentIndexes.value)) return 'complete'
  return 'pending'
}
function approverNames(step: { approverNames: string[] }, index: number): string {
  const names = step.approverNames?.length
    ? step.approverNames
    : (currentIndexes.value.includes(index) ? props.receipt.currentApproverNames || [] : [])
  return names.length ? names.join('、') : '审批人待 ERP 返回'
}
</script>

<template>
  <section class="erp-approval-receipt" aria-label="ERP 审批流程">
    <header class="erp-approval-head">
      <div>
        <strong>ERP 请购已创建</strong>
        <small>回执单号 {{ receipt.requestNo || 'ERP 未返回' }}</small>
      </div>
      <span class="erp-approval-status" :class="normalizedStatus">{{ statusLabel }}</span>
    </header>

    <div class="erp-approval-process">
      <span>审批流程</span>
      <strong>{{ receipt.processName || 'ERP 设计审批' }}</strong>
    </div>

    <div v-if="receipt.approvalSteps?.length" class="erp-approval-flow" role="list">
      <article class="erp-approval-node complete start" role="listitem">
        <div class="erp-approval-node-marker"><Check :size="14" /></div>
        <div class="erp-approval-node-copy">
          <strong>已发起</strong>
          <small>请购单进入审批流</small>
        </div>
      </article>
      <article
        v-for="(step, index) in receipt.approvalSteps"
        :key="`${step.nodeName}:${index}`"
        class="erp-approval-node"
        :class="nodeState(index)"
        role="listitem"
      >
        <div class="erp-approval-node-marker">
          <Check v-if="nodeState(index) === 'complete'" :size="14" />
          <X v-else-if="nodeState(index) === 'rejected'" :size="14" />
          <Clock3 v-else-if="nodeState(index) === 'current'" :size="14" />
          <Circle v-else :size="11" />
        </div>
        <div class="erp-approval-node-copy">
          <span v-if="nodeState(index) === 'current'" class="erp-approval-current">当前节点</span>
          <span v-else-if="nodeState(index) === 'rejected'" class="erp-approval-current rejected">已驳回</span>
          <strong>{{ step.nodeName }}</strong>
          <small>审批人：{{ approverNames(step, index) }}</small>
        </div>
      </article>
    </div>

    <div v-else class="erp-approval-empty">
      <Clock3 :size="16" />
      <span>当前节点：{{ receipt.currentNodeName || '等待 ERP 返回' }}</span>
      <small>审批人：{{ receipt.currentApproverNames?.join('、') || '等待 ERP 返回' }}</small>
    </div>
  </section>
</template>

<style scoped>
.erp-approval-receipt{width:100%;margin-top:4px;padding:16px;border:1px solid color-mix(in srgb,var(--accent) 24%,var(--border));border-radius:12px;background:color-mix(in srgb,var(--surface) 92%,var(--accent) 8%)}
.erp-approval-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin:0 0 13px}
.erp-approval-head>div{display:grid;gap:3px}
.erp-approval-head strong{font-size:14px;line-height:1.45;color:var(--text)}
.erp-approval-head small{font-size:11px;line-height:1.45;color:var(--muted)}
.erp-approval-status{flex:0 0 auto;padding:3px 9px;border:1px solid color-mix(in srgb,#d8a95d 46%,var(--border));border-radius:999px;background:color-mix(in srgb,#d8a95d 12%,transparent);color:#e0b467;font-size:11px;line-height:1.4}
.erp-approval-status.approved,.erp-approval-status.completed,.erp-approval-status.passed{border-color:color-mix(in srgb,#61b97a 48%,var(--border));background:color-mix(in srgb,#61b97a 12%,transparent);color:#72c68a}
.erp-approval-status.rejected,.erp-approval-status.refused{border-color:color-mix(in srgb,#d36e68 48%,var(--border));background:color-mix(in srgb,#d36e68 12%,transparent);color:#df817b}
.erp-approval-process{display:flex;align-items:baseline;gap:9px;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid color-mix(in srgb,var(--border) 68%,transparent)}
.erp-approval-process span{font-size:11px;color:var(--muted)}
.erp-approval-process strong{font-size:12px;color:var(--text)}
.erp-approval-flow{display:flex;align-items:flex-start;overflow-x:auto;padding:2px 2px 5px;scrollbar-width:thin}
.erp-approval-node{position:relative;display:grid;grid-template-columns:minmax(118px,1fr);grid-template-rows:24px auto;gap:8px;flex:1 0 150px;min-width:0;padding-right:22px}
.erp-approval-node:last-child{padding-right:0}
.erp-approval-node::after{content:'';position:absolute;z-index:0;top:11px;left:24px;right:0;height:2px;background:color-mix(in srgb,var(--border) 82%,transparent)}
.erp-approval-node:last-child::after{display:none}
.erp-approval-node.complete::after{background:color-mix(in srgb,#61b97a 55%,var(--border))}
.erp-approval-node-marker{position:relative;z-index:1;display:grid;place-items:center;width:24px;height:24px;border:2px solid color-mix(in srgb,var(--border) 90%,var(--muted));border-radius:50%;background:var(--surface);color:var(--muted)}
.erp-approval-node.complete .erp-approval-node-marker{border-color:#61b97a;background:#61b97a;color:#102217}
.erp-approval-node.current .erp-approval-node-marker{border-color:#d8a95d;background:color-mix(in srgb,#d8a95d 18%,var(--surface));color:#e2b76c;box-shadow:0 0 0 4px color-mix(in srgb,#d8a95d 10%,transparent)}
.erp-approval-node.rejected .erp-approval-node-marker{border-color:#d36e68;background:#d36e68;color:#251111}
.erp-approval-node-copy{position:relative;z-index:1;min-width:0;display:grid;align-content:start;gap:2px;padding-top:0}
.erp-approval-node-copy strong{font-size:12px;line-height:1.45;color:var(--text);overflow-wrap:anywhere}
.erp-approval-node-copy small{font-size:11px;line-height:1.45;color:var(--muted);overflow-wrap:anywhere}
.erp-approval-current{width:max-content;margin-bottom:2px;padding:1px 6px;border-radius:4px;background:color-mix(in srgb,#d8a95d 14%,transparent);color:#dfb469;font-size:10px;line-height:1.5}
.erp-approval-current.rejected{background:color-mix(in srgb,#d36e68 14%,transparent);color:#df817b}
.erp-approval-empty{display:grid;grid-template-columns:auto 1fr;align-items:center;gap:3px 8px;color:var(--text);font-size:12px}
.erp-approval-empty svg{grid-row:1/3;color:#d8a95d}
.erp-approval-empty small{color:var(--muted);font-size:11px}
@media(max-width:700px){.erp-approval-flow{display:grid;overflow:visible}.erp-approval-node{grid-template-columns:24px minmax(0,1fr);grid-template-rows:auto;min-height:72px;padding:0 0 18px;flex:none}.erp-approval-node-marker{grid-column:1;grid-row:1}.erp-approval-node-copy{grid-column:2;grid-row:1;padding-top:2px}.erp-approval-node:last-child{min-height:24px;padding-bottom:0}.erp-approval-node::after{top:24px;bottom:0;left:11px;right:auto;width:2px;height:auto}.erp-approval-head{align-items:center}}
</style>
