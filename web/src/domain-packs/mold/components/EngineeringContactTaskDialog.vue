<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { X, Plus, Trash2 } from 'lucide-vue-next'
import { api, post } from '../../../api'

type Props = { stepId: string; proposal: any; archived?: boolean }
type Emits = { confirmed: []; dismissed: []; error: [message: string] }

const props = withDefaults(defineProps<Props>(), { archived: false })
const emit = defineEmits<Emits>()

const opened = ref(true)
const dialogElement = ref<HTMLElement | null>(null)
const previousFocus = ref<HTMLElement | null>(null)
const originalOverflow = ref('')
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const fieldErrors = ref<Record<string, string>>({})
const options = ref<any>({ departments: [], completion_types: [], change_categories: [] })
const intent = ref<any>(null)

const source = computed(() => props.proposal?.display?.来源候选 || {})
const form = reactive<any>({
  customer_ref: '', customer_name: '', product_name: '', mold_number: '', product_ref: '',
  responsible_department_id: '', application_date: '', completion_date: '', completion_type: 'NORMAL',
  change_categories: [], change_description: '', countermeasure: '', related_units: [],
  pricing_note: '', total_amount: '0.00', currency: 'CNY',
})

function blankUnit() {
  return { department_id: '', assignee_id: '', completion_date: '', work_content: '', hours: '0.00', amount: '0.00', currency: form.currency || 'CNY', remark: '' }
}
function hydrate() {
  const input = props.proposal?.input?.form
  Object.assign(form, input ? JSON.parse(JSON.stringify(input)) : {
    customer_ref: source.value['客户引用'] || '', customer_name: source.value['客户'] || '', mold_number: source.value['模具号'] || '',
    product_ref: source.value['产品料号'] || '', application_date: source.value['申请日期'] || '',
  })
  if (!form.related_units?.length) form.related_units = [blankUnit()]
  if (!form.change_categories) form.change_categories = []
}
async function loadOptions() {
  loading.value = true
  try { options.value = await api(`/contact-proposals/${encodeURIComponent(props.stepId)}/form-options`); hydrate() }
  catch (e: any) { error.value = e.message || '无法加载责任部门和人员候选'; emit('error', error.value) }
  finally { loading.value = false }
}
function peopleFor(departmentId: string) { return options.value.departments?.find((item: any) => item.id === departmentId)?.people || [] }
function departmentChanged(unit: any) { if (!peopleFor(unit.department_id).some((person: any) => person.id === unit.assignee_id)) unit.assignee_id = '' }
function addUnit() { form.related_units.push(blankUnit()) }
function removeUnit(index: number) { if (form.related_units.length > 1) form.related_units.splice(index, 1) }
function toggleCategory(value: string) {
  const values = new Set(form.change_categories)
  if (values.has(value)) values.delete(value); else values.add(value)
  form.change_categories = [...values]
}
function validate() {
  const errors: Record<string, string> = {}
  if (!form.responsible_department_id) errors.responsible_department_id = '请选择责任部门'
  if (!form.application_date) errors.application_date = '请选择申请日期'
  if (!form.completion_date) errors.completion_date = '请选择完成日期'
  if (form.application_date && form.completion_date && form.completion_date < form.application_date) errors.completion_date = '完成日期不能早于申请日期'
  if (!form.change_categories.length) errors.change_categories = '至少选择一项变更类别'
  if (!String(form.change_description || '').trim()) errors.change_description = '请填写变更说明'
  if (!String(form.countermeasure || '').trim()) errors.countermeasure = '请填写对策'
  form.related_units.forEach((unit: any, index: number) => {
    if (!unit.department_id) errors[`unit.${index}.department_id`] = '请选择单位'
    if (!unit.assignee_id) errors[`unit.${index}.assignee_id`] = '请选择责任人'
    if (!unit.completion_date) errors[`unit.${index}.completion_date`] = '请选择完成时间'
    if (!String(unit.work_content || '').trim()) errors[`unit.${index}.work_content`] = '请填写作业内容'
  })
  fieldErrors.value = errors
  return Object.keys(errors).length === 0
}
function payload() {
  return { case_id: props.proposal.case_id, revision: props.proposal.input.revision, form: JSON.parse(JSON.stringify(form)) }
}
async function prepare() {
  if (!validate() || busy.value) return
  busy.value = true; error.value = ''
  try { intent.value = await post(`/contact-proposals/${encodeURIComponent(props.stepId)}/form-intent`, { input: payload() }) }
  catch (e: any) { error.value = e.message || '表单 Proposal 准备失败'; emit('error', error.value) }
  finally { busy.value = false }
}
async function confirm() {
  if (!intent.value || busy.value) return
  busy.value = true; error.value = ''
  try { await post(`/human-actions/${intent.value.id}/confirm`, { challenge: intent.value.challenge }); intent.value = null; emit('confirmed') }
  catch (e: any) { error.value = e.message || '确认失败'; if ([403, 409].includes(e.status)) intent.value = null; emit('error', error.value) }
  finally { busy.value = false }
}
function focusDialog() { void nextTick(() => dialogElement.value?.focus()) }
function restoreFocus() { void nextTick(() => previousFocus.value?.focus()) }
function handleKeydown(event: KeyboardEvent) {
  if (!opened.value) return
  if (event.key === 'Escape') { event.preventDefault(); close(); return }
  if (event.key !== 'Tab' || !dialogElement.value) return
  const focusable = Array.from(dialogElement.value.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])'))
  if (!focusable.length) return
  const first = focusable[0], last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
