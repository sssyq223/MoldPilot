<script setup lang="ts">
import {ref} from 'vue'
import {CalendarDays, Plus, Trash2} from 'lucide-vue-next'
import {api,post} from '../api'

const props=defineProps<{calendars:any[]}>()
const emit=defineEmits<{changed:[];error:[message:string]}>()
const opened=ref(false),busy=ref(false),editor=ref<any>(null),holidayText=ref(''),extraText=ref('')
const weekdays=[{value:1,label:'一'},{value:2,label:'二'},{value:3,label:'三'},{value:4,label:'四'},{value:5,label:'五'},{value:6,label:'六'},{value:7,label:'日'}]
function fresh(){return {id:'',calendar_key:'calendar_'+crypto.randomUUID().replaceAll('-',''),name:'工作日历',timezone:'Asia/Shanghai',config:{working_weekdays:[1,2,3,4,5],daily_intervals:[{start:'09:00',end:'12:00'},{start:'13:00',end:'18:00'}],holiday_dates:[],extra_work_dates:[]}}}
function edit(item?:any,copy=false){const value=item?JSON.parse(JSON.stringify(item)):fresh();if(copy){value.id='';delete value.edit_hash;value.status='DRAFT'}editor.value=value;holidayText.value=(value.config.holiday_dates||[]).join('\n');extraText.value=(value.config.extra_work_dates||[]).join('\n')}
function dates(value:string){return [...new Set(value.split(/[\s,，;；]+/).map(item=>item.trim()).filter(Boolean))]}
function toggleWeekday(value:number,checked:boolean){const selected=new Set(editor.value.config.working_weekdays);checked?selected.add(value):selected.delete(value);editor.value.config.working_weekdays=[...selected].sort()}
function addInterval(){editor.value.config.daily_intervals.push({start:'09:00',end:'18:00'})}
async function save(){busy.value=true;try{const value=editor.value,payload={name:value.name,timezone:value.timezone,config:{...value.config,holiday_dates:dates(holidayText.value),extra_work_dates:dates(extraText.value)}};if(value.id)await api(`/workflow-calendars/${value.id}`,{method:'PUT',body:JSON.stringify({...payload,expected_hash:value.edit_hash})});else await post('/workflow-calendars',{calendar_key:value.calendar_key,...payload});editor.value=null;emit('changed')}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
async function publish(item:any){busy.value=true;try{await post(`/workflow-calendars/${item.id}/publish`);emit('changed')}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
</script>

<template>
<div class="workflow-tool-group workflow-calendar-tool">
  <button type="button" class="workflow-tool-button" @click="opened=!opened"><CalendarDays :size="14"/>{{opened?'收起工作日历':'维护工作日历'}}</button>
  <section v-if="opened" class="surface form-stack workflow-tool-panel workflow-calendar-panel" aria-label="工作日历管理">
    <div class="section-heading"><div><strong>版本化工作日历</strong><p class="muted workflow-tool-help">节假日不写死在系统里。流程节点引用已发布版本，实例开始后到期时间保持不变。</p></div><button type="button" @click="edit()"><Plus :size="13"/>新建</button></div>
    <form v-if="editor" class="form-stack workflow-calendar-editor" @submit.prevent="save">
      <div class="form-grid"><label>日历名称<input v-model="editor.name" required maxlength="150"/></label><label>稳定标识<input v-model="editor.calendar_key" required pattern="[a-z][a-z0-9_]{2,79}" :disabled="!!editor.id"/></label><label>时区<input v-model="editor.timezone" required maxlength="64"/></label></div>
      <fieldset><legend>每周工作日</legend><div class="workflow-calendar-weekdays"><label v-for="day in weekdays" :key="day.value"><input type="checkbox" :checked="editor.config.working_weekdays.includes(day.value)" @change="toggleWeekday(day.value,($event.target as HTMLInputElement).checked)"/>周{{day.label}}</label></div></fieldset>
      <fieldset><legend>每日工作时段</legend><div v-for="(interval,index) in editor.config.daily_intervals" :key="index" class="workflow-calendar-interval"><input v-model="interval.start" type="time" required/><span>至</span><input v-model="interval.end" type="time" required/><button v-if="editor.config.daily_intervals.length>1" type="button" aria-label="删除工作时段" @click="editor.config.daily_intervals.splice(index,1)"><Trash2 :size="13"/></button></div><button type="button" @click="addInterval">增加工作时段</button></fieldset>
      <div class="form-grid"><label>休息日（每行一个日期）<textarea v-model="holidayText" rows="3" placeholder="2026-10-01"></textarea></label><label>补班日（每行一个日期）<textarea v-model="extraText" rows="3" placeholder="2026-09-20"></textarea></label></div>
      <div class="actions"><button type="button" @click="editor=null">取消</button><button class="primary" :disabled="busy">保存日历草稿</button></div>
    </form>
    <div v-for="item in props.calendars" :key="item.id" class="grant-row workflow-calendar-row"><span><strong>{{item.name}}</strong><small>{{item.calendar_key}} · 第 {{item.version}} 版 · {{item.status==='PUBLISHED'?'已发布':'草稿'}} · {{item.timezone}}</small></span><div class="actions"><button type="button" @click="edit(item,item.status==='PUBLISHED')">{{item.status==='PUBLISHED'?'复制新版本':'修改'}}</button><button v-if="item.status==='DRAFT'" type="button" :disabled="busy" @click="publish(item)">发布</button></div></div>
    <p v-if="!props.calendars.length&&!editor" class="muted">还没有工作日历。未选择日历的节点继续按自然小时计算。</p>
  </section>
</div>
</template>
