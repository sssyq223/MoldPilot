<template>
  <div class="rejection-list">
    <div class="rejection-list-header">
      <h3>报价拒绝记录</h3>
      <div class="filters">
        <select v-model="filters.category" @change="loadRejections" class="filter-select">
          <option value="">全部类别</option>
          <option value="PRICE_TOO_LOW">价格过低</option>
          <option value="TIMELINE_IMPOSSIBLE">交期不可行</option>
          <option value="TECHNICAL_DIFFICULTY">技术难度过高</option>
          <option value="CAPACITY_SHORTAGE">产能不足</option>
          <option value="CUSTOMER_CREDIT">客户信誉问题</option>
          <option value="MATERIAL_SHORTAGE">材料缺货</option>
          <option value="RESOURCE_CONFLICT">资源冲突</option>
          <option value="PROFIT_MARGIN_LOW">利润率过低</option>
          <option value="OTHER">其他原因</option>
        </select>

        <input type="date" v-model="filters.dateFrom" @change="loadRejections" class="filter-date" placeholder="开始日期" />
        <input type="date" v-model="filters.dateTo" @change="loadRejections" class="filter-date" placeholder="结束日期" />

        <button @click="resetFilters" class="btn-reset">重置筛选</button>
      </div>
    </div>

    <div v-if="loading" class="loading">加载中...</div>

    <div v-else-if="error" class="error">{{ error }}</div>

    <div v-else-if="rejections.length === 0" class="empty">暂无拒绝记录</div>

    <div v-else class="rejection-table-container">
      <table class="rejection-table">
        <thead>
          <tr>
            <th>拒绝单号</th>
            <th>报价单号</th>
            <th>拒绝类别</th>
            <th>拒绝原因</th>
            <th>决策日期</th>
            <th>决策人</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="rejection in rejections" :key="rejection.id" @click="viewDetails(rejection)" class="rejection-row">
            <td>{{ rejection.rejection_number }}</td>
            <td>{{ rejection.quotation_number }}</td>
            <td>
              <span :class="['category-badge', getCategoryClass(rejection.rejection_category)]">
                {{ getCategoryLabel(rejection.rejection_category) }}
              </span>
            </td>
            <td class="reason-cell">{{ truncateReason(rejection.rejection_reason) }}</td>
            <td>{{ formatDate(rejection.decision_date) }}</td>
            <td>{{ rejection.decision_maker_name || '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="rejections.length > 0" class="pagination">
      <button @click="prevPage" :disabled="currentPage === 1" class="btn-page">上一页</button>
      <span class="page-info">第 {{ currentPage }} 页</span>
      <button @click="nextPage" :disabled="rejections.length < pageSize" class="btn-page">下一页</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

interface QuotationRejection {
  id: string
  rejection_subject_id: string
  rejection_number: string
  quotation_subject_id: string
  quotation_number: string
  rejection_category: string
  rejection_reason: string
  decision_date: string
  decision_maker_id: string
  decision_maker_name: string
  evidence: string | null
}

const rejections = ref<QuotationRejection[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const currentPage = ref(1)
const pageSize = ref(20)

const filters = ref({
  category: '',
  dateFrom: '',
  dateTo: ''
})

const CATEGORY_LABELS: Record<string, string> = {
  PRICE_TOO_LOW: '价格过低',
  TIMELINE_IMPOSSIBLE: '交期不可行',
  TECHNICAL_DIFFICULTY: '技术难度过高',
  CAPACITY_SHORTAGE: '产能不足',
  CUSTOMER_CREDIT: '客户信誉问题',
  MATERIAL_SHORTAGE: '材料缺货',
  RESOURCE_CONFLICT: '资源冲突',
  PROFIT_MARGIN_LOW: '利润率过低',
  OTHER: '其他原因'
}

async function loadRejections() {
  loading.value = true
  error.value = null

  try {
    const params = new URLSearchParams({
      page: currentPage.value.toString(),
      page_size: pageSize.value.toString()
    })

    if (filters.value.category) {
      params.append('category', filters.value.category)
    }
    if (filters.value.dateFrom) {
      params.append('date_from', filters.value.dateFrom)
    }
    if (filters.value.dateTo) {
      params.append('date_to', filters.value.dateTo)
    }

    const response = await fetch(`/api/mold/quotation-rejections?${params}`)
    if (!response.ok) {
      throw new Error('Failed to load rejections')
    }

    const data = await response.json()
    rejections.value = data.items || []
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  filters.value = {
    category: '',
    dateFrom: '',
    dateTo: ''
  }
  currentPage.value = 1
  loadRejections()
}

function getCategoryLabel(category: string): string {
  return CATEGORY_LABELS[category] || category
}

function getCategoryClass(category: string): string {
  const classMap: Record<string, string> = {
    PRICE_TOO_LOW: 'price',
    TIMELINE_IMPOSSIBLE: 'timeline',
    TECHNICAL_DIFFICULTY: 'technical',
    CAPACITY_SHORTAGE: 'capacity',
    CUSTOMER_CREDIT: 'credit',
    MATERIAL_SHORTAGE: 'material',
    RESOURCE_CONFLICT: 'resource',
    PROFIT_MARGIN_LOW: 'profit',
    OTHER: 'other'
  }
  return classMap[category] || 'default'
}

function truncateReason(reason: string): string {
  return reason.length > 50 ? reason.substring(0, 50) + '...' : reason
}

function formatDate(dateStr: string): string {
  const date = new Date(dateStr)
  return date.toLocaleDateString('zh-CN')
}

function viewDetails(rejection: QuotationRejection) {
  // TODO: 实现详情查看功能
  console.log('View rejection details:', rejection)
}

function prevPage() {
  if (currentPage.value > 1) {
    currentPage.value--
    loadRejections()
  }
}

function nextPage() {
  currentPage.value++
  loadRejections()
}

onMounted(() => {
  loadRejections()
})
</script>

<style scoped>
.rejection-list {
  background: #0F172A;
  border-radius: 8px;
  padding: 24px;
  color: #F8FAFC;
}

.rejection-list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid #1E293B;
}

.rejection-list-header h3 {
  font-size: 20px;
  font-weight: 600;
  margin: 0;
  color: #F8FAFC;
}

.filters {
  display: flex;
  gap: 12px;
  align-items: center;
}

.filter-select,
.filter-date {
  background: #1E293B;
  border: 1px solid #334155;
  border-radius: 6px;
  padding: 8px 12px;
  color: #F8FAFC;
  font-size: 14px;
}

.filter-select:focus,
.filter-date:focus {
  outline: none;
  border-color: #38BDF8;
}

.btn-reset {
  background: #334155;
  border: none;
  border-radius: 6px;
  padding: 8px 16px;
  color: #F8FAFC;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s;
}

.btn-reset:hover {
  background: #475569;
}

.loading,
.error,
.empty {
  text-align: center;
  padding: 48px 0;
  color: #94A3B8;
  font-size: 16px;
}

.error {
  color: #F87171;
}

.rejection-table-container {
  overflow-x: auto;
}

.rejection-table {
  width: 100%;
  border-collapse: collapse;
}

.rejection-table thead {
  background: #1E293B;
}

.rejection-table th {
  padding: 12px 16px;
  text-align: left;
  font-weight: 600;
  font-size: 14px;
  color: #94A3B8;
  border-bottom: 1px solid #334155;
}

.rejection-row {
  cursor: pointer;
  transition: background 0.2s;
}

.rejection-row:hover {
  background: #1E293B;
}

.rejection-table td {
  padding: 14px 16px;
  font-size: 14px;
  border-bottom: 1px solid #1E293B;
}

.category-badge {
  display: inline-block;
  padding: 4px 12px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
}

.category-badge.price { background: #FEF3C7; color: #92400E; }
.category-badge.timeline { background: #DBEAFE; color: #1E3A8A; }
.category-badge.technical { background: #FCE7F3; color: #831843; }
.category-badge.capacity { background: #FED7AA; color: #7C2D12; }
.category-badge.credit { background: #FEE2E2; color: #991B1B; }
.category-badge.material { background: #E0E7FF; color: #3730A3; }
.category-badge.resource { background: #F3E8FF; color: #581C87; }
.category-badge.profit { background: #CCFBF1; color: #134E4A; }
.category-badge.other { background: #E2E8F0; color: #1E293B; }

.reason-cell {
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pagination {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 16px;
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid #1E293B;
}

.btn-page {
  background: #1E293B;
  border: 1px solid #334155;
  border-radius: 6px;
  padding: 8px 16px;
  color: #F8FAFC;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-page:hover:not(:disabled) {
  background: #334155;
  border-color: #38BDF8;
}

.btn-page:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.page-info {
  color: #94A3B8;
  font-size: 14px;
}
</style>
