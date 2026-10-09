<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { X } from 'lucide-vue-next'
import { post } from '../../../api'
import { quotationFormMissingFields } from '../quotationForm'

type Props = { stepId: string; proposal: any; archived?: boolean }
type Emits = { confirmed: []; dismissed: []; error: [message: string] }

const props = withDefaults(defineProps<Props>(), { archived: false })
const emit = defineEmits<Emits>()
const opened = ref(true)
const dialogElement = ref<HTMLElement | null>(null)
const previousFocus = ref<HTMLElement | null>(null)
const originalOverflow = ref('')
const busy = ref(false)
const error = ref('')
const fieldErrors = ref<Record<string, string>>({})
const intent = ref<any>(null)

const display = computed(() => props.proposal?.display || {})
const files = computed(() => display.value.files || [])
const existing = computed(() => display.value.existing_quotations || [])
const workflows = computed(() => display.value.workflow_options || [])
const currentEffective = computed(() => existing.value.find((item: any) => item.status === 'EFFECTIVE'))

function detailOf(row: any) { return row?.detail || row || {} }
function nextVersion() {
  const versions = existing.value.map((item: any) => Number(detailOf(item).version || item.version || 0))
  return Math.max(0, ...versions) + 1
}

const form = reactive<any>({
  project_id: props.proposal?.input?.project_id || display.value.project?.id || '',
  project_version: props.proposal?.input?.project_version || 1,
  previous_id: currentEffective.value?.id || null,
  quotation_number: detailOf(currentEffective.value).quotation_number || '',
  version: nextVersion(),
  preliminary_execution_mode: 'INTERNAL',
  quoted_amount: '', currency: 'CNY', promised_delivery_date: '', payment_terms: '',
  cost_amount: '', cost_evidence: '', process_analysis: '', duration_days: '', duration_evidence: '',
  supplier_quote_amount: '', supplier_delivery_date: '', supplier_requirements: '', supplier_quote_evidence: '',
  customer_company_snapshot: '', customer_contact_snapshot: '', owner_user_id: '',
  source_summary: {}, source_kind: 'UPLOAD', source_ref: '',
  file_ids: [...(props.proposal?.input?.file_ids || files.value.map((item: any) => item.id))],
  workflow_definition_id: '',
  rejection_reason: '',
  rejection_category: '',
})

const title = computed(() => intent.value ? '请确认客户报价版本' : '填写客户报价版本')
const modeLabel = computed(() => form.preliminary_execution_mode === 'FULL_OUTSOURCE' ? '整套委外' : '内部加工')
const missingLabels: Record<string, string> = {
  quotation_number: '报价编号', version: '报价版本', quoted_amount: '报价金额', currency: '币种',
  promised_delivery_date: '承诺交期', payment_terms: '收款条件', customer_company_snapshot: '客户公司',
  customer_contact_snapshot: '客户负责人', owner_user_id: '我方责任人', source_kind: '来源类型', source_ref: '来源标识',
  workflow_definition_id: '审批流程', cost_amount: '成本金额', cost_evidence: '成本依据', process_analysis: '工艺分析',
  duration_days: '工期估算', duration_evidence: '工期依据', supplier_quote_amount: '供应商报价',
  supplier_delivery_date: '供应商交期', supplier_requirements: '供应商要求', supplier_quote_evidence: '供应商报价依据',
  rejection_reason: '拒绝原因', rejection_category: '拒绝类别',
}

const rejectionCategories = [
  { value: 'PRICE_TOO_LOW', label: '价格过低' },
  { value: 'TIMELINE_IMPOSSIBLE', label: '交期不可行' },
  { value: 'TECHNICAL_DIFFICULTY', label: '技术难度过高' },
  { value: 'CAPACITY_SHORTAGE', label: '产能不足' },
  { value: 'CUSTOMER_CREDIT', label: '客户信誉问题' },
  { value: 'MATERIAL_SHORTAGE', label: '材料缺货' },
  { value: 'RESOURCE_CONFLICT', label: '资源冲突' },
  { value: 'PROFIT_MARGIN_LOW', label: '利润率过低' },
  { value: 'OTHER', label: '其他原因' },
]

const showRejectionForm = ref(false)

function validate() {
  const errors: Record<string, string> = {}
  for (const key of quotationFormMissingFields(form)) errors[key] = `请填写${missingLabels[key] || key}`
  if (form.version < 1) errors.version = '版本必须从 1 开始'
  if (form.preliminary_execution_mode === 'FULL_OUTSOURCE' && !form.supplier_quote_evidence?.trim()) {
    errors.supplier_quote_evidence = '整套委外必须填写供应商报价依据'
  }
  fieldErrors.value = errors
  return Object.keys(errors).length === 0
}

