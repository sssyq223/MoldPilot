<script setup lang="ts">
import {computed,nextTick,onMounted,onUnmounted,ref,shallowRef,watch} from 'vue'
import type {PDFDocumentLoadingTask,PDFDocumentProxy} from 'pdfjs-dist'
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import {Download,Eye,FileText,Minus,Plus,X} from 'lucide-vue-next'
import {shanghai} from '../../../api'
import BusinessFacts from './BusinessFacts.vue'
const props=defineProps<{detail:any}>()
const open=ref(false)
const previewOpen=ref(false),previewBusy=ref(false),previewError=ref(''),previewUrl=ref('')
const previewKind=ref<'pdf'|'image'|'empty'|'unsupported'>('unsupported')
const pdfHost=ref<HTMLElement|null>(null),pdfDocument=shallowRef<PDFDocumentProxy|null>(null)
const pdfPageCount=ref(0),pdfRenderedPages=ref(0),pdfZoom=ref(100)
let pdfRenderGeneration=0
let pdfLoadingTask:PDFDocumentLoadingTask|null=null
const erpOrderMaterial=computed(()=>props.detail?.snapshot?.detail?.erp_order_material||null)
const erpOrderItems=computed(()=>Array.isArray(erpOrderMaterial.value?.items)?erpOrderMaterial.value.items:[])
const contractOriginal=computed(()=>{
 const files=props.detail?.snapshot?.detail?.attachments||props.detail?.snapshot?.detail?.material_snapshot?.attachments||[]
 return files.find((file:any)=>file.is_current)||files[0]||null
})
function shortHash(value:any){const text=String(value||'');return text.length>24?`${text.slice(0,12)}…${text.slice(-8)}`:text||'—'}
function openDialog(){open.value=true}
function clearPreviewUrl(){if(previewUrl.value)URL.revokeObjectURL(previewUrl.value);previewUrl.value=''}
async function clearPdf(){pdfRenderGeneration++;pdfPageCount.value=0;pdfRenderedPages.value=0;pdfDocument.value=null;if(pdfLoadingTask){await pdfLoadingTask.destroy();pdfLoadingTask=null}}
function closePreview(){previewOpen.value=false;previewBusy.value=false;previewError.value='';clearPreviewUrl();void clearPdf()}
function close(){closePreview();open.value=false}
async function readOriginal(preview=true){
 const file=contractOriginal.value
 if(!file)return
 const response=await fetch(`/api/files/${file.file_id||file.id}/content${preview?'?preview=true':''}`,{credentials:'same-origin'})
 if(!response.ok){let message=preview?'在线预览失败':'文件下载失败';try{const body=await response.json();message=body.error?.message||body.detail?.message||body.detail||message}catch{};throw new Error(message)}
 return response.blob()
}
async function openPreview(){
 const file=contractOriginal.value
 if(!file)return
 previewOpen.value=true;previewBusy.value=true;previewError.value='';clearPreviewUrl();await clearPdf()
 const type=String(file.media_type||'').toLowerCase()
 if(type==='application/pdf')previewKind.value='pdf'
 else if(type.startsWith('image/'))previewKind.value='image'
 else{previewKind.value='unsupported';previewBusy.value=false;return}
 try{
  const blob=await readOriginal(true)
  if(blob&&previewKind.value==='pdf'&&blob.size<256){previewKind.value='empty';return}
  if(blob&&previewKind.value==='pdf'){
   const pdfjs=await import('pdfjs-dist');pdfjs.GlobalWorkerOptions.workerSrc=pdfWorker
   pdfLoadingTask=pdfjs.getDocument({data:new Uint8Array(await blob.arrayBuffer())})
   pdfDocument.value=await pdfLoadingTask.promise;pdfPageCount.value=pdfDocument.value.numPages;previewBusy.value=false
   await nextTick();await renderPdfPages()
  }else if(blob)previewUrl.value=URL.createObjectURL(blob)
 }catch(error:any){previewError.value=error.message||'在线预览失败'}finally{previewBusy.value=false}
}
async function renderPdfPages(){
 const pdfDoc=pdfDocument.value,host=pdfHost.value
 if(!pdfDoc||!host)return
 const generation=++pdfRenderGeneration
 pdfRenderedPages.value=0;host.replaceChildren()
 const available=Math.max(320,host.clientWidth-34)
 for(let number=1;number<=pdfDoc.numPages;number++){
  if(generation!==pdfRenderGeneration)return
  const page=await pdfDoc.getPage(number),unit=page.getViewport({scale:1})
  const fitScale=Math.min(1.65,available/unit.width),scale=fitScale*(pdfZoom.value/100)
  const viewport=page.getViewport({scale}),ratio=Math.min(window.devicePixelRatio||1,2)
  const sheet=document.createElement('section'),label=document.createElement('small'),canvas=document.createElement('canvas')
  label.textContent=`第 ${number} / ${pdfDoc.numPages} 页`;sheet.className='approval-pdf-page';canvas.width=Math.floor(viewport.width*ratio);canvas.height=Math.floor(viewport.height*ratio);canvas.style.width=`${Math.floor(viewport.width)}px`;canvas.style.height=`${Math.floor(viewport.height)}px`
  sheet.append(label,canvas);host.append(sheet)
  const context=canvas.getContext('2d')
  if(!context)throw new Error('当前浏览器无法创建 PDF 画布')
  await page.render({canvas,canvasContext:context,viewport,transform:ratio===1?undefined:[ratio,0,0,ratio,0,0]}).promise
  pdfRenderedPages.value=number
 }
}
watch(pdfZoom,()=>{if(previewOpen.value&&previewKind.value==='pdf'&&pdfDocument.value)void renderPdfPages()})
async function downloadOriginal(){
 try{const blob=await readOriginal(false);if(!blob)return;const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=contractOriginal.value?.filename||'合同原件';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(error:any){previewError.value=error.message||'文件下载失败'}
}
function escape(event:KeyboardEvent){if(event.key==='Escape'){if(previewOpen.value)closePreview();else close()}}
onMounted(()=>window.addEventListener('keydown',escape))
onUnmounted(()=>{window.removeEventListener('keydown',escape);clearPreviewUrl();void clearPdf()})
</script>
<template>
 <button type="button" class="approval-detail-trigger" @click="openDialog"><FileText :size="15"/>{{detail.business_type==='purchase_request'?'查看申请明细':'查看业务材料'}}</button>
 <Teleport to="body"><div v-if="open" class="modal-shade approval-detail-shade" @click.self="close"><section class="modal approval-detail-modal" role="dialog" aria-modal="true" :aria-label="detail.business_type==='purchase_request'?'采购申请明细':'提交审批的业务材料'">
  <header class="approval-detail-modal-head"><div class="approval-detail-modal-title"><span class="approval-detail-modal-icon"><FileText :size="17"/></span><div><small>审批材料</small><h2>{{detail.business_type==='purchase_request'?'采购申请明细':'提交审批的业务材料'}}</h2></div></div><button type="button" class="icon-button" aria-label="关闭申请明细" @click="close"><X :size="18"/></button></header>
  <div class="approval-detail-modal-body">
    <button v-if="contractOriginal" type="button" class="approval-contract-original" @click="openPreview"><span class="approval-contract-original-icon"><FileText :size="15"/></span><div><small>合同原件<span v-if="contractOriginal.version"> · 第 {{contractOriginal.version}} 版</span></small><strong>{{contractOriginal.title||contractOriginal.filename}}</strong></div><span class="approval-contract-original-action"><Eye :size="14"/>在线预览</span></button>
    <section v-if="erpOrderMaterial" class="surface erp-design-material">
     <div class="erp-design-material-head"><div><small>冻结的外部证据</small><h3>ERP 设计订单材料</h3></div><span>审批第 {{erpOrderMaterial.document_revision}} 版</span></div>
     <dl class="erp-design-material-facts"><div><dt>订单号</dt><dd>{{erpOrderMaterial.order_number||erpOrderMaterial.resource_id}}</dd></div><div><dt>模具号</dt><dd>{{erpOrderMaterial.mold_number||'—'}}</dd></div><div><dt>ERP 状态</dt><dd>{{erpOrderMaterial.status||'未提供'}}</dd></div><div><dt>ERP 记录版本</dt><dd>{{erpOrderMaterial.resource_version||'—'}}</dd></div><div><dt>核对时点</dt><dd>{{shanghai(erpOrderMaterial.as_of)}}</dd></div><div><dt>快照哈希</dt><dd :title="erpOrderMaterial.snapshot_hash">{{shortHash(erpOrderMaterial.snapshot_hash)}}</dd></div></dl>
     <div v-if="erpOrderItems.length" class="table-scroll"><table><thead><tr><th>序号</th><th>零件/料号</th><th>名称</th><th>材质</th><th>规格</th><th>数量</th><th>路线</th><th>状态</th></tr></thead><tbody><tr v-for="(item,index) in erpOrderItems" :key="index"><td>{{item.sequence ?? (Number(index)+1)}}</td><td>{{item.part_number||'—'}}</td><td>{{item.part_name||'—'}}</td><td>{{item.material||'—'}}</td><td>{{item.specification||'—'}}</td><td>{{item.quantity??'—'}}</td><td>{{item.route||'—'}}</td><td>{{item.status||'—'}}</td></tr></tbody></table></div>
     <p v-else class="muted">ERP 本次快照未返回可展示的订单明细。</p><p class="muted small">这是提交审批时锁定的证据快照，不代表 ERP 当前实时状态；实时资料请重新调用 ERP 查询工具。</p>
    </section>
    <div v-if="detail.business_type==='purchase_request'" class="table-scroll"><table><thead><tr><th>物料名称</th><th>数量</th><th>单位</th><th>需求日期</th></tr></thead><tbody><tr v-for="(line,i) in detail.snapshot.lines" :key="i"><td>{{line.material_name ?? '无字段权限'}}</td><td>{{line.quantity ?? '—'}}</td><td>{{line.unit ?? '—'}}</td><td>{{line.due_date ?? '—'}}</td></tr></tbody></table></div>
   <BusinessFacts v-else :value="detail.snapshot.detail" :contract-number="detail.snapshot.detail?.contract_number"/>
  </div>
 </section></div></Teleport>
 <Teleport to="body"><div v-if="previewOpen" class="approval-file-preview-shade" @click.self="closePreview"><section class="approval-file-preview-modal" role="dialog" aria-modal="true" aria-label="合同原件在线预览"><header><div><small>合同原件</small><strong>{{contractOriginal?.title||contractOriginal?.filename}}</strong></div><div><button type="button" @click="downloadOriginal"><Download :size="14"/>下载原件</button><button type="button" class="icon-button" aria-label="关闭合同预览" @click="closePreview"><X :size="18"/></button></div></header><div class="approval-file-preview-body"><div v-if="previewBusy" class="approval-file-preview-status"><span/>正在加载合同原件…</div><div v-else-if="previewError" class="approval-file-preview-status is-error"><strong>暂时无法预览</strong><p>{{previewError}}</p><button type="button" @click="openPreview">重新加载</button></div><div v-else-if="previewKind==='pdf'&&pdfDocument" class="approval-pdf-viewer"><div class="approval-pdf-toolbar"><span>共 {{pdfPageCount}} 页<template v-if="pdfRenderedPages<pdfPageCount"> · 已加载 {{pdfRenderedPages}} 页</template></span><div><button type="button" aria-label="缩小 PDF" :disabled="pdfZoom<=70" @click="pdfZoom-=10"><Minus :size="14"/></button><button type="button" class="approval-pdf-zoom" title="恢复 100%" @click="pdfZoom=100">{{pdfZoom}}%</button><button type="button" aria-label="放大 PDF" :disabled="pdfZoom>=160" @click="pdfZoom+=10"><Plus :size="14"/></button></div></div><div ref="pdfHost" class="approval-pdf-pages"/></div><img v-else-if="previewKind==='image'&&previewUrl" :src="previewUrl" :alt="contractOriginal?.filename||'合同原件'"/><div v-else-if="previewKind==='empty'" class="approval-file-preview-status"><FileText :size="30"/><strong>原件没有可预览页面</strong><p>当前 PDF 只有 {{contractOriginal?.size||0}} 字节，未包含浏览器可以呈现的合同页面。</p><p>请上传真实合同 PDF；当前占位原件仍可下载核对。</p><button type="button" @click="downloadOriginal"><Download :size="14"/>下载当前原件</button></div><div v-else class="approval-file-preview-status"><FileText :size="28"/><strong>此格式暂不支持在线预览</strong><p>请下载原件后使用本机应用查看。</p><button type="button" @click="downloadOriginal"><Download :size="14"/>下载原件</button></div></div></section></div></Teleport>
</template>
<style scoped>
.erp-design-material{display:grid;gap:14px;margin-bottom:16px}.erp-design-material-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.erp-design-material-head h3{margin:2px 0 0}.erp-design-material-head>span{font-size:12px;color:var(--muted);white-space:nowrap}.erp-design-material-facts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:0}.erp-design-material-facts div{display:grid;gap:3px}.erp-design-material-facts dt{color:var(--muted);font-size:12px}.erp-design-material-facts dd{margin:0;overflow-wrap:anywhere}.erp-design-material table{min-width:760px}.small{font-size:12px}@media(max-width:760px){.erp-design-material-facts{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
