<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {Mail,Play,RefreshCw,RotateCcw,Square,TestTube,Search} from 'lucide-vue-next'
import {api,post,shanghai} from '../api'

const emit=defineEmits<{error:[message:string]}>()
const loading=ref(false),saving=ref(false),busy=ref(''),notice=ref(''),search=ref('')
const accounts=ref<any[]>([]),messages=ref<any[]>([]),showMessages=ref(false)
const draft=ref<any>(emptyAccount())
const defaults=ref<any>({})
const selectedId=computed(()=>draft.value.id||'')
const filteredAccounts=computed(()=>accounts.value.filter(item=>String(item.name||'').toLowerCase().includes(search.value.trim().toLowerCase())))
const statusText=computed(()=>({DISABLED:'已停止',STARTING:'等待后台轮询',HEALTHY:'监听正常',ERROR:'最近一次失败',CONFIG_ERROR:'配置错误'} as Record<string,string>)[draft.value.status]||'未启动')
const keywordRows=computed(()=>Object.entries(draft.value.keywords||{}).map(([key,value])=>({key,value:Array.isArray(value)?value.join(', '):String(value||'')})))

function emptyAccount(){return {name:'企业邮箱',host:'imap.qiye.163.com',port:993,username:'',folder:'INBOX',transport:'ssl',secret_ref:'env://MOLDPILOT_MAIL_PASSWORD',allowed_senders:[],keywords:{weekly:['齐套','周齐套'],plan:['计划','新品']},poll_interval_seconds:60,lookback_days:7,enabled:false,status:'DISABLED',last_error:'',secret_configured:false,cursor:null}}
function selectAccount(row:any){draft.value=JSON.parse(JSON.stringify(row));notice.value='';messages.value=[]}
function newAccount(){draft.value=emptyAccount();notice.value='';messages.value=[]}
function senderText(){return (draft.value.allowed_senders||[]).join(', ')}
function setSenders(value:string){draft.value.allowed_senders=value.split(/[,，\n]/).map(item=>item.trim().toLowerCase()).filter(Boolean)}
function setKeyword(key:string,value:string){draft.value.keywords={...(draft.value.keywords||{}),[key]:value.split(/[,，]/).map(item=>item.trim()).filter(Boolean)}}
async function reload(){loading.value=true;try{const result=await api('/mail-monitor/config');accounts.value=result.accounts||[];defaults.value=result.defaults||{};if(draft.value.id){const current=accounts.value.find(item=>item.id===draft.value.id);current?selectAccount(current):newAccount()}else if(accounts.value.length)selectAccount(accounts.value[0]);else newAccount()}catch(e:any){emit('error',e.message)}finally{loading.value=false}}
async function save(){saving.value=true;notice.value='';try{const payload={id:selectedId.value||undefined,name:draft.value.name,host:draft.value.host,port:draft.value.port,username:draft.value.username,folder:draft.value.folder,transport:draft.value.transport,secret_ref:draft.value.secret_ref,allowed_senders:draft.value.allowed_senders||[],keywords:draft.value.keywords||{},poll_interval_seconds:draft.value.poll_interval_seconds,lookback_days:draft.value.lookback_days};const result=await api('/mail-monitor/config',{method:'PUT',body:JSON.stringify(payload)});const index=accounts.value.findIndex(item=>item.id===result.id);if(index<0)accounts.value.push(result);else accounts.value[index]=result;selectAccount(result);notice.value='邮箱监听配置已保存；保存不会自动启动监听。'}catch(e:any){emit('error',e.message)}finally{saving.value=false}}
async function runAction(action:string,message:string){if(!selectedId.value)return;busy.value=action;notice.value='';try{const result=await post(`/mail-monitor/config/${selectedId.value}/${action}`);selectAccount(result.account||result);notice.value=message;await reload()}catch(e:any){emit('error',e.message)}finally{busy.value=''}}
async function loadMessages(){if(!selectedId.value)return;busy.value='messages';try{const result=await api(`/mail-monitor/config/${selectedId.value}/messages?limit=100`);messages.value=result.items||[];showMessages.value=true}catch(e:any){emit('error',e.message)}finally{busy.value=''}}
onMounted(reload)
</script>

