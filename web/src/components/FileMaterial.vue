<script setup lang="ts">
import {computed,nextTick,onUnmounted,ref,shallowRef,watch} from 'vue'
import type {PDFDocumentLoadingTask,PDFDocumentProxy} from 'pdfjs-dist'
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import {Download,Eye,FileText,Minus,Paperclip,Plus,X} from 'lucide-vue-next'

const DOCX='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
const XLSX='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
const XLS='application/vnd.ms-excel'
const CSV='text/csv'
const props=defineProps<{file:any,reusable?:boolean}>()
const isDocx=computed(()=>props.file.media_type===DOCX||String(props.file.filename||'').toLowerCase().endsWith('.docx'))
const isSpreadsheet=computed(()=>[XLSX,XLS,CSV].includes(props.file.media_type)||/\.(xlsx|xls|csv)$/i.test(String(props.file.filename||'')))
const emit=defineEmits<{error:[message:string],reuse:[file:any]}>()
const busy=ref(false),previewOpen=ref(false),previewBusy=ref(false),previewError=ref('')
const previewKind=ref<'docx'|'image'|'pdf'|'spreadsheet'|''>(''),previewUrl=ref('')
type SpreadsheetCell={column:number;value:string|number|boolean|null;row_span?:number;column_span?:number;style?:{
 font?:{name?:string;size?:number;bold?:boolean;italic?:boolean;color?:string};fill?:string;
 alignment?:{horizontal?:string|null;vertical?:string|null;wrap?:boolean;rotation?:number;indent?:number};
 border?:Record<string,{style:string;color?:string}>;number_format?:string
}}
type SpreadsheetSheet={name:string;rows:Array<Array<string|number|boolean|null>>;row_count:number;column_count:number;truncated:boolean;column_widths?:number[];row_heights?:Array<number|null>;grid?:SpreadsheetCell[][]}
type SpreadsheetPreview={kind:'spreadsheet';format:'xlsx'|'xls'|'csv';filename:string;sheets:SpreadsheetSheet[];limits?:{max_rows:number;max_columns:number;max_sheets:number}}
const spreadsheetPreview=ref<SpreadsheetPreview|null>(null),spreadsheetSheetIndex=ref(0)
const activeSpreadsheetSheet=computed(()=>spreadsheetPreview.value?.sheets?.[spreadsheetSheetIndex.value]||null)
const spreadsheetTrailingRows=computed(()=>Math.max(0,32-(activeSpreadsheetSheet.value?.grid?.length||activeSpreadsheetSheet.value?.rows.length||0)))
const spreadsheetScroll=ref<HTMLElement|null>(null)
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
 spreadsheetPreview.value=null;spreadsheetSheetIndex.value=0
 releasePreviewUrl()
 void clearPdf()
 if(docxHost.value)docxHost.value.replaceChildren()
}

function excelColumnWidth(value:number|undefined){
 const width=Number.isFinite(value)?Number(value):8.43
 return `${Math.max(24,Math.round(width*7+5))}px`
}

function borderCss(border:{style:string;color?:string}){
 const styles:Record<string,string>={thin:'1px',medium:'2px',thick:'3px',double:'3px',hair:'1px',dashed:'1px dashed',dotted:'1px dotted'}
 if(border.style==='double')return `3px double ${border.color||'#000'}`
 if(border.style==='dashed'||border.style==='dotted')return `${styles[border.style]} ${border.color||'#000'}`
 return `${styles[border.style]||'1px'} solid ${border.color||'#000'}`
}

