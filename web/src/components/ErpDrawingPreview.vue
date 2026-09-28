<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import type { DxfViewer as DxfViewerInstance } from 'dxf-viewer'
import type { PDFDocumentLoadingTask, PDFDocumentProxy } from 'pdfjs-dist'
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { erpDesignDrawingId, type ErpDesignDrawingResult, type ErpDesignRow } from '../erpDesignPreview'

const props = withDefaults(defineProps<{
  source?: ErpDesignDrawingResult['source']
  sessionId?: number
  row: ErpDesignRow
}>(), { source: 'upload' })

const emit = defineEmits<{ close: [] }>()

const container = ref<HTMLElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
const pdfHost = ref<HTMLElement | null>(null)
const loading = ref(false)
const error = ref('')
const progress = ref('')
const objectUrl = ref('')
const displayKind = ref<'dxf' | 'image' | 'pdf'>('dxf')
const pdfDocument = shallowRef<PDFDocumentProxy | null>(null)
const pdfPageCount = ref(0)
const pdfRenderedPages = ref(0)
const pdfZoom = ref(100)
const pdfContentComplete = ref(false)
const imageNaturalWidth = ref(0)
const imageNaturalHeight = ref(0)
const imageFitScale = ref(1)
const drawingOffsetX = ref(0)
const drawingOffsetY = ref(0)
let viewer: DxfViewerInstance | null = null
let pdfLoadingTask: PDFDocumentLoadingTask | null = null
let pdfRenderGeneration = 0
let pdfPan: { x: number; y: number; left: number; top: number; pointerId: number } | null = null
let pdfResizeObserver: ResizeObserver | null = null
let drawingZoomAnchor: { x: number; y: number; offsetX: number; offsetY: number } | null = null
let drawingDrag: { x: number; y: number; offsetX: number; offsetY: number; pointerId: number } | null = null
let requestId = 0

const drawingId = computed(() => erpDesignDrawingId(props.row))

const drawingName = computed(() => String(
  props.row.drawing_file_name
  ?? props.row.drawingFileName
  ?? props.row.fileName
  ?? props.row.item_code_full
  ?? props.row.itemCodeFull
  ?? '图纸预览',
))

function cleanup() {
  requestId += 1
  try { viewer?.Destroy() } catch {}
  viewer = null
  pdfRenderGeneration += 1
  pdfPageCount.value = 0
  pdfRenderedPages.value = 0
  pdfZoom.value = 100
  pdfContentComplete.value = false
  imageNaturalWidth.value = 0
  imageNaturalHeight.value = 0
  imageFitScale.value = 1
  drawingOffsetX.value = 0
  drawingOffsetY.value = 0
  pdfDocument.value = null
  try { pdfLoadingTask?.destroy() } catch {}
  pdfLoadingTask = null
  pdfResizeObserver?.disconnect()
  pdfResizeObserver = null
  drawingZoomAnchor = null
  pdfPan = null
  drawingDrag = null
  pdfHost.value?.replaceChildren()
  if (objectUrl.value.startsWith('blob:')) URL.revokeObjectURL(objectUrl.value)
  objectUrl.value = ''
}

function startPdfPan(event: PointerEvent) {
  const host = pdfHost.value
  if (!host || event.button !== 0) return
  if (displayKind.value === 'image') {
    drawingDrag = {
      x: event.clientX,
      y: event.clientY,
      offsetX: drawingOffsetX.value,
      offsetY: drawingOffsetY.value,
      pointerId: event.pointerId,
    }
  } else {
    if (host.scrollWidth <= host.clientWidth && host.scrollHeight <= host.clientHeight) return
    pdfPan = {
      x: event.clientX,
      y: event.clientY,
      left: host.scrollLeft,
      top: host.scrollTop,
      pointerId: event.pointerId,
    }
  }
  host.setPointerCapture(event.pointerId)
  host.classList.add('is-panning')
}

