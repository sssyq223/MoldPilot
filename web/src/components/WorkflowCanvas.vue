<script setup lang="ts">
import {computed,nextTick,ref,watch} from 'vue'
import {Bell,Check,GitBranchPlus,Minus,Plus,UserRound} from 'lucide-vue-next'

type Point={x:number;y:number}
const props=defineProps<{nodes:any[];users:any[];groups:any[];selectedIndex:number;storageKey:string;readOnly?:boolean;simulation?:any}>()
const emit=defineEmits<{select:[index:number];add:[];addPerson:[userId:string];connect:[sourceIndex:number,targetKey:string];assign:[index:number,userId:string];unassign:[index:number,userId:string];error:[message:string]}>()
const positions=ref<Record<string,Point>>({})
const connecting=ref<number|null>(null)
const lineMode=ref(false)
const personQuery=ref(''),departmentFilter=ref(''),roleFilter=ref('')
const zoom=ref(100),panning=ref(false)
const NODE_W=128,NODE_H=108,NODE_PORT_Y=23
let drag:{index:number;dx:number;dy:number;left:number;top:number}|null=null
let pan:{x:number;y:number;left:number;top:number}|null=null

const activeUsers=computed(()=>props.users.filter((user:any)=>user.active))
const departments=computed(()=>props.groups.filter((group:any)=>group.active&&group.kind==='DEPARTMENT'))
const roles=computed(()=>props.groups.filter((group:any)=>group.active&&group.kind==='ROLE'))
const hasPersonFilter=computed(()=>!!(personQuery.value.trim()||departmentFilter.value||roleFilter.value))
const filteredUsers=computed(()=>{
 if(!hasPersonFilter.value)return[]
 const query=personQuery.value.trim().toLocaleLowerCase(),department=departments.value.find((group:any)=>group.id===departmentFilter.value),role=roles.value.find((group:any)=>group.id===roleFilter.value)
 const memberIds=(group:any)=>new Set((group?.members||[]).map((member:any)=>member.user_id))
 const departmentMembers=memberIds(department),roleMembers=memberIds(role)
 return activeUsers.value.filter((user:any)=>(!query||[user.display_name,user.username,user.department].some(value=>String(value||'').toLocaleLowerCase().includes(query)))&&(!department||departmentMembers.has(user.id))&&(!role||roleMembers.has(user.id)))
})
const connectionHint=computed(()=>connecting.value!==null?`已选择“${props.nodes[connecting.value]?.name||'起点'}”，请点击后续人物或审批结束`:lineMode.value?'连线模式：请先点击起点人物':'')
function defaultPoint(index:number):Point{return{x:120+index*194,y:146}}
function point(index:number){const node=props.nodes[index];return positions.value[node?.key]||defaultPoint(index)}
function endPoint(){const maxX=Math.max(120,...props.nodes.map((_:any,index:number)=>point(index).x));return{x:maxX+204,y:154}}
const canvasWidth=computed(()=>Math.max(920,endPoint().x+100,...props.nodes.map((_:any,index:number)=>point(index).x+NODE_W+80)))
const canvasHeight=computed(()=>Math.max(430,...props.nodes.map((_:any,index:number)=>point(index).y+NODE_H+120)))
const zoomScale=computed(()=>zoom.value/100)
const simulationKeys=computed(()=>new Set<string>((props.simulation?.path||[]).map((step:any)=>step.key)))
const simulationEdges=computed(()=>{
 const keys=new Set<string>(),path=props.simulation?.path||[]
 if(path.length)keys.add(`start->${path[0].key}`)
 for(const step of path)if(step.target)keys.add(`${step.key}->${step.target}`)
 return keys
})
const simulationStopKey=computed(()=>props.simulation?.outcome&&props.simulation.outcome!=='ROUTE_VALID'?props.simulation.path?.at(-1)?.key:null)
const simulationEnd=computed(()=>props.simulation?.outcome==='ROUTE_VALID'&&props.simulation.path?.at(-1)?.target==='end')
function taskPort(index:number,side:'input'|'output'){const p=point(index);return{x:p.x+NODE_W/2+(side==='output'?25:-25),y:p.y+NODE_PORT_Y}}
const edges=computed(()=>{
 const list:any[]=[]
 if(props.nodes.length)list.push({key:'start',sourceKey:'start',targetKey:props.nodes[0].key,source:{x:78,y:169},target:taskPort(0,'input'),label:''})
 props.nodes.forEach((node:any,index:number)=>{
  const source=taskPort(index,'output')
  if(node.routes?.length){
   const seen=new Set<string>()
   for(const route of node.routes){if(seen.has(route.target))continue;seen.add(route.target);const targetIndex=props.nodes.findIndex((item:any)=>item.key===route.target),end=endPoint();list.push({key:`${node.key}:route:${route.target}`,sourceKey:node.key,targetKey:route.target,source,target:route.target==='end'?{x:end.x+12,y:end.y+15}:taskPort(targetIndex,'input'),label:'条件'})}
   if(!seen.has(node.default_target)){const targetIndex=props.nodes.findIndex((item:any)=>item.key===node.default_target),end=endPoint();list.push({key:`${node.key}:default`,sourceKey:node.key,targetKey:node.default_target,source,target:node.default_target==='end'?{x:end.x+12,y:end.y+15}:taskPort(targetIndex,'input'),label:'默认'})}
  }else{
   const targetKey=props.nodes[index+1]?.key||'end',end=endPoint()
   list.push({key:`${node.key}:next`,sourceKey:node.key,targetKey,source,target:targetKey==='end'?{x:end.x+12,y:end.y+15}:taskPort(index+1,'input'),label:''})
  }
 })
 return list
})
function connector(edge:any){
 const middleX=(edge.source.x+edge.target.x)/2
 if(Math.abs(edge.source.y-edge.target.y)<2)return`M ${edge.source.x} ${edge.source.y} L ${edge.target.x} ${edge.target.y}`
 return`M ${edge.source.x} ${edge.source.y} L ${middleX} ${edge.source.y} L ${middleX} ${edge.target.y} L ${edge.target.x} ${edge.target.y}`
}
function edgeLabel(edge:any){return{x:(edge.source.x+edge.target.x)/2,y:(edge.source.y+edge.target.y)/2-7}}
function saveLayout(){try{localStorage.setItem(`mold.workflow.canvas.${props.storageKey}`,JSON.stringify(positions.value))}catch{}}
function loadLayout(){try{positions.value=JSON.parse(localStorage.getItem(`mold.workflow.canvas.${props.storageKey}`)||'{}')}catch{positions.value={}}}
watch(()=>props.storageKey,loadLayout,{immediate:true})
watch(()=>props.nodes.map((node:any)=>node.key).join('|'),()=>{const valid=new Set(props.nodes.map((node:any)=>node.key));positions.value=Object.fromEntries(Object.entries(positions.value).filter(([key])=>valid.has(key)));saveLayout()})
function startDrag(event:PointerEvent,index:number){
 if(event.button!==0||(event.target as Element).closest('button'))return
 const canvas=(event.currentTarget as HTMLElement).parentElement as HTMLElement,rect=canvas.getBoundingClientRect(),p=point(index)
 drag={index,dx:(event.clientX-rect.left)/zoomScale.value-p.x,dy:(event.clientY-rect.top)/zoomScale.value-p.y,left:rect.left,top:rect.top};(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
}
function moveDrag(event:PointerEvent){if(!drag)return;const node=props.nodes[drag.index];if(!node)return;positions.value={...positions.value,[node.key]:{x:Math.max(92,(event.clientX-drag.left)/zoomScale.value-drag.dx),y:Math.max(74,(event.clientY-drag.top)/zoomScale.value-drag.dy)}}}
function stopDrag(){if(!drag)return;drag=null;saveLayout()}
function selectNode(index:number){
 if(connecting.value!==null){
  if(index<=connecting.value)return emit('error','线路只能连接到后续审批节点')
  emit('connect',connecting.value,props.nodes[index].key);connecting.value=null;lineMode.value=false;return
 }
 if(lineMode.value){connecting.value=index;emit('select',index);return}
 emit('select',index)
}
function beginConnect(index:number){connecting.value=connecting.value===index?null:index;lineMode.value=connecting.value!==null;emit('select',index)}
function connectEnd(){if(connecting.value===null)return;emit('connect',connecting.value,'end');connecting.value=null;lineMode.value=false}
function toggleLineMode(){lineMode.value=!lineMode.value;if(!lineMode.value)connecting.value=null}
function dragUser(event:DragEvent,userId:string){event.dataTransfer?.setData('application/x-workflow-user',userId);if(event.dataTransfer)event.dataTransfer.effectAllowed='copy'}
function dropUser(event:DragEvent,index:number){const id=event.dataTransfer?.getData('application/x-workflow-user');if(id)emit('assign',index,id)}
async function dropPersonOnCanvas(event:DragEvent){
 const id=event.dataTransfer?.getData('application/x-workflow-user')
 if(!id)return
 const canvas=event.currentTarget as HTMLElement,rect=canvas.getBoundingClientRect()
 emit('addPerson',id)
 await nextTick()
 const node=props.nodes.at(-1)
 if(!node)return
 positions.value={...positions.value,[node.key]:{x:Math.max(92,(event.clientX-rect.left)/zoomScale.value-NODE_W/2),y:Math.max(74,(event.clientY-rect.top)/zoomScale.value-16)}}
 saveLayout()
}
function setZoom(value:number){zoom.value=Math.max(60,Math.min(140,Math.round(value/10)*10))}
function canvasWheel(event:WheelEvent){
 const target=event.currentTarget as HTMLElement
 if(event.shiftKey){target.scrollLeft+=event.deltaY||event.deltaX;return}
 const oldScale=zoomScale.value,nextZoom=Math.max(60,Math.min(140,zoom.value+(event.deltaY<0?10:-10)))
 if(nextZoom===zoom.value)return
 const rect=target.getBoundingClientRect(),offsetX=event.clientX-rect.left,offsetY=event.clientY-rect.top
 const canvasX=(target.scrollLeft+offsetX)/oldScale,canvasY=(target.scrollTop+offsetY)/oldScale
 zoom.value=nextZoom
 nextTick(()=>{const nextScale=zoomScale.value;target.scrollLeft=canvasX*nextScale-offsetX;target.scrollTop=canvasY*nextScale-offsetY})
}
function startPan(event:PointerEvent){if(event.button!==1)return;event.preventDefault();const target=event.currentTarget as HTMLElement;pan={x:event.clientX,y:event.clientY,left:target.scrollLeft,top:target.scrollTop};panning.value=true;target.setPointerCapture(event.pointerId)}
function movePan(event:PointerEvent){if(!pan)return;const target=event.currentTarget as HTMLElement;target.scrollLeft=pan.left-(event.clientX-pan.x);target.scrollTop=pan.top-(event.clientY-pan.y)}
function stopPan(){pan=null;panning.value=false}
function assignedUsers(node:any){return(node.users||[]).map((id:string)=>props.users.find((user:any)=>user.id===id)).filter(Boolean)}
function initials(name:string){return name.trim().slice(0,1)||'人'}
function resetLayout(){positions.value={};saveLayout()}
</script>

<template>
 <section class="workflow-designer" :class="{'read-only':readOnly}" :aria-label="readOnly?'审批流程图':'审批流程画布'">
  <aside v-if="!readOnly" class="workflow-palette">
   <div class="workflow-palette-head"><strong>流程节点</strong><small>拖放式编排</small></div>
   <div class="workflow-node-types">
    <button type="button" @click="emit('add')"><span class="human"><UserRound :size="19"/></span><span><strong>人工审批</strong><small>人员审批任务</small></span></button>
    <button type="button" @click="toggleLineMode"><span class="gateway"><GitBranchPlus :size="17"/></span><span><strong>条件判断</strong><small>添加分支线路</small></span></button>
    <button type="button" disabled title="站内知会执行节点尚待后端接入"><span class="notice"><Bell :size="18"/></span><span><strong>站内知会</strong><small>发送到工作台消息通知</small></span></button>
   </div>
   <div class="workflow-palette-head"><strong>选择人员</strong><small>筛选后拖入</small></div>
   <div class="workflow-person-filter">
    <input v-model="personQuery" type="search" placeholder="搜索姓名或账号" aria-label="搜索人员"/>
    <select v-model="departmentFilter" aria-label="按部门筛选"><option value="">全部部门</option><option v-for="group in departments" :key="group.id" :value="group.id">{{group.name}}</option></select>
    <select v-model="roleFilter" aria-label="按角色筛选"><option value="">全部角色</option><option v-for="group in roles" :key="group.id" :value="group.id">{{group.name}}</option></select>
   </div>
   <div v-if="hasPersonFilter" class="workflow-person-result"><span>找到 {{filteredUsers.length}} 人</span><button type="button" @click="personQuery='';departmentFilter='';roleFilter=''">清除</button></div>
   <div class="workflow-person-list"><button v-for="user in filteredUsers" :key="user.id" type="button" class="workflow-person-chip" draggable="true" @dragstart="dragUser($event,user.id)"><span>{{initials(user.display_name)}}</span><span><strong>{{user.display_name}}</strong><small>{{user.department||'未设置部门'}}</small></span></button><p v-if="!hasPersonFilter" class="workflow-person-empty">搜索姓名，或选择部门、角色后显示人员</p><p v-else-if="!filteredUsers.length" class="workflow-person-empty">没有符合条件的人员</p></div>
  </aside>
  <div class="workflow-canvas-shell">
   <header class="workflow-canvas-toolbar"><div><strong>{{readOnly?'流程图':'BPM 审批画布'}}</strong><small>{{readOnly?'滚轮缩放；按住滚轮拖动；Shift + 滚轮横移':'拖入人员生成节点；滚轮缩放；按住滚轮拖动'}}</small></div><div class="workflow-canvas-actions"><div class="workflow-zoom" aria-label="画布缩放"><button type="button" aria-label="缩小画布" @click="setZoom(zoom-10)"><Minus :size="13"/></button><input v-model.number="zoom" type="range" min="60" max="140" step="10" aria-label="画布缩放比例"/><button type="button" aria-label="放大画布" @click="setZoom(zoom+10)"><Plus :size="13"/></button><button type="button" class="workflow-zoom-value" title="恢复 100%" @click="zoom=100">{{zoom}}%</button></div><slot name="toolbar-actions"/><button v-if="!readOnly" type="button" :class="{active:lineMode}" @click="toggleLineMode"><GitBranchPlus :size="15"/>{{lineMode?'退出连线':'连接节点'}}</button><button type="button" @click="resetLayout">自动排列</button><button v-if="!readOnly" type="button" class="primary" @click="emit('add')"><Plus :size="15"/>空审批节点</button></div></header>
   <slot name="canvas-overlay"/>
   <div class="workflow-canvas-scroll" :class="{panning}" @wheel.prevent="canvasWheel" @pointerdown="startPan" @pointermove="movePan" @pointerup="stopPan" @pointercancel="stopPan">
    <div class="workflow-canvas-stage" :style="{width:canvasWidth*zoomScale+'px',height:canvasHeight*zoomScale+'px'}">
    <div class="workflow-canvas" :style="{width:canvasWidth+'px',height:canvasHeight+'px',transform:'scale('+zoomScale+')'}" @pointermove="readOnly?undefined:moveDrag($event)" @pointerup="readOnly?undefined:stopDrag()" @pointercancel="readOnly?undefined:stopDrag()" @dragover.prevent @drop.prevent="readOnly?undefined:dropPersonOnCanvas($event)">
     <svg class="workflow-connectors" :width="canvasWidth" :height="canvasHeight" aria-hidden="true"><defs><marker id="workflow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z"/></marker><marker id="workflow-arrow-simulated" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z"/></marker></defs><g v-for="edge in edges" :key="edge.key" :class="{simulated:simulationEdges.has(edge.sourceKey+'->'+edge.targetKey)}"><path :d="connector(edge)" :marker-end="simulationEdges.has(edge.sourceKey+'->'+edge.targetKey)?'url(#workflow-arrow-simulated)':'url(#workflow-arrow)'"/><text v-if="edge.label" :x="edgeLabel(edge).x" :y="edgeLabel(edge).y">{{edge.label}}</text></g></svg>
     <div v-if="!readOnly" class="workflow-canvas-guide" :class="{active:lineMode}"><template v-if="connectionHint"><strong>{{connectionHint}}</strong></template><template v-else><span><b>1</b> 拖入人员</span><i>→</i><span><b>2</b> 连接节点</span><i>→</i><span><b>3</b> 点击节点设置加签</span></template></div>
     <button type="button" class="workflow-event workflow-start-event" :class="{simulated:!!simulation}" aria-label="申请提交"><span></span><small>申请提交</small></button>
     <article v-for="(node,index) in nodes" :key="node.key" class="workflow-canvas-node" :class="{selected:!readOnly&&selectedIndex===index,connecting:!readOnly&&connecting===index,simulated:simulationKeys.has(node.key),'simulation-stop':simulationStopKey===node.key}" :style="{left:point(index).x+'px',top:point(index).y+'px'}" @pointerdown="readOnly?undefined:startDrag($event,index)" @click="readOnly?undefined:selectNode(index)" @dragover.prevent.stop @drop.prevent.stop="readOnly?undefined:dropUser($event,index)">
      <button v-if="!readOnly" type="button" class="workflow-node-connect" :title="connecting===index?'取消连线':'从此节点添加线路'" @click.stop="beginConnect(index)"><GitBranchPlus :size="13"/></button>
      <div class="workflow-bpm-person"><UserRound :size="27"/></div>
      <strong class="workflow-bpm-node-name">{{node.name}}</strong>
      <small>{{node.mode==='ALL'?'会签':node.mode==='ANY'?'或签':'候选领取'}}{{node.routes?.length?' · 条件分支':''}}</small>
      <div class="workflow-node-people"><span v-if="node.assignment" class="rule-person">规则选人</span><template v-else><template v-for="user in assignedUsers(node).slice(0,2)" :key="user.id"><span v-if="readOnly" class="workflow-person-label">{{user.display_name}}</span><button v-else type="button" :title="'移除 '+user.display_name" @click.stop="emit('unassign',index,user.id)"><span>{{initials(user.display_name)}}</span>{{user.display_name}}</button></template><span v-if="!assignedUsers(node).length" class="empty-person">{{readOnly?'未指定人员':'拖入人员'}}</span><span v-else-if="assignedUsers(node).length>2" class="more-person">+{{assignedUsers(node).length-2}}</span></template></div>
      <span v-if="node.routes?.length" class="workflow-route-diamond" title="包含条件分支"><GitBranchPlus :size="11"/></span>
      <i v-if="!readOnly" class="workflow-node-port input"/><i v-if="!readOnly" class="workflow-node-port output"/>
     </article>
     <button type="button" class="workflow-event workflow-end-event" :class="{simulated:simulationEnd}" :style="{left:endPoint().x+'px',top:endPoint().y+'px'}" aria-label="审批结束" @click="readOnly?undefined:connectEnd()"><span><Check :size="14"/></span><small>审批结束</small></button>
    </div>
    </div>
   </div>
  </div>
 </section>
</template>
