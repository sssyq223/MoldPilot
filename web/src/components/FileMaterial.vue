<script setup lang="ts">
import {computed,nextTick,onUnmounted,ref,shallowRef,watch} from 'vue'
import type {PDFDocumentLoadingTask,PDFDocumentProxy} from 'pdfjs-dist'
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import {Download,Eye,FileText,Minus,Paperclip,Plus,X} from 'lucide-vue-next'

const DOCX='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
const props=defineProps<{file:any,reusable?:boolean}>()
const isDocx=computed(()=>props.file.media_type===DOCX||String(props.file.filename||'').toLowerCase().endsWith('.docx'))
const emit=defineEmits<{error:[message:string],reuse:[file:any]}>()
const busy=ref(false),previewOpen=ref(false),previewBusy=ref(false),previewError=ref('')
const previewKind=ref<'docx'|'image'|'pdf'|''>(''),previewUrl=ref('')
const docxHost=ref<HTMLElement|null>(null)
const pdfHost=ref<HTMLElement|null>(null),pdfDocument=shallowRef<PDFDocumentProxy|null>(null)
const pdfPageCount=ref(0),pdfRenderedPages=ref(0),pdfZoom=ref(100)
let pdfRenderGeneration=0
let pdfLoadingTask:PDFDocumentLoadingTask|null=null
let alive=true

onUnmounted(()=>{alive=false;releasePreviewUrl();void clearPdf()})

function releasePreviewUrl(){
 if(previewUrl.value)URL.revokeObjectURL(previewUrl.value)
 previewUrl.value=''
}

function closePreview(){
 previewOpen.value=false
 previewError.value=''
 previewKind.value=''
 releasePreviewUrl()
 void clearPdf()
 if(docxHost.value)docxHost.value.replaceChildren()
}

async function clearPdf(){
 pdfRenderGeneration++;pdfPageCount.value=0;pdfRenderedPages.value=0;pdfDocument.value=null
 if(pdfLoadingTask){await pdfLoadingTask.destroy();pdfLoadingTask=null}
}

async function loadPdf(data:Blob){
 const pdfjs=await import('pdfjs-dist');pdfjs.GlobalWorkerOptions.workerSrc=pdfWorker
 pdfLoadingTask=pdfjs.getDocument({data:new Uint8Array(await data.arrayBuffer())})
 pdfDocument.value=await pdfLoadingTask.promise;pdfPageCount.value=pdfDocument.value.numPages
 previewKind.value='pdf';previewBusy.value=false
 await nextTick();await renderPdfPages()
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
  label.textContent=`第 ${number} / ${pdfDoc.numPages} 页`;sheet.className='approval-pdf-page'
  canvas.width=Math.floor(viewport.width*ratio);canvas.height=Math.floor(viewport.height*ratio)
  canvas.style.width=`${Math.floor(viewport.width)}px`;canvas.style.height=`${Math.floor(viewport.height)}px`
  sheet.append(label,canvas);host.append(sheet)
  const context=canvas.getContext('2d')
  if(!context)throw new Error('当前浏览器无法创建 PDF 画布')
  await page.render({canvas,canvasContext:context,viewport,transform:ratio===1?undefined:[ratio,0,0,ratio,0,0]}).promise
  pdfRenderedPages.value=number
 }
}

watch(pdfZoom,()=>{if(previewOpen.value&&previewKind.value==='pdf'&&pdfDocument.value)void renderPdfPages()})

async function responseBlob(preview:boolean,render=''){
 const query=new URLSearchParams()
 if(preview)query.set('preview','true')
 if(render)query.set('render',render)
 const response=await fetch('/api/files/'+(props.file.file_id||props.file.id)+'/content'+(query.size?'?'+query.toString():''),{credentials:'same-origin'})
 if(!response.ok){
  const body=await response.json().catch(()=>null)
  throw new Error(body?.error?.message||'文件读取失败')
 }
 return response.blob()
}

async function downloadOriginal(){
 busy.value=true
 try{
  const data=await responseBlob(false)
  if(!alive)return
  const url=URL.createObjectURL(data),link=document.createElement('a')
  link.href=url;link.download=props.file.filename;link.click()
  setTimeout(()=>URL.revokeObjectURL(url),1000)
 }catch(e:any){if(alive)emit('error',e.message)}finally{busy.value=false}
}