<template>
 <section class="mail-settings">
  <div class="mail-settings-head"><div><h2>企业邮箱</h2><p class="muted">对照 cyqlc 的“系统配置”，这里集中维护 IMAP 账号、白名单、轮询和邮件处理流程。</p></div><small class="muted">{{filteredAccounts.length}} 个邮箱账户</small></div>
  <p class="mail-security-note"><Mail :size="17"/>密码不会写入 MoldPilot 数据库。请把客户端授权码注入运行环境变量，并在“密钥引用”填写对应的 <code>env://</code> 名称。</p>
  <div class="mail-toolbar">
   <label class="mail-search-pill"><Search :size="15"/><input v-model="search" placeholder="搜索邮箱账户" aria-label="搜索邮箱账户"/></label>
   <button type="button" class="mail-toolbar-action" :disabled="saving" @click="newAccount">新增邮箱账户</button>
   <button type="button" class="mail-toolbar-action" :disabled="loading||saving" @click="reload"><RefreshCw :size="15"/>刷新</button>
  </div>
  <div class="mail-settings-layout">
   <aside class="mail-account-list surface">
    <div class="mail-account-list-head"><strong>邮箱账户</strong></div>
    <button v-for="item in filteredAccounts" :key="item.id" type="button" class="mail-account-row" :class="{active:item.id===selectedId}" @click="selectAccount(item)"><span><strong>{{item.name}}</strong><small>{{item.username||'尚未填写账号'}}</small></span><em :class="'mail-status-'+String(item.status||'').toLowerCase()">{{item.status==='HEALTHY'?'正常':item.enabled?'已启用':'已停止'}}</em></button>
    <p v-if="!filteredAccounts.length" class="muted small">还没有保存的企业邮箱账户。</p>
   </aside>
   <section class="mail-editor surface">
    <div v-if="loading" role="status">正在读取邮箱配置…</div>
    <form v-else @submit.prevent="save">
     <div class="mail-editor-title"><div><h3>{{draft.name||'新增监听账户'}}</h3><p class="muted small">状态：{{statusText}}<span v-if="draft.cursor?.last_polled_at"> · 最近轮询 {{shanghai(draft.cursor.last_polled_at)}}</span></p></div><span v-if="draft.secret_configured" class="mail-secret-ok">授权码已配置</span></div>
     <div class="mail-form-grid">
      <label>账户名称<input v-model.trim="draft.name" required maxlength="120" placeholder="例如：供应商企业邮箱"/></label>
      <label>邮箱账号<input v-model.trim="draft.username" required maxlength="255" autocomplete="username" placeholder="planner@your-company.com"/></label>
      <label>IMAP服务器<input v-model.trim="draft.host" required maxlength="255" placeholder="imap.qiye.163.com"/></label>
      <label>端口<input v-model.number="draft.port" type="number" min="1" max="65535" required/></label>
      <label>安全方式<select v-model="draft.transport"><option value="ssl">SSL/TLS（推荐）</option><option value="starttls">STARTTLS</option></select></label>
      <label>邮箱文件夹<input v-model.trim="draft.folder" required placeholder="INBOX"/></label>
      <label class="mail-wide">密钥引用<input v-model.trim="draft.secret_ref" required maxlength="255" placeholder="env://MOLDPILOT_MAIL_PASSWORD"/><small>仅保存引用名；例如在启动 API 和 mail worker 前设置 <code>MOLDPILOT_MAIL_PASSWORD</code>。</small></label>
      <label class="mail-wide">允许发件人 / 域<textarea :value="senderText()" rows="2" placeholder="planner@example.com, @example.com" @input="setSenders(($event.target as HTMLTextAreaElement).value)"/><small>只有命中白名单的邮件才会继续解析。</small></label>
      <label>轮询间隔（秒）<input v-model.number="draft.poll_interval_seconds" type="number" min="15" max="3600" required/></label>
      <label>回溯天数<input v-model.number="draft.lookback_days" type="number" min="0" max="90" required/></label>
     </div>
     <div class="mail-keywords"><div class="mail-subhead"><strong>业务关键词</strong><small class="muted">按逗号分隔；匹配主题、正文表格和附件文件名。</small></div><label v-for="row in keywordRows" :key="row.key">{{row.key}}<input :value="row.value" @input="setKeyword(row.key,($event.target as HTMLInputElement).value)"/></label><button type="button" class="mail-add-keyword" @click="draft.keywords={...(draft.keywords||{}),['new_group']:[]} ">添加关键词组</button></div>
     <div class="mail-actions"><button class="primary" :disabled="saving||!draft.name||!draft.username||!draft.host">{{saving?'正在保存…':'保存配置'}}</button><button type="button" :disabled="!selectedId||!!busy" @click="runAction('test','邮箱连接测试成功')"><TestTube :size="15"/>测试连接</button><button v-if="selectedId&&!draft.enabled" type="button" class="success-button" :disabled="!!busy" @click="runAction('start','监听已启动，后台 worker 将按轮询间隔检查邮箱')"><Play :size="15"/>启动监听</button><button v-if="selectedId&&draft.enabled" type="button" class="warning-button" :disabled="!!busy" @click="runAction('stop','监听已停止')"><Square :size="15"/>停止监听</button><button v-if="selectedId" type="button" :disabled="!!busy" @click="runAction('rescan','扫描游标已重置，下次轮询将重新检查回溯窗口')"><RotateCcw :size="15"/>重新回溯</button><button v-if="selectedId" type="button" :disabled="!!busy" @click="loadMessages">查看处理记录</button></div>
     <p v-if="notice" class="mail-notice" role="status">{{notice}}</p><p v-if="draft.last_error" class="mail-error" role="alert">{{draft.last_error}}</p>
    </form>
   </section>
  </div>
  <section v-if="showMessages" class="mail-messages surface"><div class="mail-subhead"><h3>邮件处理记录</h3><button type="button" @click="showMessages=false">关闭</button></div><div v-if="!messages.length" class="muted">暂无处理记录。</div><table v-else><thead><tr><th>主题</th><th>发件人</th><th>结果</th><th>接收时间</th><th>错误</th></tr></thead><tbody><tr v-for="item in messages" :key="item.id"><td>{{item.subject||'（无主题）'}}</td><td>{{item.sender||'-'}}</td><td>{{item.outcome}}</td><td>{{shanghai(item.received_at)}}</td><td>{{item.error_message||'-'}}</td></tr></tbody></table></section>
 </section>
