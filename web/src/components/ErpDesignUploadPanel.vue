<script setup lang="ts">
import {computed, ref} from 'vue'
import {CheckCircle2, FileSpreadsheet, RefreshCw, ShieldCheck, Upload} from 'lucide-vue-next'
import {api, post} from '../api'
import ErpParsedDesignView from './ErpParsedDesignView.vue'
import ErpApprovalConfigView from './ErpApprovalConfigView.vue'

const props = defineProps<{canImport: boolean}>()
const emit = defineEmits<{error: [message: string]}>()

const file = ref<File | null>(null)
const sheetType = ref<'hardware' | 'steel'>('hardware')
const busy = ref(false)
const sessionId = ref<number | null>(null)
const parsed = ref<any>(null)
const status = ref<any>(null)
const result = ref<any>(null)
const validation = ref<any>(null)
const approvalConfig = ref<any>(null)
const imported = ref<any>(null)
const confirmImport = ref(false)
const expectedDate = ref('')
const existingSessionId = ref('')
const duplicateMessage = ref('')

const rows = computed<any[]>(() => Array.isArray(result.value?.previewRows) ? result.value.previewRows : [])
const moldCode = computed(() => result.value?.moldCode ?? parsed.value?.moldCode ?? null)
const drawingFinished = computed(() => status.value?.drawingProcessing !== true)
const importAllowed = computed(() => validation.value?.canImport !== false && validation.value?.valid !== false && !validation.value?.errors?.length)
const validationErrors = computed<any[]>(() => Array.isArray(validation.value?.errors) ? validation.value.errors : [])
const validationWarnings = computed<any[]>(() => Array.isArray(validation.value?.warnings) ? validation.value.warnings : [])

function message(error: any) {
  return error instanceof Error ? error.message : '操作未完成，请稍后重试'
}
function resultText(value: any) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string') return value
  return value.message || value.msg || value.reason || 'ERP 返回了需要处理的信息'
}
function useFile(event: Event) {
  const input = event.target as HTMLInputElement
  file.value = input.files?.[0] ?? null
  sessionId.value = null
  parsed.value = status.value = result.value = validation.value = approvalConfig.value = imported.value = null
  confirmImport.value = false
  expectedDate.value = ''
}
function requireSession() {
  if (!sessionId.value) throw new Error('请先上传并解析设计清单')
  return sessionId.value
}
function rowPayload() {
  const currentSession = requireSession()
  if (!rows.value.length) throw new Error('请先读取已完成的解析结果')
  return {session_id: currentSession, sheet_type: sheetType.value, mold_code: moldCode.value, preview_rows: rows.value}
}
async function parse() {
  const selected = file.value
  if (!selected) {
    emit('error', '请选择 XLSX 设计清单')
    return
  }
  if (!selected.name.toLowerCase().endsWith('.xlsx')) {
    emit('error', '新模设计上传仅支持 XLSX 文件')
    return
  }
  busy.value = true
  try {
    const query = new URLSearchParams({filename: selected.name, request_key: crypto.randomUUID()})
    const saved = await api(`/files?${query}`, {
      method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: selected,
    })
    parsed.value = await post('/erp-design-uploads/parse', {file_id: saved.id, sheet_type: sheetType.value})
    sessionId.value = parsed.value.sessionId
    await refreshStatus()
  } catch (error) {
    emit('error', message(error))
  } finally {
    busy.value = false
  }
}
async function resumeSession() {
  const value = Number(existingSessionId.value)
  if (!Number.isInteger(value) || value < 1) {
    emit('error', '请输入有效的 ERP 上传会话 ID')
    return
  }
  sessionId.value = value
  parsed.value = status.value = result.value = validation.value = approvalConfig.value = imported.value = null
  confirmImport.value = false
  await refreshStatus()
}
async function refreshStatus() {
  try {
    status.value = await post('/erp-design-uploads/status', {session_id: requireSession(), include_result: false})
  } catch (error) {
    emit('error', message(error))
  }
}
async function loadResult() {
  busy.value = true
  try {
    result.value = await post('/erp-design-uploads/result', {session_id: requireSession()})
  } catch (error) {
    emit('error', message(error))
  } finally {
    busy.value = false
  }
}
async function validate() {
  busy.value = true
  try {
    validation.value = await post('/erp-design-uploads/validate', rowPayload())
  } catch (error) {
    emit('error', message(error))
  } finally {
    busy.value = false
  }
}
async function reprice() {
  busy.value = true
  try {
    const response = await post('/erp-design-uploads/reprice', rowPayload())
    if (Array.isArray(response?.previewRows)) result.value = {...result.value, previewRows: response.previewRows}
  } catch (error) {
    emit('error', message(error))
  } finally {
    busy.value = false
  }
}
async function loadApprovalConfig() {
  busy.value = true
  try {
    approvalConfig.value = await post('/erp-design-uploads/approval-config', {session_id: requireSession()})
  } catch (error) {
    emit('error', message(error))
  } finally {
    busy.value = false
  }
}
async function importToErp(allowDuplicate = false) {
  if (!confirmImport.value) return
  if (!expectedDate.value) {
    emit('error', '请选择交期后再确认导入')
    return
  }
  busy.value = true
  try {
    imported.value = await post('/erp-design-uploads/import', {...rowPayload(), confirm_import: true, expected_date: expectedDate.value, allow_duplicate: allowDuplicate})
  } catch (error) {
    if ((error as any)?.code === 'ERP_DUPLICATE_CONFIRMATION_REQUIRED') {
      duplicateMessage.value = message(error)
      return
    }
    emit('error', message(error))
  } finally {
    busy.value = false
  }
}
async function confirmDuplicateImport() {
  duplicateMessage.value = ''
  await importToErp(true)
}
</script>