function movePdfPan(event: PointerEvent) {
  const host = pdfHost.value
  if (!host) return
  if (displayKind.value === 'image') {
    if (!drawingDrag || event.pointerId !== drawingDrag.pointerId) return
    drawingOffsetX.value = drawingDrag.offsetX + event.clientX - drawingDrag.x
    drawingOffsetY.value = drawingDrag.offsetY + event.clientY - drawingDrag.y
    return
  }
  if (!pdfPan || event.pointerId !== pdfPan.pointerId) return
  host.scrollLeft = pdfPan.left - (event.clientX - pdfPan.x)
  host.scrollTop = pdfPan.top - (event.clientY - pdfPan.y)
}

function endPdfPan(event: PointerEvent) {
  const host = pdfHost.value
  if (!host) return
  const pointerId = displayKind.value === 'image' ? drawingDrag?.pointerId : pdfPan?.pointerId
  if (pointerId == null || event.pointerId !== pointerId) return
  if (host.hasPointerCapture(event.pointerId)) host.releasePointerCapture(event.pointerId)
  host.classList.remove('is-panning')
  if (displayKind.value === 'image') drawingDrag = null
  else pdfPan = null
}

function restoreDrawingZoomAnchor() {
  const host = pdfHost.value
  const anchor = drawingZoomAnchor
  if (!host || !anchor) return
  host.scrollLeft = anchor.x * host.scrollWidth - anchor.offsetX
  host.scrollTop = anchor.y * host.scrollHeight - anchor.offsetY
  drawingZoomAnchor = null
}

function onDrawingWheel(event: WheelEvent) {
  const host = pdfHost.value
  if (!host || !Number.isFinite(event.deltaY) || event.deltaY === 0) return
  const rect = host.getBoundingClientRect()
  drawingZoomAnchor = {
    x: (host.scrollLeft + event.clientX - rect.left) / Math.max(1, host.scrollWidth),
    y: (host.scrollTop + event.clientY - rect.top) / Math.max(1, host.scrollHeight),
    offsetX: event.clientX - rect.left,
    offsetY: event.clientY - rect.top,
  }
  const nextZoom = Math.max(50, Math.min(300, pdfZoom.value + (event.deltaY < 0 ? 10 : -10)))
  if (nextZoom === pdfZoom.value) {
    drawingZoomAnchor = null
    return
  }
  pdfZoom.value = nextZoom
}

function updatePdfViewportState() {
  const host = pdfHost.value
  pdfContentComplete.value = Boolean(
    host
    && host.scrollWidth <= host.clientWidth + 1
    && host.scrollHeight <= host.clientHeight + 1,
  )
}

function imageStyle() {
  const width = imageNaturalWidth.value * imageFitScale.value * (pdfZoom.value / 100)
  const height = imageNaturalHeight.value * imageFitScale.value * (pdfZoom.value / 100)
  return {
    width: `${Math.max(1, Math.floor(width))}px`,
    height: `${Math.max(1, Math.floor(height))}px`,
    transform: `translate(${drawingOffsetX.value}px, ${drawingOffsetY.value}px)`,
  }
}

function fitImage() {
  const image = document.querySelector<HTMLImageElement>('.erp-drawing-image-viewer .erp-drawing-image')
  const host = pdfHost.value
  if (!image || !host || !image.naturalWidth || !image.naturalHeight) return
  imageNaturalWidth.value = image.naturalWidth
  imageNaturalHeight.value = image.naturalHeight
  const availableWidth = Math.max(320, host.clientWidth - 34)
  const availableHeight = Math.max(200, host.clientHeight - 46)
  imageFitScale.value = Math.min(
    1.65,
    availableWidth / image.naturalWidth,
    availableHeight / image.naturalHeight,
  )
  nextTick(updatePdfViewportState)
}