</template>

<style scoped>
.mail-settings-head,.mail-editor-title,.mail-account-list-head,.mail-subhead{display:flex;align-items:center;justify-content:space-between;gap:12px}.mail-settings-head h2,.mail-editor-title h3,.mail-subhead h3{margin:0}.mail-settings-head p{margin:6px 0 14px}.mail-toolbar-action,.mail-actions button,.mail-subhead button{display:inline-flex;align-items:center;gap:6px}.mail-security-note{display:flex;align-items:flex-start;gap:9px;padding:12px 14px;border:1px solid color-mix(in srgb,var(--accent,#3478e8) 25%,var(--border));border-radius:12px;background:color-mix(in srgb,var(--accent,#3478e8) 7%,var(--surface));font-size:12px;line-height:1.6}.mail-security-note svg{flex:none;color:var(--accent,#3478e8)}.mail-toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:14px 0 18px}.mail-search-pill{height:36px;min-width:260px;max-width:460px;flex:1 1 300px;display:inline-flex;align-items:center;gap:8px;padding:0 12px;border:1px solid color-mix(in srgb,var(--border) 76%,transparent);border-radius:10px;background:color-mix(in srgb,var(--surface) 88%,var(--bg));color:var(--muted)}.mail-search-pill:focus-within{border-color:color-mix(in srgb,var(--accent) 48%,var(--border));box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 12%,transparent)}.mail-search-pill input{height:34px;min-width:0;width:100%;border:0;outline:0;background:transparent;padding:0;font-size:12px;color:var(--text)}.mail-toolbar-action{height:36px;padding:0 12px;border:1px solid color-mix(in srgb,var(--border) 76%,transparent);border-radius:10px;background:color-mix(in srgb,var(--surface) 88%,var(--bg));color:var(--text);font-size:12px}.mail-settings-layout{display:grid;grid-template-columns:250px minmax(0,1fr);gap:14px;margin-top:14px}.mail-account-list,.mail-editor,.mail-messages{border:1px solid var(--border);border-radius:14px;padding:14px;background:var(--surface,#fff)}.mail-account-list{align-self:start}.mail-account-row{display:flex;width:100%;align-items:center;justify-content:space-between;gap:7px;text-align:left;padding:10px;border:1px solid transparent;border-radius:10px;background:transparent;color:inherit}.mail-account-row:hover,.mail-account-row.active{background:color-mix(in srgb,var(--accent,#3478e8) 9%,var(--surface));border-color:color-mix(in srgb,var(--accent,#3478e8) 30%,var(--border))}.mail-account-row span{min-width:0}.mail-account-row strong,.mail-account-row small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.mail-account-row small{font-size:11px;color:var(--muted);margin-top:3px}.mail-account-row em{font-style:normal;font-size:10px;white-space:nowrap;color:var(--muted)}.mail-status-healthy{color:#15803d!important}.mail-status-error,.mail-status-config_error{color:#b42318!important}.mail-editor-title{padding-bottom:12px;border-bottom:1px solid var(--border)}.mail-editor-title p{margin:4px 0 0}.mail-secret-ok{font-size:11px;color:#15803d;background:#15803d14;padding:4px 8px;border-radius:8px}.mail-form-grid{display:grid;grid-template-columns:1fr 1fr;gap:13px;margin-top:15px}.mail-form-grid label,.mail-keywords label{display:flex;flex-direction:column;gap:6px;font-size:12px}.mail-form-grid input,.mail-form-grid select,.mail-form-grid textarea,.mail-keywords input{border:1px solid var(--border);border-radius:8px;background:var(--surface);color:inherit;padding:8px 9px;font:inherit}.mail-form-grid small{color:var(--muted);font-size:11px;line-height:1.4}.mail-wide{grid-column:1/-1}.mail-keywords{margin-top:17px;padding-top:14px;border-top:1px solid var(--border);display:grid;grid-template-columns:1fr 1fr;gap:12px}.mail-subhead{grid-column:1/-1}.mail-subhead small{font-size:11px;font-weight:400}.mail-add-keyword{justify-self:start;font-size:11px}.mail-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:18px}.success-button{color:#15803d}.warning-button{color:#b45309}.mail-notice{color:#15803d;font-size:12px}.mail-error{color:#b42318;background:#b4231810;padding:9px;border-radius:8px;font-size:12px}.mail-messages{margin-top:14px;overflow:auto}.mail-messages table{width:100%;border-collapse:collapse;font-size:12px;margin-top:10px}.mail-messages th,.mail-messages td{text-align:left;padding:8px;border-bottom:1px solid var(--border);white-space:nowrap}.mail-messages td:last-child{white-space:normal;min-width:180px}@media(max-width:850px){.mail-settings-layout{grid-template-columns:1fr}.mail-form-grid,.mail-keywords{grid-template-columns:1fr}.mail-wide{grid-column:auto}.mail-subhead{grid-column:auto}.mail-toolbar{align-items:stretch}.mail-search-pill,.mail-toolbar-action{width:100%;max-width:none;flex:1 1 100%}}
</style>
