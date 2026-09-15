<script setup lang="ts">
import {ref,onUnmounted} from 'vue'
import {FileText,Download,Image as ImageIcon,X} from 'lucide-vue-next'
const props=defineProps<{file:any}>()
const emit=defineEmits<{error:[message:string]}>()
const imageUrl=ref(''),busy=ref(false)
let alive=true
onUnmounted(()=>{alive=false;if(imageUrl.value)URL.revokeObjectURL(imageUrl.value)})
function closePreview(){if(imageUrl.value)URL.revokeObjectURL(imageUrl.value);imageUrl.value=''}
async function read(preview=false){
 busy.value=true
 try{
  const response=await fetch('/api/files/'+(props.file.file_id||props.file.id)+'/content'+(preview?'?preview=true':''),{credentials:'same-origin'})
  if(!response.ok){const body=await response.json();throw new Error(body.error?.message||'文件读取失败')}
  const data=await response.blob();if(!alive)return
  const url=URL.createObjectURL(data)
  if(preview){closePreview();imageUrl.value=url}
  else{const link=document.createElement('a');link.href=url;link.download=props.file.filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
 }catch(e:any){if(alive)emit('error',e.message)}finally{busy.value=false}
}
</script>
<template>
<article class="file-material">
 <div class="file-material-heading"><FileText :size="18"/><div><strong>{{file.title||file.filename}}</strong><small>{{file.title?file.filename+' · ':''}}{{Math.ceil(file.size/1024)}} KB<span v-if="file.version"> · 第 {{file.version}} 版 · {{file.is_current?'当前版本':'历史版本'}}</span></small></div></div>
 <div class="file-material-actions"><button v-if="['image/png','image/jpeg'].includes(file.media_type)" type="button" :disabled="busy" @click="imageUrl?closePreview():read(true)"><ImageIcon :size="14"/>{{imageUrl?'收起图片':'查看图片'}}</button><button type="button" :disabled="busy" @click="read(false)"><Download :size="14"/>下载原件</button></div>
 <div v-if="imageUrl" class="file-preview"><img :src="imageUrl" :alt="file.filename"/><button type="button" class="icon-button" aria-label="关闭图片预览" @click="closePreview"><X :size="16"/></button></div>
</article>
</template>
<style scoped>
.file-material{border:1px solid var(--border);border-radius:7px;padding:12px;margin:10px 0;min-width:0}.file-material-heading{display:flex;gap:10px;align-items:flex-start}.file-material-heading>div{min-width:0}.file-material-heading strong{font-size:13px;overflow-wrap:anywhere}.file-material-heading small{color:var(--muted);font-size:11px}.file-material-actions{display:flex;gap:10px;margin-top:10px}.file-material-actions button{font-size:12px;padding:5px 10px}.file-preview{position:relative;margin-top:12px}.file-preview img{max-width:100%;max-height:60vh;object-fit:contain}.file-preview button{position:absolute;right:4px;top:4px;background:var(--surface)}
</style>