function payload() {
  return {
    ...JSON.parse(JSON.stringify(form)),
    version: Number(form.version),
    project_version: Number(form.project_version),
    quoted_amount: String(form.quoted_amount),
    cost_amount: form.cost_amount === '' ? null : String(form.cost_amount),
    duration_days: form.duration_days === '' ? null : Number(form.duration_days),
    supplier_quote_amount: form.supplier_quote_amount === '' ? null : String(form.supplier_quote_amount),
    supplier_delivery_date: form.supplier_delivery_date || null,
    previous_id: form.previous_id || null,
  }
}

async function prepare() {
  if (!validate() || busy.value) return
  busy.value = true; error.value = ''
  try { intent.value = await post(`/quotation-proposals/${encodeURIComponent(props.stepId)}/form-intent`, { input: payload() }) }
  catch (e: any) { error.value = e.message || '报价 Proposal 准备失败'; emit('error', error.value) }
  finally { busy.value = false }
}
async function confirm() {
  if (!intent.value || busy.value) return
  busy.value = true; error.value = ''
  try { await post(`/human-actions/${intent.value.id}/confirm`, { challenge: intent.value.challenge }); intent.value = null; emit('confirmed') }
  catch (e: any) { error.value = e.message || '确认失败'; if ([403, 409].includes(e.status)) intent.value = null; emit('error', error.value) }
  finally { busy.value = false }
}
async function dismiss() {
  if (busy.value) return
  busy.value = true
  try { await post(`/proposals/${encodeURIComponent(props.stepId)}/dismiss`); close(); emit('dismissed') }
  catch (e: any) { error.value = e.message || '暂不处理失败'; emit('error', error.value) }
  finally { busy.value = false }
}
async function reject() {
  if (!form.rejection_category || !form.rejection_reason?.trim() || busy.value) return
  busy.value = true; error.value = ''
  try {
    await post(`/quotation-proposals/${encodeURIComponent(props.stepId)}/reject`, {
      rejection_category: form.rejection_category,
      rejection_reason: form.rejection_reason.trim(),
    })
    close()
    emit('dismissed')
  }
  catch (e: any) { error.value = e.message || '拒绝报价失败'; emit('error', error.value) }
  finally { busy.value = false }
}
function focusDialog() { void nextTick(() => dialogElement.value?.focus()) }
function restoreFocus() { void nextTick(() => previousFocus.value?.focus()) }
function close() { opened.value = false; document.body.style.overflow = originalOverflow.value; restoreFocus() }
function handleKeydown(event: KeyboardEvent) {
  if (!opened.value) return
  if (event.key === 'Escape') { event.preventDefault(); dismiss(); return }
  if (event.key !== 'Tab' || !dialogElement.value) return
  const focusable = Array.from(dialogElement.value.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])'))
  if (!focusable.length) return
  const first = focusable[0], last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
onMounted(() => {
  previousFocus.value = document.activeElement as HTMLElement
  originalOverflow.value = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  window.addEventListener('keydown', handleKeydown)
  focusDialog()
})
onUnmounted(() => { window.removeEventListener('keydown', handleKeydown); document.body.style.overflow = originalOverflow.value })
</script>

