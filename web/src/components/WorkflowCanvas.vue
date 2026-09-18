<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {Check,GitBranchPlus,Plus,Trash2,UserRound} from 'lucide-vue-next'

type Point={x:number;y:number}
const props=defineProps<{nodes:any[];users:any[];selectedIndex:number;storageKey:string}>()
const emit=defineEmits<{select:[index:number];add:[];connect:[sourceIndex:number,targetKey:string];assign:[index:number,userId:string];unassign:[index:number,userId:string];error:[message:string]}>()
const positions=ref<Record<string,Point>>({})
const connecting=ref<number|null>(null)
const lineMode=ref(false)
const NODE_W=188,NODE_H=82
let drag:{index:number;dx:number;dy:number}|null=null

const activeUsers=computed(()=>props.users.filter((user:any)=>user.active))
function defaultPoint(index:number):Point{return{x:118+index*238,y:154+(index%2)*12}}
function point(index:number){const node=props.nodes[index];return positions.value[node?.key]||defaultPoint(index)}
function endPoint(){const maxX=Math.max(118,...props.nodes.map((_:any,index:number)=>point(index).x));return{x:maxX+256,y:174}}
const canvasWidth=computed(()=>Math.max(920,endPoint().x+110,...props.nodes.map((_:any,index:number)=>point(index).x+NODE_W+90)))
const canvasHeight=computed(()=>Math.max(430,...props.nodes.map((_:any,index:number)=>point(index).y+NODE_H+120)))
function targetPoint(key:string){if(key==='end')return endPoint();const index=props.nodes.findIndex((node:any)=>node.key===key);return index>=0?point(index):endPoint()}
const edges=computed(()=>{
 const list:any[]=[]
 if(props.nodes.length)list.push({key:'start',source:{x:70,y:195},target:{x:point(0).x,y:point(0).y+NODE_H/2},label:''})
 props.nodes.forEach((node:any,index:number)=>{
  const sourcePoint=point(index),source={x:sourcePoint.x+NODE_W,y:sourcePoint.y+NODE_H/2}
  if(node.routes?.length){
   const seen=new Set<string>()
   for(const route of node.routes){if(seen.has(route.target))continue;seen.add(route.target);const target=targetPoint(route.target);list.push({key:`${node.key}:route:${route.target}`,source,target:{x:target.x,y:target.y+(route.target==='end'?21:NODE_H/2)},label:'条件'})}
   if(!seen.has(node.default_target)){const target=targetPoint(node.default_target);list.push({key:`${node.key}:default`,source,target:{x:target.x,y:target.y+(node.default_target==='end'?21:NODE_H/2)},label:'默认'})}
  }else{
   const targetKey=props.nodes[index+1]?.key||'end',target=targetPoint(targetKey)
   list.push({key:`${node.key}:next`,source,target:{x:target.x,y:target.y+(targetKey==='end'?21:NODE_H/2)},label:''})
  }
 })
 return list
})
function curve(edge:any){const bend=Math.max(55,Math.abs(edge.target.x-edge.source.x)*.42);return`M ${edge.source.x} ${edge.source.y} C ${edge.source.x+bend} ${edge.source.y}, ${edge.target.x-bend} ${edge.target.y}, ${edge.target.x} ${edge.target.y}`}
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
function assignedUsers(node:any){return(node.users||[]).map((id:string)=>props.users.find((user:any)=>user.id===id)).filter(Boolean)}
function initials(name:string){return name.trim().slice(0,1)||'人'}
function resetLayout(){positions.value={};saveLayout()}
</script>

<template>
 <section class="workflow-designer" aria-label="审批流程画布">
  <aside class="workflow-palette">
   <div class="workflow-palette-head"><strong>人员</strong><small>拖到审批节点</small></div>
   <div class="workflow-person-list"><button v-for="user in activeUsers" :key="user.id" type="button" class="workflow-person-chip" draggable="true" @dragstart="dragUser($event,user.id)"><span>{{initials(user.display_name)}}</span><span><strong>{{user.display_name}}</strong><small>{{user.department||'未设置部门'}}</small></span></button></div>
  </aside>
  <div class="workflow-canvas-shell">
   <header class="workflow-canvas-toolbar"><div><strong>BPM 审批画布</strong><small>{{connecting!==null?'请选择后续节点作为线路目标':lineMode?'请选择线路起点':'拖动节点调整布局，选择节点编辑属性'}}</small></div><div><button type="button" :class="{active:lineMode}" @click="toggleLineMode"><GitBranchPlus :size="15"/>{{lineMode?'取消连线':'添加线路'}}</button><button type="button" @click="resetLayout">自动排列</button><button type="button" class="primary" @click="emit('add')"><Plus :size="15"/>审批节点</button></div></header>
   <div class="workflow-canvas-scroll">
    <div class="workflow-canvas" :style="{width:canvasWidth+'px',height:canvasHeight+'px'}" @pointermove="moveDrag" @pointerup="stopDrag" @pointercancel="stopDrag">
     <svg class="workflow-connectors" :width="canvasWidth" :height="canvasHeight" aria-hidden="true"><defs><marker id="workflow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z"/></marker></defs><g v-for="edge in edges" :key="edge.key"><path :d="curve(edge)"/><text v-if="edge.label" :x="edgeLabel(edge).x" :y="edgeLabel(edge).y">{{edge.label}}</text></g></svg>
     <button type="button" class="workflow-event workflow-start-event" aria-label="申请提交"><span></span><small>申请提交</small></button>
     <article v-for="(node,index) in nodes" :key="node.key" class="workflow-canvas-node" :class="{selected:selectedIndex===index,connecting:connecting===index}" :style="{left:point(index).x+'px',top:point(index).y+'px'}" @pointerdown="startDrag($event,index)" @click="selectNode(index)" @dragover.prevent @drop.prevent="dropUser($event,index)">
      <header><span><UserRound :size="15"/>{{node.name}}</span><button type="button" :title="connecting===index?'取消连线':'从此节点添加线路'" @click.stop="beginConnect(index)"><GitBranchPlus :size="14"/></button></header>
      <small>{{node.mode==='ALL'?'会签':node.mode==='ANY'?'或签':'候选领取'}} · {{node.routes?.length?'条件分支':'顺序流转'}}</small>
      <div class="workflow-node-people"><span v-if="node.assignment" class="rule-person">规则选人</span><template v-else><button v-for="user in assignedUsers(node)" :key="user.id" type="button" :title="'移除 '+user.display_name" @click.stop="emit('unassign',index,user.id)"><span>{{initials(user.display_name)}}</span>{{user.display_name}}</button><span v-if="!assignedUsers(node).length" class="empty-person">拖入审批人员</span></template></div>
      <i class="workflow-node-port input"/><i class="workflow-node-port output"/>
     </article>
     <button type="button" class="workflow-event workflow-end-event" :style="{left:endPoint().x+'px',top:endPoint().y+'px'}" aria-label="审批结束" @click="connectEnd"><span><Check :size="14"/></span><small>审批结束</small></button>
    </div>
   </div>
  </div>
 </section>
</template>