async function renderPdfPages() {
  const pdf = pdfDocument.value
  const host = pdfHost.value
  if (!pdf || !host) return
  const generation = ++pdfRenderGeneration
  pdfRenderedPages.value = 0
  host.replaceChildren()
  const available = Math.max(320, host.clientWidth - 34)
  for (let number = 1; number <= pdf.numPages; number += 1) {
    if (generation !== pdfRenderGeneration) return
    const page = await pdf.getPage(number)
    const unit = page.getViewport({ scale: 1 })
    const availableHeight = Math.max(200, host.clientHeight - 46)
    const fitScale = Math.min(1.65, available / unit.width, availableHeight / unit.height)
    const viewport = page.getViewport({ scale: fitScale * (pdfZoom.value / 100) })
    const ratio = Math.min(window.devicePixelRatio || 1, 2)
    const sheet = document.createElement('section')
    const label = document.createElement('small')
    const canvas = document.createElement('canvas')
    label.textContent = `第 ${number} / ${pdf.numPages} 页`
    sheet.className = 'approval-pdf-page'
    canvas.width = Math.floor(viewport.width * ratio)
    canvas.height = Math.floor(viewport.height * ratio)
    canvas.style.width = `${Math.floor(viewport.width)}px`
    canvas.style.height = `${Math.floor(viewport.height)}px`
    sheet.append(label, canvas)
    host.append(sheet)
    const context = canvas.getContext('2d')
    if (!context) throw new Error('当前浏览器无法创建 PDF 画布')
    progress.value = `正在生成第 ${number} / ${pdf.numPages} 页…`
    await page.render({
      canvas,
      canvasContext: context,
      viewport,
      transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
    }).promise
    if (generation !== pdfRenderGeneration) return
    pdfRenderedPages.value = number
  }
  await nextTick()
  if (generation === pdfRenderGeneration) {
    restoreDrawingZoomAnchor()
    updatePdfViewportState()
  }
}

