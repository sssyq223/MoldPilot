<script setup lang="ts">
import { ref, watch } from 'vue'
import { ArrowRightLeft, Check, ChevronDown, FileText, ShieldCheck, Undo2, UserCheck, UserPlus } from 'lucide-vue-next'
import { api, post, shanghai } from '../api'
import FileMaterial from './FileMaterial.vue'
import ApprovalBusinessDetails from '@domain-pack/components/ApprovalBusinessDetails.vue'
import {statusName,numberText} from '@domain-pack/uiText'
import {approvalConfirmationNotice} from '@domain-pack/uiPolicy'
const props = defineProps<{ detail: any }>()
const emit = defineEmits<{ changed: []; error: [message: string] }>()
const comment = ref(''), busy = ref(false), confirmation = ref<any>(null)
const returnTarget = ref('')
const returnTargetOpen = ref(false), returnTargetSelect = ref<HTMLElement|null>(null)
const transferTarget = ref(''), transferReason = ref(''), transferConfirmation = ref<any>(null)
const addSignTarget = ref(''), addSignTiming = ref(''), addSignReason = ref(''), addSignConfirmation = ref<any>(null)
const withdrawReason = ref(''), withdrawConfirmation = ref<any>(null)
const labels: Record<string,string> = { APPROVE:'同意', REJECT:'驳回', RETURN:'退回修改' }
watch(()=>props.detail,(detail)=>{returnTarget.value=detail.return_options?.length===1?detail.return_options[0].key:''},{immediate:true})
function returnTargetName(key:string){return props.detail.return_options?.find((item:any)=>item.key===key)?.name||'目标已变化'}
function pickReturnTarget(key:string){returnTarget.value=key;returnTargetOpen.value=false}
function closeReturnTarget(event:FocusEvent){if(!returnTargetSelect.value?.contains(event.relatedTarget as Node|null))returnTargetOpen.value=false}
async function prepare(decision: string) {
  if (!comment.value.trim()) return emit('error','请填写本次审批意见')
  if (decision==='RETURN'&&!returnTarget.value) return emit('error','请选择退回目标')
  busy.value = true
  try {
    const d = props.detail
    confirmation.value = await post('/approvals/decision-intent', { instance_id:d.id, seat_id:d.seat_id, seat_version:d.seat_version, version:d.version, snapshot_hash:d.snapshot_hash, decision, comment:comment.value, ...(decision==='RETURN'?{return_target_node_key:returnTarget.value}:{}) })
  } catch(e:any) { emit('error',e.message) } finally { busy.value=false }
}
async function confirm() {
  busy.value = true
  try { await post(`/human-actions/${confirmation.value.id}/confirm`, { challenge:confirmation.value.challenge }); confirmation.value=null; comment.value=''; emit('changed') }
  catch(e:any) { emit('error',e.message) } finally { busy.value=false }
}
async function prepareTransfer() {
  if (!transferTarget.value) return emit('error','请选择转交人员')
  if (!transferReason.value.trim()) return emit('error','请填写转交原因')
  busy.value = true
  try {
    const d = props.detail
    transferConfirmation.value = await post('/approval-seat-transfers/intent', {
      instance_id:d.id, seat_id:d.seat_id, seat_version:d.seat_version,
      version:d.version, snapshot_hash:d.snapshot_hash,
      target_user_id:transferTarget.value, reason:transferReason.value,
    })
  } catch(e:any) { emit('error',e.message) } finally { busy.value=false }
}
async function confirmTransfer() {
  busy.value = true
  try {
    await post(`/human-actions/${transferConfirmation.value.id}/confirm`, { challenge:transferConfirmation.value.challenge })
    transferConfirmation.value=null; transferTarget.value=''; transferReason.value=''; emit('changed')
  } catch(e:any) { emit('error',e.message) } finally { busy.value=false }
}
function transferTargetName(id:string) { return props.detail.transfer_options.find((item:any)=>item.id===id)?.display_name||'目标人员' }
async function prepareAddSign() {
  if (!addSignTiming.value) return emit('error','请选择前加签或后加签')
  if (!addSignTarget.value) return emit('error','请选择加签人员')
  if (!addSignReason.value.trim()) return emit('error','请填写加签原因')
  busy.value = true
  try {
    const d = props.detail
    addSignConfirmation.value = await post('/approval-seat-additions/intent', {
      instance_id:d.id, seat_id:d.seat_id, seat_version:d.seat_version,
      version:d.version, snapshot_hash:d.snapshot_hash,
      target_user_id:addSignTarget.value, timing:addSignTiming.value, reason:addSignReason.value,
    })
  } catch(e:any) { emit('error',e.message) } finally { busy.value=false }
}
async function confirmAddSign() {
  busy.value = true
  try {
    await post(`/human-actions/${addSignConfirmation.value.id}/confirm`, { challenge:addSignConfirmation.value.challenge })
    addSignConfirmation.value=null; addSignTarget.value=''; addSignTiming.value=''; addSignReason.value=''; emit('changed')
  } catch(e:any) { emit('error',e.message) } finally { busy.value=false }
}
function addSignTargetName(id:string) { return props.detail.add_sign_options.find((item:any)=>item.id===id)?.display_name||'目标人员' }
function timingName(value:string) { return value==='PRE'?'前加签':'后加签' }
function modeName(value:string) { return value==='ALL'?'会签':value==='ANY'?'或签':'候选领取' }
function approvalActor(history:any) { return history.user.principal_user?`${history.user.name}（代 ${history.user.principal_user.display_name}）`:history.user.name }
function nodeState(index:number) {
  if (props.detail.status==='COMPLETED') return '已通过'
  if (index<props.detail.stage_index) return '已通过'
  if (index>props.detail.stage_index) return '尚未进入'
  return ({RUNNING:'当前节点',REJECTED:'本轮已驳回',RETURNED:'本轮已退回',CANCELLED:'本轮已撤回'} as Record<string,string>)[props.detail.status]||'已结束'
}
async function prepareWithdraw() {
  if (!withdrawReason.value.trim()) return emit('error','请填写撤回原因')
  busy.value=true
  try {
    const d=props.detail
    withdrawConfirmation.value=await post('/approvals/withdraw-intent',{instance_id:d.id,version:d.version,snapshot_hash:d.snapshot_hash,reason:withdrawReason.value})
  } catch(e:any) { emit('error',e.message) } finally { busy.value=false }
}
async function confirmWithdraw() {
  busy.value=true
  try {
    await post(`/human-actions/${withdrawConfirmation.value.id}/confirm`,{challenge:withdrawConfirmation.value.challenge})
    withdrawConfirmation.value=null;withdrawReason.value='';emit('changed')
  } catch(e:any) { emit('error',e.message) } finally { busy.value=false }
}
async function claimApproval() {
  busy.value=true
  try {
    await post(`/approvals/${props.detail.id}/claim`,{version:props.detail.version,snapshot_hash:props.detail.snapshot_hash})
    emit('changed')
  } catch(e:any) { emit('error',e.message) } finally { busy.value=false }
}
</script>
<template>
  <div class="approval-head"><div><span class="muted">{{detail.definition.name}}</span><h2>{{ numberText(detail.snapshot.number) }}</h2></div><span class="status">{{ detail.status==='RUNNING'?'待审批':detail.status==='COMPLETED'?'审批已完成':detail.status==='CANCELLED'?'申请已撤回':statusName(detail.status) }}</span></div>
  <div class="approval-layout">
    <div>
      <dl class="facts"><div><dt>提交人</dt><dd>{{detail.snapshot.submitter.name}}</dd></div><div><dt>提交时间</dt><dd>{{shanghai(detail.snapshot.submitted_at)}}</dd></div><div><dt>部门</dt><dd>{{detail.snapshot.submitter.department || '—'}}</dd></div><div><dt>材料版本</dt><dd>第 {{detail.revision}} 版</dd></div></dl>
      <section class="surface"><h3>申请备注</h3><p class="preserve">{{detail.snapshot.remark || '未填写备注'}}</p></section>
      <section class="surface"><h3><FileText :size="16"/> 附件资料</h3><template v-if="detail.snapshot.detail?.material_snapshot?.attachments?.length"><p class="muted">以下为提交本轮审批时锁定的附件版本。</p><FileMaterial v-for="file in detail.snapshot.detail.material_snapshot.attachments" :key="file.id" :file="file" @error="emit('error',$event)"/></template><p v-else class="muted">当前申请未绑定附件。</p></section>
    </div>
    <section class="timeline surface approval-flow">
      <div class="approval-flow-head"><div><h3>审批流程</h3><small class="muted">{{detail.definition.name}} · 第 {{detail.definition.version}} 版</small></div><span v-if="detail.status==='RUNNING'" class="approval-flow-progress">当前 {{Math.min(detail.stage_index + 1, detail.nodes.length)}} / {{detail.nodes.length}}</span></div>
      <div class="approval-flow-track">
        <div class="approval-flow-node complete">
          <span class="approval-flow-marker"><Check :size="10"/></span>
          <div class="approval-flow-content"><div class="approval-flow-row"><div class="approval-flow-title"><strong>发起申请</strong><span class="approval-flow-state">已提交</span></div><ApprovalBusinessDetails :detail="detail"/></div><small>{{detail.snapshot.submitter.name}} · {{shanghai(detail.snapshot.submitted_at)}}</small></div>
        </div>
        <div v-for="(node,i) in detail.nodes" :key="node.key" class="approval-flow-node" :class="{current:detail.status==='RUNNING'&&i===detail.stage_index,complete:detail.status==='COMPLETED'||i<detail.stage_index,halted:['REJECTED','RETURNED','CANCELLED'].includes(detail.status)&&i===detail.stage_index}">
          <span class="approval-flow-marker"><Check v-if="detail.status==='COMPLETED'||i<detail.stage_index" :size="10"/><i v-else-if="detail.status==='RUNNING'&&i===detail.stage_index"/></span>
          <div class="approval-flow-content"><div class="approval-flow-row"><strong>{{node.name}}</strong><span class="approval-flow-state">{{nodeState(Number(i))}}</span></div><small>{{modeName(node.mode)}}</small></div>
        </div>
        <div class="approval-flow-node approval-flow-end" :class="{complete:detail.status==='COMPLETED'}">
          <span class="approval-flow-marker"><Check v-if="detail.status==='COMPLETED'" :size="10"/></span>
          <div class="approval-flow-content"><div class="approval-flow-row"><strong>结束</strong><span class="approval-flow-state">{{detail.status==='COMPLETED'?'已完成':'待流转'}}</span></div><small>{{detail.status==='COMPLETED'?'审批流程已结束':'完成全部审批节点后结束'}}</small></div>
        </div>
      </div>
      <p v-if="detail.deadline" :class="detail.deadline.status==='OVERDUE'?'warning':'muted'">{{detail.deadline.status==='OVERDUE'?'本节点已超过办理时限':'本节点办理时限'}} · {{shanghai(detail.deadline.due_at)}}。超时不会自动同意。</p>
      <p v-if="detail.incident" class="warning">{{detail.incident==='ASSIGNMENT_BLOCKED'?'暂无具备资格的审批人员，需要处理人员配置。':'审批暂时无法推进，请联系流程管理员处理。'}}</p>
    </section>
  </div>
  <section v-if="detail.history.length" class="surface"><h3>历史审批意见</h3><div v-for="(h,i) in detail.history" :key="i" class="history-row"><strong>{{approvalActor(h)}} · {{labels[h.decision]}}</strong><small>{{shanghai(h.at)}}</small><p v-if="h.decision_context?.return_target" class="muted">退回到：{{h.decision_context.return_target.name}}</p><p>{{h.comment}}</p></div></section>
  <section v-if="detail.transfer_history?.length" class="surface"><h3><ArrowRightLeft :size="16"/> 席位转交记录</h3><div v-for="(h,i) in detail.transfer_history" :key="i" class="history-row"><strong>{{h.from_user.name}} → {{h.to_user.display_name}}</strong><small>{{shanghai(h.at)}}</small><p>{{h.reason}}</p></div></section>
  <section v-if="detail.add_sign_history?.length" class="surface"><h3><UserPlus :size="16"/> 加签记录</h3><div v-for="(h,i) in detail.add_sign_history" :key="i" class="history-row"><strong>{{h.initiated_by.name}} → {{h.target_user.display_name}} · {{timingName(h.timing)}}</strong><small>{{shanghai(h.at)}}</small><p>{{h.reason}}</p></div></section>
  <section v-if="detail.claim_history?.length" class="surface"><h3><UserCheck :size="16"/> 候选领取记录</h3><div v-for="(h,i) in detail.claim_history" :key="i" class="history-row"><strong>{{h.claimed_by.display_name}} · 已领取唯一责任席位</strong><small>{{shanghai(h.at)}}</small><p class="muted">冻结候选池共 {{h.candidate_count}} 人；其他候选入口已关闭。</p></div></section>
  <section v-if="detail.withdrawal" class="surface"><h3><Undo2 :size="16"/> 申请撤回记录</h3><div class="history-row"><strong>{{detail.withdrawal.applicant.name}} · 撤回本轮审批</strong><small>{{shanghai(detail.withdrawal.at)}}</small><p>{{detail.withdrawal.reason}}</p><p class="muted">本轮已终止，{{detail.withdrawal.cancelled_seats}} 个未完成审批席位已关闭；业务材料已回到草稿，历史审批意见继续保留。</p></div></section>
  <p v-if="detail.material_notice" class="warning">{{detail.material_notice}}</p>
  <div v-if="detail.rejection_reasons.length" class="warning"><strong>命中必须驳回的条件</strong><p v-for="reason in detail.rejection_reasons" :key="reason">{{reason}}</p><small>请核对原因并确认驳回。不能继续同意或以退回代替。</small></div>
  <div v-if="!detail.materials_complete" class="warning">当前权限不足以读取全部必需审批资料，不能提交决定。</div>
  <div v-if="detail.proxy_delegation" class="proxy-notice"><ShieldCheck :size="17"/><div><strong>正在代理 {{detail.proxy_delegation.principal_user.display_name}} 的审批席位</strong><p>{{detail.proxy_delegation.reason}} · 可办理：{{detail.proxy_delegation.allowed_decisions.map((item:string)=>labels[item]).join('、')}}</p><small v-if="detail.proxy_delegation.valid_to">有效期至 {{shanghai(detail.proxy_delegation.valid_to)}}</small></div></div>
  <div class="decision-box decision-primary" v-if="detail.allowed_actions.length"><h3><ShieldCheck :size="17"/> 本次审批意见</h3><div v-if="detail.allowed_actions.includes('RETURN')" class="approval-return-field"><span>退回处理方</span><div ref="returnTargetSelect" class="approval-return-select" :class="{open:returnTargetOpen}" @focusout="closeReturnTarget"><button type="button" class="approval-return-select-button" :aria-expanded="returnTargetOpen" aria-haspopup="listbox" @click="returnTargetOpen=!returnTargetOpen" @keydown.esc.stop="returnTargetOpen=false"><span>{{returnTarget?returnTargetName(returnTarget):'请选择流程允许的处理方'}}</span><ChevronDown :size="15"/></button><div v-if="returnTargetOpen" class="approval-return-select-menu" role="listbox" aria-label="退回处理方"><button v-for="target in detail.return_options" :key="target.key" type="button" role="option" :aria-selected="returnTarget===target.key" :class="{active:returnTarget===target.key}" @click="pickReturnTarget(target.key)"><span>{{target.name}}</span><Check v-if="returnTarget===target.key" :size="14"/></button></div></div><small>退回会结束本轮审批；修改后重新提交，并从首个节点重新审核。</small></div><textarea v-model="comment" placeholder="填写判断依据和审批意见…" rows="3" aria-label="审批意见"/><div class="actions"><button v-for="action in detail.allowed_actions" :key="action" :class="action==='APPROVE'?'primary':''" :disabled="busy" @click="prepare(action)">{{labels[action]}}</button></div></div>
  <div class="decision-box decision-secondary" v-if="detail.claim_allowed"><h3><UserCheck :size="17"/> 领取候选审批任务</h3><p class="muted">当前节点只有一个责任席位。领取成功后由你办理，其他候选人将不能再领取；审批权限和完整材料会在服务端重新校验。</p><div class="actions"><button class="primary" :disabled="busy" @click="claimApproval">{{busy?'正在领取…':'领取审批任务'}}</button></div></div>
  <div class="decision-box decision-secondary" v-else-if="detail.claim_status==='CLAIMED'&&detail.claimed_by"><h3><UserCheck :size="17"/> 候选任务已领取</h3><p>{{detail.claimed_by.display_name}} 已于 {{shanghai(detail.claimed_by.at)}} 取得本节点唯一责任席位。</p></div>
  <div class="decision-box transfer-box" v-if="detail.transfer_allowed"><h3><ArrowRightLeft :size="17"/> 转交审批席位</h3><template v-if="detail.transfer_options.length"><div class="form-grid"><label>转交给<select v-model="transferTarget" aria-label="转交人员"><option value="" disabled>请选择具备权限的人员</option><option v-for="person in detail.transfer_options" :key="person.id" :value="person.id">{{person.display_name}}{{person.department?' · '+person.department:''}}</option></select></label><label>转交原因<input v-model="transferReason" maxlength="500" placeholder="说明无法处理或转交依据" aria-label="转交原因"/></label></div><div class="actions"><button :disabled="busy" @click="prepareTransfer">准备转交</button></div></template><p v-else class="muted">当前没有通过业务读取与审批权限复核的可转交人员。</p></div>
  <div class="decision-box add-sign-box" v-if="detail.add_sign_allowed"><h3><UserPlus :size="17"/> 增加审批复核人</h3><template v-if="detail.add_sign_options.length"><div class="form-grid"><label>加签方式<select v-model="addSignTiming" aria-label="加签方式"><option value="" disabled>请选择前加签或后加签</option><option v-for="timing in detail.add_sign_timings" :key="timing" :value="timing">{{timingName(timing)}}</option></select></label><label>加签人员<select v-model="addSignTarget" aria-label="加签人员"><option value="" disabled>请选择预设合格人员</option><option v-for="person in detail.add_sign_options" :key="person.id" :value="person.id">{{person.display_name}}{{person.department?' · '+person.department:''}}</option></select></label><label>加签原因<input v-model="addSignReason" maxlength="500" placeholder="说明需要增加复核的依据" aria-label="加签原因"/></label></div><div class="actions"><button :disabled="busy" @click="prepareAddSign">准备加签</button></div></template><p v-else class="muted">预设人员池中当前没有通过权限和材料读取复核的可加签人员。</p></div>
  <div class="decision-box withdraw-box" v-if="detail.withdraw_allowed"><h3><Undo2 :size="17"/> 撤回本人发起的审批</h3><p class="muted">仅终止当前审批轮并关闭未完成席位，不删除已经形成的审批意见和审计记录。撤回后业务材料回到草稿。</p><label>撤回原因<textarea v-model="withdrawReason" rows="2" maxlength="500" placeholder="说明本轮为何需要撤回" aria-label="撤回原因"/></label><div class="actions"><button :disabled="busy" @click="prepareWithdraw">撤回申请</button></div></div>
  <Teleport to="body"><div v-if="confirmation" class="modal-shade"><section class="modal" role="dialog" aria-modal="true" aria-label="确认审批决定"><h2>确认本次{{labels[confirmation.payload.decision]}}</h2><p>{{numberText(detail.snapshot.number)}} · 第 {{detail.revision}} 版</p><p v-if="confirmation.payload.decision==='RETURN'" class="warning">本轮将退回到“{{returnTargetName(confirmation.payload.return_target_node_key)}}”并结束；申请人修改后重新提交会创建新轮次，从首个节点完整重审。</p><p v-if="detail.proxy_delegation" class="warning">你将以本人身份代理 {{detail.proxy_delegation.principal_user.display_name}} 的席位提交决定，审计会同时记录双方。</p><p class="preserve">{{confirmation.payload.comment}}</p><p class="muted">{{approvalConfirmationNotice}}</p><div class="actions"><button :disabled="busy" @click="confirmation=null">返回核对</button><button class="primary" :disabled="busy" @click="confirm">{{busy?'正在提交…':'确认提交'}}</button></div></section></div></Teleport>
  <Teleport to="body"><div v-if="transferConfirmation" class="modal-shade"><section class="modal" role="dialog" aria-modal="true" aria-label="确认转交审批席位"><h2>确认转交审批席位</h2><p>{{numberText(detail.snapshot.number)}} · {{transferTargetName(transferConfirmation.payload.target_user_id)}}</p><p class="preserve">{{transferConfirmation.payload.reason}}</p><p class="muted">确认后当前待审批席位将转给目标人员，你将不能再提交该席位的审批决定。系统会再次校验版本和权限。</p><div class="actions"><button :disabled="busy" @click="transferConfirmation=null">返回核对</button><button class="primary" :disabled="busy" @click="confirmTransfer">{{busy?'正在转交…':'确认转交'}}</button></div></section></div></Teleport>
  <Teleport to="body"><div v-if="addSignConfirmation" class="modal-shade"><section class="modal" role="dialog" aria-modal="true" aria-label="确认增加审批复核人"><h2>确认{{timingName(addSignConfirmation.payload.timing)}}</h2><p>{{numberText(detail.snapshot.number)}} · {{addSignTargetName(addSignConfirmation.payload.target_user_id)}}</p><p class="preserve">{{addSignConfirmation.payload.reason}}</p><p class="muted">确认后将创建一个必须明确处理的加签席位。前加签完成后原席位继续；后加签在原席位同意后开始。系统会再次校验版本、人员池和业务权限。</p><div class="actions"><button :disabled="busy" @click="addSignConfirmation=null">返回核对</button><button class="primary" :disabled="busy" @click="confirmAddSign">{{busy?'正在加签…':'确认加签'}}</button></div></section></div></Teleport>
  <Teleport to="body"><div v-if="withdrawConfirmation" class="modal-shade"><section class="modal" role="dialog" aria-modal="true" aria-label="确认撤回本轮审批"><h2>确认撤回本轮审批</h2><p>{{numberText(detail.snapshot.number)}} · 第 {{detail.revision}} 版</p><p class="warning">确认后本轮立即终止，所有未完成审批席位关闭，业务材料回到草稿；已有审批意见和审计记录不会删除。</p><p class="preserve">{{withdrawConfirmation.payload.reason}}</p><div class="actions"><button :disabled="busy" @click="withdrawConfirmation=null">返回核对</button><button class="primary" :disabled="busy" @click="confirmWithdraw">{{busy?'正在撤回…':'确认撤回'}}</button></div></section></div></Teleport>
</template>