async function openPreview(){
 previewOpen.value=true;previewBusy.value=true;previewError.value='';previewKind.value='';releasePreviewUrl();await clearPdf()
 try{
  if(isDocx.value){
   try{
    const data=await responseBlob(true,'pdf')
    if(!alive)return
    if(data.type!=='application/pdf')throw new Error('高保真预览格式无效')
    await loadPdf(data)
   }catch{
    const data=await responseBlob(true)
    if(!alive)return
    previewKind.value='docx';await nextTick()
    if(!docxHost.value)throw new Error('预览容器未就绪')
    docxHost.value.replaceChildren()
    const {renderAsync}=await import('docx-preview')
    await renderAsync(await data.arrayBuffer(),docxHost.value,undefined,{
     className:'docx-page',inWrapper:true,breakPages:true,ignoreWidth:false,ignoreHeight:false,useBase64URL:true
    })
   }
  }else if(['image/png','image/jpeg'].includes(props.file.media_type)){
   const data=await responseBlob(true);if(!alive)return
   previewKind.value='image';previewUrl.value=URL.createObjectURL(data)
  }else if(props.file.media_type==='application/pdf'){
   const data=await responseBlob(true);if(!alive)return
   await loadPdf(data)
  }else throw new Error('此格式暂不支持在线预览')
 }catch(e:any){if(alive)previewError.value=e.message||'文件预览失败'}finally{previewBusy.value=false}
}
</script>
<template>
<article class="file-material">
 <div class="file-material-main">
  <div class="file-material-heading"><FileText :size="18"/><div><strong>{{file.title||file.filename}}</strong><small>{{file.title?file.filename+' · ':''}}{{Math.ceil(file.size/1024)}} KB<span v-if="file.version"> · 第 {{file.version}} 版 · {{file.is_current?'当前版本':'历史版本'}}</span></small></div></div>
  <div class="file-material-actions"><button type="button" :disabled="busy" @click="openPreview"><Eye :size="14"/>在线预览</button><button v-if="reusable" type="button" :disabled="busy" @click="emit('reuse',file)"><Paperclip :size="14"/>引用到新消息</button></div>
 </div>
 <Teleport to="body">
  <div v-if="previewOpen" class="approval-file-preview-shade" @click.self="closePreview">
   <section class="approval-file-preview-modal file-material-preview-modal" role="dialog" aria-modal="true" :aria-label="(file.title||file.filename)+'在线预览'">
    <header><div><small>附件在线预览</small><strong>{{file.title||file.filename}}</strong></div><div><button type="button" :disabled="busy" @click="downloadOriginal"><Download :size="14"/>下载原件</button><button type="button" class="icon-button" aria-label="关闭附件预览" @click="closePreview"><X :size="18"/></button></div></header>
    <div class="approval-file-preview-body file-material-preview-body">
     <div v-if="previewBusy" class="approval-file-preview-status"><span/>{{isDocx?'正在生成高保真预览…':'正在加载附件…'}}</div>
     <div v-else-if="previewError" class="approval-file-preview-status is-error"><strong>暂时无法预览</strong><p>{{previewError}}</p><button type="button" @click="openPreview">重新加载</button></div>
     <div v-else-if="previewKind==='pdf'&&pdfDocument" class="approval-pdf-viewer"><div class="approval-pdf-toolbar"><span>共 {{pdfPageCount}} 页<template v-if="pdfRenderedPages<pdfPageCount"> · 已加载 {{pdfRenderedPages}} 页</template></span><div><button type="button" aria-label="缩小 PDF" :disabled="pdfZoom<=70" @click="pdfZoom-=10"><Minus :size="14"/></button><button type="button" class="approval-pdf-zoom" title="恢复 100%" @click="pdfZoom=100">{{pdfZoom}}%</button><button type="button" aria-label="放大 PDF" :disabled="pdfZoom>=160" @click="pdfZoom+=10"><Plus :size="14"/></button></div></div><div ref="pdfHost" class="approval-pdf-pages"/></div>
     <img v-else-if="previewKind==='image'&&previewUrl" :src="previewUrl" :alt="file.filename"/>
     <div v-show="previewKind==='docx'&&!previewError" ref="docxHost" class="file-docx-preview"/>
    </div>
   </section>
  </div>
 </Teleport>
</article>
</template>
<style scoped>
.file-material{border:1px solid var(--border);border-radius:7px;padding:10px 12px;margin:10px 0;min-width:0}.file-material-main{display:flex;align-items:center;justify-content:space-between;gap:16px;min-width:0}.file-material-heading{display:flex;flex:1;align-items:center;gap:10px;min-width:0}.file-material-heading>div{display:flex;flex:1;align-items:center;gap:8px;min-width:0;white-space:nowrap}.file-material-heading strong{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}.file-material-heading small{flex:0 0 auto;color:var(--muted);font-size:11px;white-space:nowrap}.file-material-actions{display:flex;flex:0 0 auto;gap:10px;justify-content:flex-end}.file-material-actions button{font-size:12px;padding:5px 10px}.approval-file-preview-modal.file-material-preview-modal{width:min(1240px,calc(100vw - 32px));height:min(92vh,940px)}.approval-file-preview-modal.file-material-preview-modal>header{flex-wrap:nowrap}.approval-file-preview-modal.file-material-preview-modal>header>div:first-child{display:flex;align-items:center;gap:10px;min-width:0;white-space:nowrap}.approval-file-preview-modal.file-material-preview-modal>header small{flex:0 0 auto;margin:0}.approval-file-preview-modal.file-material-preview-modal>header strong{display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.approval-file-preview-body.file-material-preview-body{display:block;overflow:auto;padding:20px}.file-material-preview-body>img{display:block;margin:auto}.file-docx-preview{min-height:100%}.file-docx-preview:deep(.docx-wrapper){min-width:max-content;min-height:100%;padding:0;background:transparent}.file-docx-preview:deep(section.docx-page){margin:0 auto 20px;box-shadow:0 4px 22px #0003}
@media(max-width:520px){.file-material-main{align-items:center;flex-direction:row}.file-material-heading>div{gap:5px}.file-material-heading strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.file-material-actions{align-self:auto}.approval-file-preview-modal.file-material-preview-modal{width:100vw;height:100dvh}.approval-file-preview-modal.file-material-preview-modal>header>div:first-child small{display:none}.approval-file-preview-body.file-material-preview-body{padding:10px}}
</style>
