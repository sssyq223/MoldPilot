<script setup lang="ts">
import { ref } from 'vue'
import { ChevronDown } from 'lucide-vue-next'
import type { ErpDesignTechnicalRequirements } from '../erpDesignPreview'

defineProps<{
  result: ErpDesignTechnicalRequirements | null
}>()

const expanded = ref(true)
</script>

<template>
  <section v-if="result" class="erp-technical-requirements" :class="{collapsed:!expanded}" aria-label="ERP 钢料技术要求与公差表">
    <header>
      <button
        type="button"
        class="collapse-trigger"
        :aria-expanded="expanded"
        :title="expanded?'收起技术要求':'展开技术要求'"
        @click="expanded=!expanded"
      >
        <span class="title-group">
          <strong>技术要求</strong>
          <span>ERP 固定钢料技术指标</span>
        </span>
        <span class="toggle-meta">
          <small v-if="expanded">单位：mm</small>
          <small v-else>{{result.requirements.length}} 条要求 · {{result.toleranceRows.length}} 档公差 · 单位：mm</small>
          <ChevronDown :size="15" aria-hidden="true"/>
        </span>
      </button>
    </header>

    <div v-show="expanded" class="requirements-body">
      <ol v-if="result.requirements.length" class="requirement-list">
        <li v-for="(requirement, index) in result.requirements" :key="`${index}:${requirement}`">
          {{requirement}}
        </li>
      </ol>

      <div v-if="result.toleranceRows.length" class="tolerance-table-scroll">
        <table aria-label="ERP 钢料公差指标">
          <thead>
            <tr>
              <th>序号</th>
              <th>规格</th>
              <th>长度公差</th>
              <th>厚度公差</th>
              <th>对角公差</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in result.toleranceRows" :key="row.seq">
              <td>{{row.seq}}</td>
              <td>{{row.spec}}</td>
              <td>{{row.lengthTolerance}}</td>
              <td>{{row.thicknessTolerance}}</td>
              <td>{{row.diagonalTolerance}}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>

<style scoped>
.erp-technical-requirements{width:100%;max-width:100%;min-width:0;margin-top:12px;border:1px solid color-mix(in srgb,var(--accent) 18%,var(--border));border-radius:11px;background:var(--surface);overflow:hidden}
.erp-technical-requirements header{border-bottom:1px solid var(--border);background:color-mix(in srgb,var(--accent) 5%,var(--surface))}.erp-technical-requirements.collapsed header{border-bottom:0}
.collapse-trigger{display:flex;align-items:center;justify-content:space-between;gap:16px;width:100%;height:auto;padding:11px 13px;border:0;border-radius:0;background:transparent;color:var(--text);text-align:left;cursor:pointer}.collapse-trigger:hover{background:color-mix(in srgb,var(--accent) 7%,transparent)}.collapse-trigger:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.title-group{display:flex;align-items:baseline;gap:8px;min-width:0}.title-group strong{font-size:13px}.title-group>span,.toggle-meta small{color:var(--muted);font-size:11px}.toggle-meta{display:flex;align-items:center;gap:8px;flex:0 0 auto}.toggle-meta svg{color:var(--muted);transition:transform .18s ease}.collapsed .toggle-meta svg{transform:rotate(-90deg)}
.requirement-list{display:grid;gap:8px;margin:0;padding:14px 18px 14px 38px;font-size:13px;line-height:1.7}.requirement-list li{padding-left:3px;overflow-wrap:anywhere}.requirement-list li::marker{color:var(--muted);font-variant-numeric:tabular-nums}
.tolerance-table-scroll{width:100%;max-width:100%;overflow-x:auto;border-top:1px solid var(--border)}.tolerance-table-scroll table{width:100%;min-width:620px;border-collapse:collapse;font-size:12px}.tolerance-table-scroll th,.tolerance-table-scroll td{padding:10px 12px;border-right:1px solid var(--border);border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}.tolerance-table-scroll th:last-child,.tolerance-table-scroll td:last-child{border-right:0}.tolerance-table-scroll tbody tr:last-child td{border-bottom:0}.tolerance-table-scroll th{color:var(--muted);font-weight:500;background:color-mix(in srgb,var(--surface) 82%,var(--bg))}.tolerance-table-scroll th:first-child,.tolerance-table-scroll td:first-child{width:58px;text-align:center}.tolerance-table-scroll tbody tr:hover td{background:color-mix(in srgb,var(--accent) 5%,var(--surface))}
@media(max-width:700px){.collapse-trigger{align-items:flex-start}.title-group{align-items:flex-start;flex-direction:column;gap:2px}.toggle-meta small{display:none}.requirement-list{padding:12px 14px 12px 34px;font-size:12px}}
</style>