<template>
  <Teleport to="body">
    <div v-if="opened" class="modal-shade" @click.self="dismiss">
      <section ref="dialogElement" class="modal quotation-dialog" role="dialog" aria-modal="true" aria-label="客户报价版本表单" tabindex="-1">
        <header class="dialog-head">
          <div><p class="eyebrow">客户报价 · {{ modeLabel }}</p><h2>{{ title }}</h2><p class="muted">报价版本和来源资料必须由本人复核；确认前不会创建报价版本。</p></div>
          <button type="button" class="icon-button" aria-label="关闭报价弹窗" :disabled="busy" @click="dismiss"><X :size="18"/></button>
        </header>
        <div class="source-strip"><strong>{{ display.project?.code || '当前项目' }}</strong><span>{{ display.project?.name || '—' }}</span><span v-for="file in files" :key="file.id">附件：{{ file.filename }}</span></div>
        <form @submit.prevent="intent ? confirm() : prepare()">
          <fieldset :disabled="busy || Boolean(intent)"><legend>版本与客户</legend><div class="form-grid">
            <label>报价编号<input v-model="form.quotation_number" required/><small v-if="fieldErrors.quotation_number" class="field-error">{{ fieldErrors.quotation_number }}</small></label>
            <label>版本<input v-model.number="form.version" type="number" min="1" required/><small v-if="fieldErrors.version" class="field-error">{{ fieldErrors.version }}</small></label>
            <label>前一生效版本<select v-model="form.previous_id"><option :value="null">无（首版）</option><option v-for="item in existing" :key="item.id" :value="item.id">{{ detailOf(item).quotation_number || item.number || item.id }} · V{{ detailOf(item).version || item.version || '?' }} · {{ item.status }}</option></select></label>
            <label>客户公司<input v-model="form.customer_company_snapshot" required/><small v-if="fieldErrors.customer_company_snapshot" class="field-error">{{ fieldErrors.customer_company_snapshot }}</small></label>
            <label>客户负责人<input v-model="form.customer_contact_snapshot" required/><small v-if="fieldErrors.customer_contact_snapshot" class="field-error">{{ fieldErrors.customer_contact_snapshot }}</small></label>
            <label>我方责任人 ID<input v-model="form.owner_user_id" required/><small v-if="fieldErrors.owner_user_id" class="field-error">{{ fieldErrors.owner_user_id }}</small></label>
          </div></fieldset>
          <fieldset :disabled="busy || Boolean(intent)"><legend>价格、交期与条件</legend><div class="form-grid">
            <label>报价金额<input v-model="form.quoted_amount" type="number" min="0.01" step="0.01" required/><small v-if="fieldErrors.quoted_amount" class="field-error">{{ fieldErrors.quoted_amount }}</small></label>
            <label>币种<input v-model="form.currency" maxlength="3" required/></label>
            <label>承诺交期<input v-model="form.promised_delivery_date" type="date" required/><small v-if="fieldErrors.promised_delivery_date" class="field-error">{{ fieldErrors.promised_delivery_date }}</small></label>
            <label class="wide">收款条件<textarea v-model="form.payment_terms" rows="2" required/><small v-if="fieldErrors.payment_terms" class="field-error">{{ fieldErrors.payment_terms }}</small></label>
          </div></fieldset>
          <fieldset :disabled="busy || Boolean(intent)"><legend>加工方式</legend><div class="choice-row"><label><input v-model="form.preliminary_execution_mode" type="radio" value="INTERNAL"/>内部加工</label><label><input v-model="form.preliminary_execution_mode" type="radio" value="FULL_OUTSOURCE"/>整套委外</label></div><div v-if="form.preliminary_execution_mode === 'INTERNAL'" class="form-grid nested-grid">
            <label>成本金额<input v-model="form.cost_amount" type="number" min="0" step="0.01" required/><small v-if="fieldErrors.cost_amount" class="field-error">{{ fieldErrors.cost_amount }}</small></label>
            <label>预计工期（天）<input v-model="form.duration_days" type="number" min="1" required/><small v-if="fieldErrors.duration_days" class="field-error">{{ fieldErrors.duration_days }}</small></label>
            <label class="wide">成本依据<textarea v-model="form.cost_evidence" rows="2" required/><small v-if="fieldErrors.cost_evidence" class="field-error">{{ fieldErrors.cost_evidence }}</small></label>
            <label class="wide">工艺分析<textarea v-model="form.process_analysis" rows="2" required/><small v-if="fieldErrors.process_analysis" class="field-error">{{ fieldErrors.process_analysis }}</small></label>
            <label class="wide">工期依据<textarea v-model="form.duration_evidence" rows="2" required/><small v-if="fieldErrors.duration_evidence" class="field-error">{{ fieldErrors.duration_evidence }}</small></label>
          </div><div v-else class="form-grid nested-grid">
            <label>供应商报价<input v-model="form.supplier_quote_amount" type="number" min="0" step="0.01" required/><small v-if="fieldErrors.supplier_quote_amount" class="field-error">{{ fieldErrors.supplier_quote_amount }}</small></label>
            <label>供应商交期<input v-model="form.supplier_delivery_date" type="date" required/><small v-if="fieldErrors.supplier_delivery_date" class="field-error">{{ fieldErrors.supplier_delivery_date }}</small></label>
            <label class="wide">供应商要求<textarea v-model="form.supplier_requirements" rows="2" required/><small v-if="fieldErrors.supplier_requirements" class="field-error">{{ fieldErrors.supplier_requirements }}</small></label>
            <label class="wide">供应商报价依据<textarea v-model="form.supplier_quote_evidence" rows="2" required/><small v-if="fieldErrors.supplier_quote_evidence" class="field-error">{{ fieldErrors.supplier_quote_evidence }}</small></label>
          </div></fieldset>
          <fieldset :disabled="busy || Boolean(intent)"><legend>来源与审批</legend><div class="form-grid">
            <label>来源类型<select v-model="form.source_kind"><option value="UPLOAD">上传资料</option><option value="EMAIL">邮件</option><option value="CUSTOMER_PLATFORM">客户平台</option><option value="OTHER">其他</option></select></label>
            <label>来源标识<input v-model="form.source_ref" required placeholder="如 MAIL-QUOTE-001"/><small v-if="fieldErrors.source_ref" class="field-error">{{ fieldErrors.source_ref }}</small></label>
            <label>审批流程<select v-model="form.workflow_definition_id" required><option value="">请选择已发布报价流程</option><option v-for="item in workflows" :key="item.id" :value="item.id">{{ item.name }} · 第{{ item.version }}版</option></select><small v-if="fieldErrors.workflow_definition_id" class="field-error">{{ fieldErrors.workflow_definition_id }}</small></label>
          </div><p v-if="!workflows.length" class="field-error">当前没有可用的已发布报价审批流程，请先配置流程后再提交。</p></fieldset>
          <p v-if="error" class="field-error" role="alert">{{ error }}</p>
          <div class="dialog-actions">
            <button type="button" :disabled="busy" @click="showRejectionForm = !showRejectionForm">{{ showRejectionForm ? '取消拒绝' : '拒绝报价' }}</button>
            <button type="button" :disabled="busy" @click="dismiss">暂不处理</button>
            <button class="primary" type="submit" :disabled="busy || props.archived || !workflows.length">{{ busy ? '正在处理…' : intent ? '本人确认并提交报价审批' : '生成报价 Proposal' }}</button>
          </div>
          <fieldset v-if="showRejectionForm" :disabled="busy" class="rejection-form"><legend>拒绝报价</legend><div class="form-grid">
            <label>拒绝类别<select v-model="form.rejection_category" required><option value="">请选择拒绝类别</option><option v-for="cat in rejectionCategories" :key="cat.value" :value="cat.value">{{ cat.label }}</option></select><small v-if="fieldErrors.rejection_category" class="field-error">{{ fieldErrors.rejection_category }}</small></label>
            <label class="wide">拒绝原因<textarea v-model="form.rejection_reason" rows="3" required placeholder="请详细说明拒绝报价的原因"/><small v-if="fieldErrors.rejection_reason" class="field-error">{{ fieldErrors.rejection_reason }}</small></label>
          </div><div class="dialog-actions"><button type="button" class="danger" :disabled="busy || !form.rejection_category || !form.rejection_reason?.trim()" @click="reject">确认拒绝报价</button></div></fieldset>
        </form>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.quotation-dialog{width:min(900px,calc(100vw - 24px));max-height:calc(100vh - 24px);overflow:auto;padding:20px}.dialog-head{display:flex;justify-content:space-between;gap:18px;margin-bottom:14px}.dialog-head h2{margin:3px 0 6px;font-size:20px}.dialog-head p{margin:0;line-height:1.5}.eyebrow{font-size:11px;color:var(--accent);letter-spacing:.08em}.source-strip{display:flex;flex-wrap:wrap;gap:6px 12px;padding:9px 11px;margin-bottom:12px;border-radius:8px;background:color-mix(in srgb,var(--surface) 84%,var(--accent) 16%);font-size:12px}.source-strip span{color:var(--muted)}.quotation-dialog form{display:grid;gap:12px}.quotation-dialog fieldset{border:1px solid var(--border);border-radius:12px;padding:13px}.quotation-dialog legend{padding:0 6px;color:var(--muted);font-size:12px}.form-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.form-grid label{display:grid;gap:5px;font-size:12px;color:var(--muted)}.form-grid input,.form-grid select,.form-grid textarea{width:100%;box-sizing:border-box;border:1px solid var(--border);border-radius:7px;padding:8px;background:var(--surface);color:var(--text);font:inherit}.form-grid textarea{resize:vertical}.wide{grid-column:span 2}.nested-grid{margin-top:12px}.choice-row{display:flex;gap:18px;font-size:13px}.field-error{color:var(--error,#b4534b);font-size:11px}.dialog-actions{display:flex;justify-content:flex-end;gap:8px}.dialog-actions button{min-height:34px;padding:0 14px;border-radius:8px}.primary{background:var(--accent);color:var(--accent-contrast,#fff);border-color:var(--accent)}.danger{background:var(--error,#b4534b);color:#fff;border-color:var(--error,#b4534b)}.rejection-form{margin-top:12px;border-color:var(--error,#b4534b)}@media(max-width:700px){.form-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.wide{grid-column:span 2}}@media(max-width:480px){.quotation-dialog{padding:14px}.form-grid{grid-template-columns:1fr}.wide{grid-column:auto}.dialog-head{gap:8px}.dialog-head h2{font-size:18px}}
</style>