function indexNumber(value: string | number) { return Number(value) }
function close() { opened.value = false; intent.value = null; document.body.style.overflow = originalOverflow.value; emit('dismissed'); restoreFocus() }
function reopen() { opened.value = true; document.body.style.overflow = 'hidden'; focusDialog() }
onMounted(async () => {
  previousFocus.value = document.activeElement as HTMLElement
  originalOverflow.value = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  window.addEventListener('keydown', handleKeydown)
  await loadOptions()
  focusDialog()
})
onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown)
  document.body.style.overflow = originalOverflow.value
})
const title = computed(() => intent.value ? '请确认工程变更申请联络单办理事项' : '填写工程变更申请联络单')
</script>

<template>
  <section v-if="!opened" class="contact-form-reopen">
    <span>工程变更申请联络单办理事项待本人确认</span>
    <button type="button" class="primary" @click="reopen">继续选择并确认</button>
  </section>
  <Teleport to="body">
    <div v-if="opened" class="modal-shade" @click.self="close">
      <section ref="dialogElement" class="modal engineering-contact-task-dialog" role="dialog" aria-modal="true" aria-label="工程变更申请联络单办理事项" tabindex="-1">
        <header class="dialog-head">
          <div><p class="eyebrow">工程联络单 · 批量办理事项</p><h2>{{ title }}</h2><p class="muted">本人确认前不会创建任何办理事项；责任单位和具体责任人必须从当前授权候选中选择。</p></div>
          <button type="button" class="icon-button" aria-label="关闭工程变更申请联络单弹窗" @click="close"><X :size="18"/></button>
        </header>
        <p v-if="loading" class="muted" role="status">正在加载责任部门和人员候选…</p>
        <form v-else @submit.prevent="intent ? confirm() : prepare()">
          <fieldset :disabled="busy || Boolean(intent)">
            <legend>基本信息</legend>
            <div class="form-grid">
              <label>客户<input v-model="form.customer_name" required/></label>
              <label>客户引用<input v-model="form.customer_ref" required/></label>
              <label>品名<input v-model="form.product_name" required/></label>
              <label>模具编号<input v-model="form.mold_number" required/></label>
              <label>产品料号<input v-model="form.product_ref" required/></label>
              <label>责任部门<select v-model="form.responsible_department_id" required><option value="">请选择</option><option v-for="item in options.departments" :key="item.id" :value="item.id">{{ item.name }}</option></select><small v-if="fieldErrors.responsible_department_id" class="field-error">{{ fieldErrors.responsible_department_id }}</small></label>
              <label>申请日期<input v-model="form.application_date" type="date" required/><small v-if="fieldErrors.application_date" class="field-error">{{ fieldErrors.application_date }}</small></label>
              <label>完成日期<input v-model="form.completion_date" type="date" required/><small v-if="fieldErrors.completion_date" class="field-error">{{ fieldErrors.completion_date }}</small></label>
            </div>
          </fieldset>
          <fieldset :disabled="busy || Boolean(intent)"><legend>完成类型</legend><div class="choice-row"><label v-for="item in options.completion_types" :key="item.value"><input v-model="form.completion_type" type="radio" :value="item.value"/> {{ item.label }}</label></div></fieldset>
          <fieldset :disabled="busy || Boolean(intent)"><legend>变更类别</legend><div class="choice-row category-list"><label v-for="item in options.change_categories" :key="item.value"><input type="checkbox" :checked="form.change_categories.includes(item.value)" @change="toggleCategory(item.value)"/> {{ item.label }}</label></div><small v-if="fieldErrors.change_categories" class="field-error">{{ fieldErrors.change_categories }}</small></fieldset>
          <fieldset :disabled="busy || Boolean(intent)"><legend>变更说明与对策</legend><label>变更说明<textarea v-model="form.change_description" rows="3" required/><small v-if="fieldErrors.change_description" class="field-error">{{ fieldErrors.change_description }}</small></label><label>对策<textarea v-model="form.countermeasure" rows="3" required/><small v-if="fieldErrors.countermeasure" class="field-error">{{ fieldErrors.countermeasure }}</small></label></fieldset>
          <fieldset :disabled="busy || Boolean(intent)"><legend>相关单位与办理事项</legend><div v-for="(unit,index) in form.related_units" :key="index" class="unit-row"><div class="unit-row-head"><strong>办理事项 {{ indexNumber(index) + 1 }}</strong><button v-if="form.related_units.length > 1" type="button" class="icon-button" aria-label="删除办理事项" @click="removeUnit(indexNumber(index))"><Trash2 :size="15"/></button></div><div class="unit-grid"><label>单位<select v-model="unit.department_id" required @change="departmentChanged(unit)"><option value="">请选择</option><option v-for="item in options.departments" :key="item.id" :value="item.id">{{ item.name }}</option></select><small v-if="fieldErrors[`unit.${index}.department_id` ]" class="field-error">{{ fieldErrors[`unit.${index}.department_id` ] }}</small></label><label>责任人<select v-model="unit.assignee_id" required><option value="">请选择</option><option v-for="person in peopleFor(unit.department_id)" :key="person.id" :value="person.id">{{ person.name }}</option></select><small v-if="fieldErrors[`unit.${index}.assignee_id` ]" class="field-error">{{ fieldErrors[`unit.${index}.assignee_id` ] }}</small></label><label>完成时间<input v-model="unit.completion_date" type="date" required/><small v-if="fieldErrors[`unit.${index}.completion_date` ]" class="field-error">{{ fieldErrors[`unit.${index}.completion_date` ] }}</small></label><label class="wide">作业内容<input v-model="unit.work_content" required/><small v-if="fieldErrors[`unit.${index}.work_content` ]" class="field-error">{{ fieldErrors[`unit.${index}.work_content` ] }}</small></label><label>工时<input v-model="unit.hours" type="number" min="0" step="0.01" required/></label><label>金额<input v-model="unit.amount" type="number" min="0" step="0.01" required/></label><label>币种<input v-model="unit.currency" maxlength="3" required/></label><label>备注<input v-model="unit.remark"/></label></div></div><button type="button" class="secondary add-unit" @click="addUnit"><Plus :size="15"/>新增相关单位</button></fieldset>
          <fieldset :disabled="busy || Boolean(intent)"><legend>计价</legend><div class="form-grid"><label>计价说明<input v-model="form.pricing_note"/></label><label>金额合计<input v-model="form.total_amount" type="number" min="0" step="0.01" required/></label><label>币种<input v-model="form.currency" maxlength="3" required/></label></div></fieldset>
          <div class="workflow-note"><strong>流程提示</strong><span>申请、审核、批准字段仅作为纸质表单追溯提示，不显示或伪造模型签名；当前仅准备批量办理事项 Proposal。</span></div>
          <p v-if="error" class="field-error" role="alert">{{ error }}</p>
          <div class="dialog-actions"><button type="button" @click="close">暂不处理</button><button class="primary" type="submit" :disabled="loading || busy || props.archived">{{ busy ? '正在处理…' : intent ? '本人确认并创建全部事项' : '生成最终 Proposal' }}</button></div>
        </form>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.contact-form-reopen{display:flex;align-items:center;justify-content:space-between;gap:12px;width:min(820px,calc(100% - 48px));margin:8px auto;padding:12px 14px;border:1px solid var(--border);border-radius:12px;background:var(--surface);font-size:13px}.engineering-contact-task-dialog{width:min(920px,calc(100vw - 24px));max-height:calc(100vh - 24px);overflow:auto;padding:20px}.dialog-head{display:flex;justify-content:space-between;gap:18px;margin-bottom:16px}.dialog-head h2{margin:3px 0 6px;font-size:20px}.dialog-head p{margin:0;line-height:1.5}.eyebrow{font-size:11px;color:var(--accent);letter-spacing:.08em}.engineering-contact-task-dialog form{display:grid;gap:14px}.engineering-contact-task-dialog fieldset{border:1px solid var(--border);border-radius:12px;padding:13px}.engineering-contact-task-dialog legend{padding:0 6px;color:var(--muted);font-size:12px}.form-grid,.unit-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.form-grid label,.unit-grid label,.engineering-contact-task-dialog fieldset>label{display:grid;gap:5px;font-size:12px;color:var(--muted)}.form-grid input,.form-grid select,.unit-grid input,.unit-grid select,.engineering-contact-task-dialog textarea{width:100%;box-sizing:border-box;border:1px solid var(--border);border-radius:7px;padding:8px;background:var(--surface);color:var(--text);font:inherit}.engineering-contact-task-dialog textarea{resize:vertical}.choice-row{display:flex;flex-wrap:wrap;gap:10px 16px}.choice-row label{font-size:13px;color:var(--text)}.category-list{display:grid;grid-template-columns:repeat(4,minmax(0,1fr))}.unit-row{border-top:1px dashed var(--border);padding-top:12px;margin-top:12px}.unit-row-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;font-size:13px}.unit-grid .wide{grid-column:span 2}.add-unit{display:inline-flex;align-items:center;gap:5px;margin-top:10px}.workflow-note{display:grid;gap:3px;padding:10px 12px;border-radius:8px;background:color-mix(in srgb,var(--surface) 84%,var(--accent) 16%);font-size:12px;line-height:1.5}.workflow-note span{color:var(--muted)}.field-error{color:var(--error,#b4534b);font-size:11px}.dialog-actions{display:flex;justify-content:flex-end;gap:8px}.dialog-actions button{min-height:34px;padding:0 14px;border-radius:8px}.primary{background:var(--accent);color:var(--accent-contrast,#fff);border-color:var(--accent)}.secondary{border:1px solid var(--border);background:var(--surface);color:var(--text)}@media(max-width:760px){.form-grid,.unit-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.category-list{grid-template-columns:repeat(2,minmax(0,1fr))}.unit-grid .wide{grid-column:span 2}}@media(max-width:480px){.engineering-contact-task-dialog{padding:14px}.form-grid,.unit-grid,.category-list{grid-template-columns:1fr}.unit-grid .wide{grid-column:auto}.contact-form-reopen{width:calc(100% - 20px);align-items:flex-start;flex-direction:column}}
</style>

