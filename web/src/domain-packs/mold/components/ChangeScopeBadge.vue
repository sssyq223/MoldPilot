<template>
  <span :class="['change-scope-badge', scopeClass]">
    <span class="badge-icon">{{ icon }}</span>
    <span class="badge-text">{{ label }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  isMinor: boolean
  showIcon?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  showIcon: true
})

const scopeClass = computed(() => props.isMinor ? 'minor' : 'major')

const label = computed(() => props.isMinor ? '小范围设变' : '重大设变')

const icon = computed(() => {
  if (!props.showIcon) return ''
  return props.isMinor ? '●' : '▲'
})
</script>

<style scoped>
.change-scope-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}

.change-scope-badge.minor {
  background: #D1FAE5;
  color: #065F46;
}

.change-scope-badge.major {
  background: #FEF3C7;
  color: #92400E;
}

.badge-icon {
  font-size: 8px;
  line-height: 1;
}

.badge-text {
  line-height: 1;
}
</style>
