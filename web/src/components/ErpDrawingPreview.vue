<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { DxfViewer as DxfViewerInstance } from 'dxf-viewer'
import type { ErpDesignRow } from '../erpDesignPreview'

const props = defineProps<{
  sessionId: number
  row: ErpDesignRow
}>()

defineEmits<{ close: [] }>()

const container = ref<HTMLElement | null>(null)
const loading = ref(false)
const error = ref('')
const progress = ref('')
const objectUrl = ref('')
const displayKind = ref<'dxf' | 'image' | 'pdf'>('dxf')
let viewer: DxfViewerInstance | null = null
let requestId = 0

const drawingId = computed(() => Number(
  props.row.drawing_resource_id
  ?? props.row.drawingResourceId
  ?? props.row.drawing_id
  ?? props.row.drawingId
  ?? 0,
))

const drawingName = computed(() => String(
  props.row.drawing_file_name
  ?? props.row.drawingFileName
  ?? props.row.item_code_full
  ?? props.row.itemCodeFull
  ?? '图纸预览',
))

function cleanup() {
  requestId += 1
  try { viewer?.Destroy() } catch {}
  viewer = null
  if (objectUrl.value) URL.revokeObjectURL(objectUrl.value)
  objectUrl.value = ''
}

function isBinaryDxf(buffer: ArrayBuffer): boolean {
  const head = new Uint8Array(buffer.slice(0, 24))
  const marker = 'AutoCAD Binary DXF'
  return marker.split('').every((char, index) => head[index] === char.charCodeAt(0))
}

function isAsciiDxf(text: string): boolean {
  return Boolean(text) && text.includes('SECTION')
}

function fitView() {
  if (!viewer) return
  const bounds = viewer.GetBounds()
  const origin = viewer.GetOrigin()
  if (!bounds || !origin) return
  viewer.FitView(
    bounds.minX - origin.x,
    bounds.maxX - origin.x,
    bounds.minY - origin.y,
    bounds.maxY - origin.y,
    0.08,
  )
  viewer.Render()
}

function progressLabel(phase: string, processed: number, total: number): string {
  const phaseLabel: Record<string, string> = { fetch: '读取', parse: '解析', prepare: '生成预览', font: '载入字体' }
  if (!total) return `${phaseLabel[phase] || '处理'}中…`
  return `${phaseLabel[phase] || '处理'} ${Math.min(100, Math.round(processed / total * 100))}%`
}

async function loadDrawing() {
  cleanup()
  const currentRequest = requestId
  loading.value = true
  error.value = ''
  progress.value = '正在读取 ERP 图纸…'
  try {
    if (!drawingId.value) throw new Error('该行没有可预览的图纸编号')
    const response = await fetch(
      `/api/erp-design-uploads/${props.sessionId}/drawings/${drawingId.value}/preview`,
      { credentials: 'same-origin' },
    )
    if (!response.ok) {
      let message = `读取图纸失败（${response.status}）`
      try {
        const value = await response.json()
        message = value?.error?.message || value?.detail?.message || value?.detail || value?.message || message
      } catch {}
      throw new Error(message)
    }
    const blob = await response.blob()
    if (currentRequest !== requestId) return
    const mediaType = (response.headers.get('content-type') || blob.type || '').toLowerCase()
    if (mediaType.includes('application/json')) {
      let message = 'ERP 返回了错误信息而不是图纸预览'
      try {
        const value = JSON.parse(await blob.text())
        message = value?.msg || value?.detail || value?.message || message
      } catch {}
      throw new Error(message)
    }
    if (blob.size < 64) throw new Error('预览图为空或文件不完整，请下载原图核对')
    if (mediaType.startsWith('image/')) {
      displayKind.value = 'image'
      objectUrl.value = URL.createObjectURL(blob)
      return
    }
    if (mediaType.includes('pdf')) {
      displayKind.value = 'pdf'
      objectUrl.value = URL.createObjectURL(blob)
      return
    }
    const buffer = await blob.arrayBuffer()
    const binaryDxf = isBinaryDxf(buffer)
    const dxfText = binaryDxf ? '' : new TextDecoder().decode(buffer)
    if (!binaryDxf && !isAsciiDxf(dxfText)) throw new Error('预览文件不是有效的 DXF，请下载原图核对')
    objectUrl.value = URL.createObjectURL(new Blob([binaryDxf ? buffer : dxfText], { type: 'application/dxf' }))
    displayKind.value = 'dxf'
    await nextTick()
    if (!container.value) throw new Error('图纸预览容器尚未就绪')
    const [{ DxfViewer }, THREE] = await Promise.all([import('dxf-viewer'), import('three')])
    if (currentRequest !== requestId || !container.value) return
    viewer = new DxfViewer(container.value, {
      autoResize: true,
      antialias: true,
      clearColor: new THREE.Color(0x101820),
      blackWhiteInversion: true,
    })
    if (!viewer.HasRenderer()) throw new Error('无法创建 WebGL 渲染器，请检查浏览器设置')
    await viewer.Load({
      url: objectUrl.value,
      progressCbk: (phase, processed, total) => {
        progress.value = progressLabel(phase, processed, total)
      },
    })
    fitView()
  } catch (value: any) {
    if (currentRequest === requestId) error.value = value?.message || '图纸预览失败'
  } finally {
    if (currentRequest === requestId) {
      loading.value = false
      progress.value = ''
    }
  }
}

watch(() => [props.sessionId, drawingId.value], () => void loadDrawing(), { immediate: true })
onBeforeUnmount(cleanup)
</script>

<template>
  <div class="erp-drawing-preview-shade" @click.self="$emit('close')">
    <section class="erp-drawing-preview-modal" role="dialog" aria-modal="true" aria-label="图纸预览">
      <header>
        <div><strong>图纸预览</strong><span>{{drawingName}}</span></div>
        <div>
          <button v-if="displayKind==='dxf'&&!loading&&!error" type="button" @click="fitView">适合窗口</button>
          <button type="button" aria-label="关闭图纸预览" @click="$emit('close')">×</button>
        </div>
      </header>
      <div class="erp-drawing-preview-body">
        <div v-if="displayKind==='dxf'" ref="container" class="erp-drawing-dxf"></div>
        <img v-else-if="displayKind==='image'&&objectUrl" :src="objectUrl" :alt="drawingName">
        <iframe v-else-if="displayKind==='pdf'&&objectUrl" :src="objectUrl" :title="drawingName"></iframe>
        <div v-if="loading" class="erp-drawing-preview-status"><span></span>{{progress||'图纸载入中…'}}</div>
        <div v-if="error" class="erp-drawing-preview-status is-error">
          <p>{{error}}</p><button type="button" @click="loadDrawing">重新加载</button>
        </div>
      </div>
    </section>
  </div>
</template>
