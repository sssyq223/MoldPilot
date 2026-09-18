<script setup lang="ts">
import {computed,nextTick,ref,watch} from 'vue'
import {Bell,Check,GitBranchPlus,Plus,UserRound} from 'lucide-vue-next'

type Point={x:number;y:number}
const props=defineProps<{nodes:any[];users:any[];selectedIndex:number;storageKey:string}>()
const emit=defineEmits<{select:[index:number];add:[];addPerson:[userId:string];connect:[sourceIndex:number,targetKey:string];assign:[index:number,userId:string];unassign:[index:number,userId:string];error:[message:string]}>()
const positions=ref<Record<string,Point>>({})
const connecting=ref<number|null>(null)
const lineMode=ref(false)
const NODE_W=128,NODE_H=108,NODE_PORT_Y=23
let drag:{index:number;dx:number;dy:number}|null=null

const activeUsers=computed(()=>props.users.filter((user:any)=>user.active))
const connectionHint=computed(()=>connecting.value!==null?`已选择“${props.nodes[connecting.value]?.name||'起点'}”，请点击后续人物或审批结束`:lineMode.value?'连线模式：请先点击起点人物':'')
function defaultPoint(index:number):Point{return{x:120+index*194,y:146}}
function point(index:number){const node=props.nodes[index];return positions.value[node?.key]||defaultPoint(index)}
function endPoint(){const maxX=Math.max(120,...props.nodes.map((_:any,index:number)=>point(index).x));return{x:maxX+204,y:154}}
const canvasWidth=computed(()=>Math.max(920,endPoint().x+100,...props.nodes.map((_:any,index:number)=>point(index).x+NODE_W+80)))
const canvasHeight=computed(()=>Math.max(430,...props.nodes.map((_:any,index:number)=>point(index).y+NODE_H+120)))
function taskPort(index:number,side:'input'|'output'){const p=point(index);return{x:p.x+NODE_W/2+(side==='output'?25:-25),y:p.y+NODE_PORT_Y}}
const edges=computed(()=>{
 const list:any[]=[]
 if(props.nodes.length)list.push({key:'start',source:{x:78,y:169},target:taskPort(0,'input'),label:''})
 props.nodes.forEach((node:any,index:number)=>{
  const source=taskPort(index,'output')
  if(node.routes?.length){
   const seen=new Set<string>()
   for(const route of node.routes){if(seen.has(route.target))continue;seen.add(route.target);const targetIndex=props.nodes.findIndex((item:any)=>item.key===route.target),end=endPoint();list.push({key:`${node.key}:route:${route.target}`,source,target:route.target==='end'?{x:end.x+12,y:end.y+15}:taskPort(targetIndex,'input'),label:'条件'})}
   if(!seen.has(node.default_target)){const targetIndex=props.nodes.findIndex((item:any)=>item.key===node.default_target),end=endPoint();list.push({key:`${node.key}:default`,source,target:node.default_target==='end'?{x:end.x+12,y:end.y+15}:taskPort(targetIndex,'input'),label:'默认'})}
  }else{
   const targetKey=props.nodes[index+1]?.key||'end',end=endPoint()
   list.push({key:`${node.key}:next`,source,target:targetKey==='end'?{x:end.x+12,y:end.y+15}:taskPort(index+1,'input'),label:''})
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
 if((event.target as Element).closest('button'))return
 const p=point(index);drag={index,dx:event.clientX-p.x,dy:event.clientY-p.y};(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
}
function moveDrag(event:PointerEvent){if(!drag)return;const node=props.nodes[drag.index];if(!node)return;positions.value={...positions.value,[node.key]:{x:Math.max(92,event.clientX-drag.dx),y:Math.max(74,event.clientY-drag.dy)}}}
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
 positions.value={...positions.value,[node.key]:{x:Math.max(92,event.clientX-rect.left-NODE_W/2),y:Math.max(74,event.clientY-rect.top-16)}}
 saveLayout()
}
function assignedUsers(node:any){return(node.users||[]).map((id:string)=>props.users.find((user:any)=>user.id===id)).filter(Boolean)}
function initials(name:string){return name.trim().slice(0,1)||'人'}
function resetLayout(){positions.value={};saveLayout()}
</script>

<template>
 <section class="workflow-designer" aria-label="审批流程画布">
  <aside class="workflow-palette">
   <div class="workflow-palette-head"><strong>流程节点</strong><small>拖放式编排</small></div>
   <div class="workflow-node-types">
    <button type="button" @click="emit('add')"><span class="human"><UserRound :size="19"/></span><span><strong>人工审批</strong><small>人员审批任务</small></span></button>
    <button type="button" @click="toggleLineMode"><span class="gateway"><GitBranchPlus :size="17"/></span><span><strong>条件判断</strong><small>添加分支线路</small></span></button>
    <button type="button" disabled title="站内知会执行节点尚待后端接入"><span class="notice"><Bell :size="18"/></span><span><strong>站内知会</strong><small>发送到工作台消息通知</small></span></button>
   </div>
   <div class="workflow-palette-head"><strong>人员</strong><small>拖到审批节点</small></div>
   <div class="workflow-person-list"><button v-for="user in activeUsers" :key="user.id" type="button" class="workflow-person-chip" draggable="true" @dragstart="dragUser($event,user.id)"><span>{{initials(user.display_name)}}</span><span><strong>{{user.display_name}}</strong><small>{{user.department||'未设置部门'}}</small></span></button></div>
  </aside>
  <div class="workflow-canvas-shell">
   <header class="workflow-canvas-toolbar"><div><strong>BPM 审批画布</strong><small>拖入人员生成节点；普通点击编辑，连线时依次选择起点和终点</small></div><div><button type="button" :class="{active:lineMode}" @click="toggleLineMode"><GitBranchPlus :size="15"/>{{lineMode?'退出连线':'连接节点'}}</button><button type="button" @click="resetLayout">自动排列</button><button type="button" class="primary" @click="emit('add')"><Plus :size="15"/>空审批节点</button></div></header>
   <div class="workflow-canvas-scroll">
    <div class="workflow-canvas" :style="{width:canvasWidth+'px',height:canvasHeight+'px'}" @pointermove="moveDrag" @pointerup="stopDrag" @pointercancel="stopDrag" @dragover.prevent @drop.prevent="dropPersonOnCanvas">
     <svg class="workflow-connectors" :width="canvasWidth" :height="canvasHeight" aria-hidden="true"><defs><marker id="workflow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z"/></marker></defs><g v-for="edge in edges" :key="edge.key"><path :d="connector(edge)"/><text v-if="edge.label" :x="edgeLabel(edge).x" :y="edgeLabel(edge).y">{{edge.label}}</text></g></svg>
     <div class="workflow-canvas-guide" :class="{active:lineMode}"><template v-if="connectionHint"><strong>{{connectionHint}}</strong></template><template v-else><span><b>1</b> 拖入人员</span><i>→</i><span><b>2</b> 连接节点</span><i>→</i><span><b>3</b> 点击节点编辑</span></template></div>
     <button type="button" class="workflow-event workflow-start-event" aria-label="申请提交"><span></span><small>申请提交</small></button>
     <article v-for="(node,index) in nodes" :key="node.key" class="workflow-canvas-node" :class="{selected:selectedIndex===index,connecting:connecting===index}" :style="{left:point(index).x+'px',top:point(index).y+'px'}" @pointerdown="startDrag($event,index)" @click="selectNode(index)" @dragover.prevent.stop @drop.prevent.stop="dropUser($event,index)">
      <button type="button" class="workflow-node-connect" :title="connecting===index?'取消连线':'从此节点添加线路'" @click.stop="beginConnect(index)"><GitBranchPlus :size="13"/></button>
      <div class="workflow-bpm-person"><UserRound :size="27"/></div>
      <strong class="workflow-bpm-node-name">{{node.name}}</strong>
      <small>{{node.mode==='ALL'?'会签':node.mode==='ANY'?'或签':'候选领取'}}{{node.routes?.length?' · 条件分支':''}}</small>
      <div class="workflow-node-people"><span v-if="node.assignment" class="rule-person">规则选人</span><template v-else><button v-for="user in assignedUsers(node).slice(0,2)" :key="user.id" type="button" :title="'移除 '+user.display_name" @click.stop="emit('unassign',index,user.id)"><span>{{initials(user.display_name)}}</span>{{user.display_name}}</button><span v-if="!assignedUsers(node).length" class="empty-person">拖入人员</span><span v-else-if="assignedUsers(node).length>2" class="more-person">+{{assignedUsers(node).length-2}}</span></template></div>
      <span v-if="node.routes?.length" class="workflow-route-diamond" title="包含条件分支"><GitBranchPlus :size="11"/></span>
      <i class="workflow-node-port input"/><i class="workflow-node-port output"/>
     </article>
     <button type="button" class="workflow-event workflow-end-event" :style="{left:endPoint().x+'px',top:endPoint().y+'px'}" aria-label="审批结束" @click="connectEnd"><span><Check :size="14"/></span><small>审批结束</small></button>
    </div>
   </div>
  </div>
 </section>
</template>