function spreadsheetCellStyle(cell:SpreadsheetCell){
 const style=cell.style||{},font=style.font||{},alignment=style.alignment||{},border=style.border||{}
 const css:Record<string,string>={
  fontFamily:font.name?`'${font.name.replaceAll("'",'')}',sans-serif`:'inherit',
  fontSize:`${Math.max(8,Number(font.size||11)*1.333)}px`,
  fontWeight:font.bold?'700':'400',fontStyle:font.italic?'italic':'normal',
  color:font.color||'inherit',backgroundColor:style.fill||'transparent',
  textAlign:alignment.horizontal||'left',verticalAlign:alignment.vertical||'bottom',
  whiteSpace:alignment.wrap?'pre-wrap':'nowrap',paddingLeft:`${4+Number(alignment.indent||0)*8}px`,
 }
 if(border.left?.style)css.borderLeft=borderCss(border.left)
 if(border.right?.style)css.borderRight=borderCss(border.right)
 if(border.top?.style)css.borderTop=borderCss(border.top)
 if(border.bottom?.style)css.borderBottom=borderCss(border.bottom)
 if(alignment.rotation)css.transform=`rotate(${alignment.rotation}deg)`
 return css
}

function resetSpreadsheetScroll(){
 nextTick(()=>{if(spreadsheetScroll.value){spreadsheetScroll.value.scrollLeft=0;spreadsheetScroll.value.scrollTop=0}})
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

async function responseJson(render:string){
 const query=new URLSearchParams({preview:'true',render})
 const response=await fetch('/api/files/'+(props.file.file_id||props.file.id)+'/content?'+query.toString(),{credentials:'same-origin'})
 if(!response.ok){
  const body=await response.json().catch(()=>null)
  throw new Error(body?.error?.message||'文件预览读取失败')
 }
 return response.json()
}

function excelColumn(index:number){
 let value=index+1,result=''
 while(value){value--;result=String.fromCharCode(65+value%26)+result;value=Math.floor(value/26)}
 return result
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
  if(isSpreadsheet.value){
   const result=await responseJson('table') as SpreadsheetPreview
   if(!result||result.kind!=='spreadsheet'||!Array.isArray(result.sheets))throw new Error('表格预览格式无效')
   spreadsheetPreview.value=result;spreadsheetSheetIndex.value=0;previewKind.value='spreadsheet';resetSpreadsheetScroll()
  }else if(isDocx.value){
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
    <div class="approval-file-preview-body file-material-preview-body" :class="{'is-spreadsheet-body':previewKind==='spreadsheet'}">
     <div v-if="previewBusy" class="approval-file-preview-status"><span/>{{isDocx?'正在生成高保真预览…':'正在加载附件…'}}</div>
     <div v-else-if="previewError" class="approval-file-preview-status is-error"><strong>暂时无法预览</strong><p>{{previewError}}</p><button type="button" @click="openPreview">重新加载</button></div>
     <div v-else-if="previewKind==='pdf'&&pdfDocument" class="approval-pdf-viewer"><div class="approval-pdf-toolbar"><span>共 {{pdfPageCount}} 页<template v-if="pdfRenderedPages<pdfPageCount"> · 已加载 {{pdfRenderedPages}} 页</template></span><div><button type="button" aria-label="缩小 PDF" :disabled="pdfZoom<=70" @click="pdfZoom-=10"><Minus :size="14"/></button><button type="button" class="approval-pdf-zoom" title="恢复 100%" @click="pdfZoom=100">{{pdfZoom}}%</button><button type="button" aria-label="放大 PDF" :disabled="pdfZoom>=160" @click="pdfZoom+=10"><Plus :size="14"/></button></div></div><div ref="pdfHost" class="approval-pdf-pages"/></div>
     <img v-else-if="previewKind==='image'&&previewUrl" :src="previewUrl" :alt="file.filename"/>
     <div v-else-if="previewKind==='spreadsheet'&&activeSpreadsheetSheet" class="file-spreadsheet-preview">
      <div class="file-spreadsheet-summary"><strong>{{spreadsheetPreview?.format.toUpperCase()}} · {{activeSpreadsheetSheet.name}}</strong><span>{{activeSpreadsheetSheet.row_count}} 行 · {{activeSpreadsheetSheet.column_count}} 列<template v-if="activeSpreadsheetSheet.truncated"> · 预览已截取部分内容</template></span></div>
      <div ref="spreadsheetScroll" class="file-spreadsheet-scroll"><table class="file-spreadsheet-grid" :class="{'is-rich':Boolean(activeSpreadsheetSheet.grid?.length)}">
       <colgroup><col class="file-spreadsheet-row-number"/><col v-for="column in activeSpreadsheetSheet.column_count" :key="column" :style="{width:excelColumnWidth(activeSpreadsheetSheet.column_widths?.[Number(column)-1])}"/></colgroup>
       <thead><tr><th class="file-spreadsheet-corner">#</th><th v-for="column in activeSpreadsheetSheet.column_count" :key="column">{{excelColumn(Number(column)-1)}}</th></tr></thead>
       <tbody v-if="activeSpreadsheetSheet.grid?.length"><tr v-for="(row,rowIndex) in activeSpreadsheetSheet.grid" :key="rowIndex" :style="activeSpreadsheetSheet.row_heights?.[Number(rowIndex)]?{height:`${Number(activeSpreadsheetSheet.row_heights[Number(rowIndex)])*1.333}px`}:undefined"><th>{{Number(rowIndex)+1}}</th><td v-for="cell in row" :key="`${rowIndex}-${cell.column}`" :colspan="cell.column_span||1" :rowspan="cell.row_span||1" :style="spreadsheetCellStyle(cell)">{{cell.value ?? ''}}</td></tr></tbody>
       <tbody v-else><tr v-for="(row,rowIndex) in activeSpreadsheetSheet.rows" :key="rowIndex"><th>{{Number(rowIndex)+1}}</th><td v-for="column in activeSpreadsheetSheet.column_count" :key="column">{{row[Number(column)-1] ?? ''}}</td></tr></tbody>
       <tbody v-if="spreadsheetTrailingRows"><tr v-for="offset in spreadsheetTrailingRows" :key="`empty-${offset}`"><th>{{(activeSpreadsheetSheet.grid?.length||activeSpreadsheetSheet.rows.length)+offset}}</th><td v-for="column in activeSpreadsheetSheet.column_count" :key="column"></td></tr></tbody>
      </table></div>
      <div v-if="(spreadsheetPreview?.sheets?.length||0)>1" class="file-spreadsheet-tabs"><button v-for="(sheet,index) in spreadsheetPreview?.sheets" :key="sheet.name+index" type="button" :class="{active:spreadsheetSheetIndex===index}" @click="spreadsheetSheetIndex=index;resetSpreadsheetScroll()">{{sheet.name}}</button></div>
     </div>
     <div v-show="previewKind==='docx'&&!previewError" ref="docxHost" class="file-docx-preview"/>
    </div>
   </section>
  </div>
 </Teleport>
</article>
</template>
<style scoped>
.file-material{border:1px solid var(--border);border-radius:7px;padding:10px 12px;margin:10px 0;min-width:0}.file-material-main{display:flex;align-items:center;justify-content:space-between;gap:16px;min-width:0}.file-material-heading{display:flex;flex:1;align-items:center;gap:10px;min-width:0}.file-material-heading>div{display:flex;flex:1;align-items:center;gap:8px;min-width:0;white-space:nowrap}.file-material-heading strong{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}.file-material-heading small{flex:0 0 auto;color:var(--muted);font-size:11px;white-space:nowrap}.file-material-actions{display:flex;flex:0 0 auto;gap:10px;justify-content:flex-end}.file-material-actions button{font-size:12px;padding:5px 10px}.approval-file-preview-modal.file-material-preview-modal{width:min(1240px,calc(100vw - 32px));height:min(92vh,940px)}.approval-file-preview-modal.file-material-preview-modal>header{flex-wrap:nowrap}.approval-file-preview-modal.file-material-preview-modal>header>div:first-child{display:flex;align-items:center;gap:10px;min-width:0;white-space:nowrap}.approval-file-preview-modal.file-material-preview-modal>header small{flex:0 0 auto;margin:0}.approval-file-preview-modal.file-material-preview-modal>header strong{display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.approval-file-preview-body.file-material-preview-body{display:block;overflow:auto;padding:20px}.file-material-preview-body>img{display:block;margin:auto}.file-docx-preview{min-height:100%}.file-docx-preview:deep(.docx-wrapper){min-width:max-content;min-height:100%;padding:0;background:transparent}.file-docx-preview:deep(section.docx-page){margin:0 auto 20px;box-shadow:0 4px 22px #0003}
.file-material-preview-body.is-spreadsheet-body{display:flex!important;flex:1 1 auto;flex-direction:column;align-items:stretch;justify-content:stretch;box-sizing:border-box;min-height:0;height:100%;padding:0!important;overflow:hidden!important}.file-spreadsheet-preview{display:flex;flex:1 1 auto;flex-direction:column;min-width:0;min-height:0;height:auto;background:var(--surface)}.file-spreadsheet-summary{display:flex;flex:0 0 auto;align-items:center;gap:12px;min-height:30px;padding:0 10px;border-bottom:1px solid var(--border);font-size:11px}.file-spreadsheet-summary strong{font-size:12px}.file-spreadsheet-summary span{margin-left:auto;color:var(--muted);white-space:nowrap}.file-spreadsheet-scroll{flex:1 1 auto;width:100%;height:0;min-height:0;max-height:100%;overflow:scroll!important;scrollbar-gutter:stable;scrollbar-width:auto}.file-spreadsheet-scroll::-webkit-scrollbar{width:12px;height:12px}.file-spreadsheet-scroll::-webkit-scrollbar-track{background:color-mix(in srgb,var(--bg) 88%,var(--surface))}.file-spreadsheet-scroll::-webkit-scrollbar-thumb{border:3px solid transparent;border-radius:8px;background:color-mix(in srgb,var(--muted) 55%,transparent);background-clip:padding-box}.file-spreadsheet-grid{width:max-content;min-width:100%;table-layout:fixed;border-collapse:separate;border-spacing:0;font-size:11px}.file-spreadsheet-grid th,.file-spreadsheet-grid td{box-sizing:border-box;padding:2px 4px;border-right:1px solid color-mix(in srgb,var(--border) 78%,transparent);border-bottom:1px solid color-mix(in srgb,var(--border) 78%,transparent);text-align:left;white-space:pre-wrap;overflow-wrap:anywhere;vertical-align:top}.file-spreadsheet-grid .file-spreadsheet-row-number{width:44px}.file-spreadsheet-grid thead th{position:sticky;top:0;height:24px;z-index:3;background:color-mix(in srgb,var(--bg) 86%,var(--surface));color:var(--muted);font-weight:600;text-align:center}.file-spreadsheet-grid tbody th{position:sticky;left:0;z-index:2;width:44px;background:color-mix(in srgb,var(--bg) 86%,var(--surface));color:var(--muted);font-weight:500;text-align:center}.file-spreadsheet-grid .file-spreadsheet-corner{left:0;z-index:4;width:44px}.file-spreadsheet-grid.is-rich td{overflow:hidden}.file-spreadsheet-tabs{display:flex;flex:0 0 auto;align-items:center;gap:3px;min-height:30px;padding:3px 8px 0;border-top:1px solid var(--border);overflow-x:auto}.file-spreadsheet-tabs button{height:26px;padding:0 12px;border:0;border-radius:6px 6px 0 0;background:transparent;color:var(--muted);font-size:10px;white-space:nowrap}.file-spreadsheet-tabs button.active{background:var(--surface);color:var(--accent);box-shadow:inset 0 2px var(--accent)}
@media(max-width:520px){.file-material-main{align-items:center;flex-direction:row}.file-material-heading>div{gap:5px}.file-material-heading strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.file-material-actions{align-self:auto}.approval-file-preview-modal.file-material-preview-modal{width:100vw;height:100dvh}.approval-file-preview-modal.file-material-preview-modal>header>div:first-child small{display:none}.approval-file-preview-body.file-material-preview-body{padding:10px}}
</style>