async function loadPdf(blob: Blob, expectedRequest: number) {
  const pdfjs = await import('pdfjs-dist')
  if (expectedRequest !== requestId) return
  pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker
  pdfLoadingTask = pdfjs.getDocument({ data: new Uint8Array(await blob.arrayBuffer()) })
  const document = await pdfLoadingTask.promise
  if (expectedRequest !== requestId) {
    try { await pdfLoadingTask.destroy() } catch {}
    return
  }
  pdfDocument.value = document
  pdfPageCount.value = pdfDocument.value.numPages
  displayKind.value = 'pdf'
  await nextTick()
  if (pdfHost.value && typeof ResizeObserver !== 'undefined') {
    pdfResizeObserver?.disconnect()
    pdfResizeObserver = new ResizeObserver(() => updatePdfViewportState())
    pdfResizeObserver.observe(pdfHost.value)
  }
  await renderPdfPages()
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
    const standardHardware = props.source === 'standard_hardware'
    if (standardHardware ? !props.row.relativePath : !drawingId.value || !props.sessionId) {
      throw new Error('该行没有可预览的 ERP 图纸引用')
    }
    const previewEndpoint = standardHardware
      ? `/api/erp-design-uploads/standard-hardware/preview?relative_path=${encodeURIComponent(props.row.relativePath)}`
      : `/api/erp-design-uploads/${props.sessionId}/drawings/${drawingId.value}/preview`
    const response = await fetch(
      previewEndpoint,
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
      await nextTick()
      if (pdfHost.value && typeof ResizeObserver !== 'undefined') {
        pdfResizeObserver?.disconnect()
        pdfResizeObserver = new ResizeObserver(() => fitImage())
        pdfResizeObserver.observe(pdfHost.value)
      }
      return
    }
    const header = new Uint8Array(await blob.slice(0, 5).arrayBuffer())
    const isPdf = mediaType.includes('pdf')
      || /\.pdf$/i.test(drawingName.value)
      || new TextDecoder().decode(header) === '%PDF-'
    if (isPdf) {
      await loadPdf(blob, currentRequest)
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

watch(() => [props.source, props.sessionId, drawingId.value, props.row.relativePath], () => void loadDrawing(), { immediate: true })
watch(pdfZoom, () => {
  if (displayKind.value === 'pdf' && pdfDocument.value && !loading.value) void renderPdfPages()
  else if (displayKind.value === 'image' && !loading.value) {
    if (pdfZoom.value === 100) {
      drawingOffsetX.value = 0
      drawingOffsetY.value = 0
    }
    void nextTick(() => {
      restoreDrawingZoomAnchor()
      updatePdfViewportState()
    })
  }
})
function onEscape(event: KeyboardEvent) {
  if (event.key !== 'Escape') return
  event.preventDefault()
  event.stopImmediatePropagation()
  emit('close')
}
onMounted(() => {
  closeButton.value?.focus()
  window.addEventListener('keydown', onEscape, true)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onEscape, true)
  cleanup()
})
</script>

<template>
  <div class="erp-drawing-preview-shade" @click.self="$emit('close')">
    <section class="erp-drawing-preview-modal" role="dialog" aria-modal="true" aria-label="图纸预览">
      <header>
        <div><strong>图纸预览</strong><span>{{drawingName}}</span></div>
        <div>
          <button v-if="displayKind==='dxf'&&!loading&&!error" type="button" @click="fitView">适合窗口</button>
          <button ref="closeButton" type="button" aria-label="关闭图纸预览" @click="$emit('close')">×</button>
        </div>
      </header>
      <div class="erp-drawing-preview-body">
        <div v-if="displayKind==='dxf'" ref="container" class="erp-drawing-dxf"></div>
        <div v-else-if="displayKind==='image'&&objectUrl" class="approval-pdf-viewer erp-drawing-pdf-viewer erp-drawing-image-viewer">
          <div class="approval-pdf-toolbar">
            <span>{{pdfContentComplete ? '图纸已完整显示' : '图纸超出当前视口，请拖动查看'}}</span>
            <div>
              <button type="button" aria-label="缩小图纸" :disabled="pdfZoom<=50" @click="pdfZoom-=10">−</button>
              <button type="button" class="approval-pdf-zoom" title="恢复适合窗口" @click="pdfZoom=100">{{pdfZoom}}%</button>
              <button type="button" aria-label="放大图纸" :disabled="pdfZoom>=300" @click="pdfZoom+=10">＋</button>
            </div>
          </div>
          <div
            ref="pdfHost"
            class="approval-pdf-pages"
            aria-label="图纸预览区域"
            @pointerdown="startPdfPan"
            @pointermove="movePdfPan"
            @pointerup="endPdfPan"
            @pointercancel="endPdfPan"
            @wheel.prevent="onDrawingWheel"
          >
            <img class="erp-drawing-image" :style="imageStyle()" :src="objectUrl" :alt="drawingName" draggable="false" @load="fitImage">
          </div>
        </div>
        <div v-else-if="displayKind==='pdf'&&pdfDocument" class="approval-pdf-viewer erp-drawing-pdf-viewer">
          <div class="approval-pdf-toolbar">
            <span>
              <template v-if="pdfRenderedPages < pdfPageCount">正在生成图纸预览…</template>
              <template v-else-if="pdfContentComplete">图纸已完整显示</template>
              <template v-else>图纸超出当前视口，请拖动查看</template>
            </span>
            <div>
              <button type="button" aria-label="缩小 PDF" :disabled="pdfZoom<=50" @click="pdfZoom-=10">−</button>
              <button type="button" class="approval-pdf-zoom" title="恢复适合窗口" @click="pdfZoom=100">{{pdfZoom}}%</button>
              <button type="button" aria-label="放大 PDF" :disabled="pdfZoom>=300" @click="pdfZoom+=10">＋</button>
            </div>
          </div>
          <div
            ref="pdfHost"
            class="approval-pdf-pages"
            :aria-label="`PDF 图纸，共 ${pdfPageCount} 页`"
            @pointerdown="startPdfPan"
            @pointermove="movePdfPan"
            @pointerup="endPdfPan"
            @pointercancel="endPdfPan"
            @wheel.prevent="onDrawingWheel"
          />
        </div>
        <div v-if="loading" class="erp-drawing-preview-status"><span></span>{{progress||'图纸载入中…'}}</div>
        <div v-if="error" class="erp-drawing-preview-status is-error">
          <p>{{error}}</p><button type="button" @click="loadDrawing">重新加载</button>
        </div>
      </div>
    </section>
  </div>
</template>