<template>
  <section class="erp-design-upload">
    <header class="erp-hero">
      <div><span class="erp-kicker">ERP MCP · 新模设计</span><h2>新模设计上传</h2><p>上传、图纸处理、校验与确认导入都在同一条业务流程中完成。</p></div>
      <dl class="erp-hero-metrics"><div><dt>上传会话</dt><dd>{{sessionId ?? '—'}}</dd></div><div><dt>解析明细</dt><dd>{{rows.length || '—'}}</dd></div><div><dt>当前模号</dt><dd>{{moldCode || '待解析'}}</dd></div></dl>
    </header>

    <div class="erp-flow">
      <section class="erp-step is-current">
        <header class="erp-step-heading"><span class="erp-step-number">1</span><div><h3><FileSpreadsheet :size="18"/>上传设计清单</h3><p>选择一份 XLSX 文件，创建 ERP 上传会话。</p></div></header>
        <div class="erp-upload-grid"><label>设计清单<input type="file" accept=".xlsx" @change="useFile"/></label><label>清单类型<select v-model="sheetType"><option value="hardware">五金清单</option><option value="steel">钢材清单</option></select></label></div>
        <div class="erp-step-actions"><button class="primary" :disabled="busy || !file" @click="parse"><Upload :size="17"/>{{busy ? '正在上传…' : '上传并解析'}}</button><span v-if="sessionId" class="erp-session-chip">会话 {{sessionId}}</span></div>
        <div class="erp-resume-row"><label>恢复已有 ERP 会话<input v-model="existingSessionId" type="number" min="1" placeholder="例如：242"/></label><button :disabled="busy || !existingSessionId" @click="resumeSession">恢复会话</button></div>
      </section>

      <section v-if="sessionId" class="erp-step" :class="{'is-complete': status && drawingFinished, 'is-processing': status?.drawingProcessing === true}">
        <header class="erp-step-heading"><span class="erp-step-number">2</span><div><h3><RefreshCw :size="18"/>等待图纸处理</h3><p>ERP 完成图纸匹配和归档后才能读取解析结果。</p></div><span class="erp-state" :class="status?.drawingProcessing === true ? 'is-waiting' : 'is-ready'">{{status?.drawingProcessing === true ? '处理中' : status ? '已完成' : '待查询'}}</span></header>
        <p v-if="status?.drawingProcessing === true" class="warning">ERP 正在处理图纸，请稍后刷新状态。</p>
        <p v-else-if="status" class="erp-hint">图纸处理已完成，可以继续读取解析结果。</p>
        <div class="erp-step-actions"><button :disabled="busy" @click="refreshStatus">刷新状态</button><button class="primary" :disabled="busy || !drawingFinished" @click="loadResult">读取解析结果</button></div>
      </section>

      <section v-if="result" class="erp-step" :class="{'is-complete': validation && importAllowed}">
        <header class="erp-step-heading"><span class="erp-step-number">3</span><div><h3><CheckCircle2 :size="18"/>校验与核价</h3><p>核对解析出的设计明细，确认其可被 ERP 接收。</p></div><span class="erp-state" :class="validation ? (importAllowed ? 'is-ready' : 'is-error') : ''">{{validation ? (importAllowed ? '校验通过' : '需处理') : '待校验'}}</span></header>
        <dl class="erp-upload-summary"><dt>模号</dt><dd>{{moldCode || 'ERP 未返回'}}</dd><dt>明细行数</dt><dd>{{rows.length}}</dd></dl>
        <div class="erp-step-actions"><button :disabled="busy || !rows.length" @click="reprice">重新核价</button><button class="primary" :disabled="busy || !rows.length" @click="validate">校验明细</button></div>
        <p v-if="validation" :class="importAllowed ? 'erp-hint' : 'warning'">{{importAllowed ? 'ERP 校验已通过，可以查看审批配置并确认导入。' : 'ERP 校验未通过，请查看详情并处理错误。'}}</p>
        <details class="erp-disclosure"><summary>查看解析明细</summary><ErpParsedDesignView :value="result"/></details>
        <section v-if="validationErrors.length || validationWarnings.length" class="erp-validation-notes"><h4>校验提示</h4><ul v-if="validationErrors.length" class="is-error"><li v-for="(item, index) in validationErrors" :key="'error-'+index">{{resultText(item)}}</li></ul><ul v-if="validationWarnings.length"><li v-for="(item, index) in validationWarnings" :key="'warning-'+index">{{resultText(item)}}</li></ul></section>
      </section>

      <section v-if="validation" class="erp-step erp-import-step" :class="{'is-complete': imported}">
        <header class="erp-step-heading"><span class="erp-step-number">4</span><div><h3><ShieldCheck :size="18"/>审批配置与确认导入</h3><p>确认交期和审批配置后，才会在 ERP 创建申请和审批数据。</p></div></header>
        <button class="erp-config-button" :disabled="busy" @click="loadApprovalConfig">读取 ERP 审批配置</button>
        <details v-if="approvalConfig" class="erp-disclosure"><summary>查看审批配置</summary><ErpApprovalConfigView :value="approvalConfig"/></details>
        <template v-if="props.canImport">
          <div class="erp-confirm-grid"><label>交期<input v-model="expectedDate" type="date" required/></label><label class="check-label erp-confirm-check"><input v-model="confirmImport" type="checkbox"/>我确认现在在 ERP 创建新模设计申请及审批数据</label></div>
          <p v-if="!importAllowed" class="warning">ERP 校验未通过，不能确认导入。</p>
          <button class="primary erp-import-button" :disabled="busy || !expectedDate || !confirmImport || !importAllowed" @click="() => importToErp()">确认导入 ERP</button>
        </template>
        <p v-else class="erp-hint">当前账号没有 ERP 导入权限；可完成上传、状态查询、结果读取和校验。</p>
        <section v-if="imported" class="erp-import-receipt"><div><span class="erp-state is-ready">已导入</span><h4>{{imported.message || 'ERP 已创建新模设计申请'}}</h4><p>申请已进入 ERP 审批流程，可在 ERP 中继续跟进。</p></div><dl><dt>申请编号</dt><dd>{{imported.requestNo || '—'}}</dd><dt>审批流程</dt><dd>{{imported.processName || '—'}}</dd><dt>当前节点</dt><dd>{{imported.currentNodeName || '—'}}</dd></dl></section>
      </section>
    </div>

    <div v-if="duplicateMessage" class="modal-shade" role="presentation">
      <section class="modal" role="dialog" aria-modal="true" aria-labelledby="duplicate-import-title">
        <h2 id="duplicate-import-title">检测到重复上传</h2>
        <p>{{duplicateMessage}}</p>
        <p class="warning">继续导入会在 ERP 中创建一条新的申请及审批数据。</p>
        <div class="actions"><button @click="duplicateMessage=''">取消</button><button class="primary" :disabled="busy" @click="confirmDuplicateImport">仍要导入</button></div>
      </section>
    </div>
  </section>
</template>
