<script setup lang="ts">
import { ref } from 'vue'
import { ArrowRightLeft, Check, Circle, FileText, ShieldCheck } from 'lucide-vue-next'
import { api, post, shanghai } from '../api'
import FileMaterial from './FileMaterial.vue'
import ApprovalBusinessDetails from '@domain-pack/components/ApprovalBusinessDetails.vue'
import {statusName,numberText} from '@domain-pack/uiText'
import {approvalConfirmationNotice} from '@domain-pack/uiPolicy'
const props = defineProps<{ detail: any }>()
const emit = defineEmits<{ changed: []; error: [message: string] }>()
const comment = ref(''), busy = ref(false), confirmation = ref<any>(null)
const transferTarget = ref(''), transferReason = ref(''), transferConfirmation = ref<any>(null)
const labels: Record<string,string> = { APPROVE:'同意', REJECT:'驳回', RETURN:'退回修改' }
async function prepare(decision: string) {
  if (!comment.value.trim()) return emit('error','请填写本次审批意见')
  busy.value = true
  try {
    const d = props.detail
    confirmation.value = await post('/approvals/decision-intent', { instance_id:d.id, seat_id:d.seat_id, seat_version:d.seat_version, version:d.version, snapshot_hash:d.snapshot_hash, decision, comment:comment.value })
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
</script>
<template>
  <div class="approval-head"><div><span class="muted">{{detail.definition.name}}</span><h2>{{ numberText(detail.snapshot.number) }}</h2></div><span class="status">{{ detail.status==='RUNNING'?'待审批':detail.status==='COMPLETED'?'审批已完成':statusName(detail.status) }}</span></div>
  <div class="approval-layout">
    <div>
      <dl class="facts"><div><dt>提交人</dt><dd>{{detail.snapshot.submitter.name}}</dd></div><div><dt>提交时间</dt><dd>{{shanghai(detail.snapshot.submitted_at)}}</dd></div><div><dt>部门</dt><dd>{{detail.snapshot.submitter.department || '—'}}</dd></div><div><dt>材料版本</dt><dd>第 {{detail.revision}} 版</dd></div></dl>
      <ApprovalBusinessDetails :detail="detail"/>
      <section class="surface"><h3>申请备注</h3><p class="preserve">{{detail.snapshot.remark || '未填写备注'}}</p></section>
      <section class="surface"><h3><FileText :size="16"/> 附件资料</h3><template v-if="detail.snapshot.detail?.material_snapshot?.attachments?.length"><p class="muted">以下为提交本轮审批时锁定的附件版本。</p><FileMaterial v-for="file in detail.snapshot.detail.material_snapshot.attachments" :key="file.id" :file="file" @error="emit('error',$event)"/></template><p v-else class="muted">当前申请未绑定附件。</p></section>
    </div>
    <section class="timeline surface"><h3>审批流程</h3><small class="muted">{{detail.definition.name}} · 第 {{detail.definition.version}} 版</small>
      <div class="timeline-node complete"><Check :size="18"/><div>发起申请<small>{{detail.snapshot.submitter.name}}</small></div></div>
      <div v-for="(node,i) in detail.nodes" :key="node.key" class="timeline-node" :class="{current:i===detail.stage_index,complete:i<detail.stage_index}"><Check v-if="i<detail.stage_index" :size="18"/><Circle v-else :size="18"/><div>{{node.name}}<small>{{i<detail.stage_index?'已通过':i===detail.stage_index?'当前节点':'尚未进入'}} · {{node.mode==='ALL'?'会签':'或签'}}</small></div></div>
      <p v-if="detail.incident" class="warning">{{detail.incident==='ASSIGNMENT_BLOCKED'?'暂无具备资格的审批人员，需要处理人员配置。':'审批暂时无法推进，请联系流程管理员处理。'}}</p>
    </section>
  </div>
  <section v-if="detail.history.length" class="surface"><h3>历史审批意见</h3><div v-for="(h,i) in detail.history" :key="i" class="history-row"><strong>{{h.user.name}} · {{labels[h.decision]}}</strong><small>{{shanghai(h.at)}}</small><p>{{h.comment}}</p></div></section>
  <section v-if="detail.transfer_history?.length" class="surface"><h3><ArrowRightLeft :size="16"/> 席位转交记录</h3><div v-for="(h,i) in detail.transfer_history" :key="i" class="history-row"><strong>{{h.from_user.name}} → {{h.to_user.display_name}}</strong><small>{{shanghai(h.at)}}</small><p>{{h.reason}}</p></div></section>
  <p v-if="detail.material_notice" class="warning">{{detail.material_notice}}</p>
  <div v-if="detail.rejection_reasons.length" class="warning"><strong>命中必须驳回的条件</strong><p v-for="reason in detail.rejection_reasons" :key="reason">{{reason}}</p><small>请核对原因并确认驳回。不能继续同意或以退回代替。</small></div>
  <div v-if="!detail.materials_complete" class="warning">当前权限不足以读取全部必需审批资料，不能提交决定。</div>
  <div class="decision-box" v-if="detail.allowed_actions.length"><h3><ShieldCheck :size="17"/> 本次审批意见</h3><textarea v-model="comment" placeholder="填写判断依据和审批意见…" rows="3" aria-label="审批意见"/><div class="actions"><button v-for="action in detail.allowed_actions" :key="action" :class="action==='APPROVE'?'primary':''" :disabled="busy" @click="prepare(action)">{{labels[action]}}</button></div></div>
  <div class="decision-box transfer-box" v-if="detail.transfer_allowed"><h3><ArrowRightLeft :size="17"/> 转交审批席位</h3><template v-if="detail.transfer_options.length"><div class="form-grid"><label>转交给<select v-model="transferTarget" aria-label="转交人员"><option value="" disabled>请选择具备权限的人员</option><option v-for="person in detail.transfer_options" :key="person.id" :value="person.id">{{person.display_name}}{{person.department?' · '+person.department:''}}</option></select></label><label>转交原因<input v-model="transferReason" maxlength="500" placeholder="说明无法处理或转交依据" aria-label="转交原因"/></label></div><div class="actions"><button :disabled="busy" @click="prepareTransfer">准备转交</button></div></template><p v-else class="muted">当前没有通过业务读取与审批权限复核的可转交人员。</p></div>
  <Teleport to="body"><div v-if="confirmation" class="modal-shade"><section class="modal" role="dialog" aria-modal="true" aria-label="确认审批决定"><h2>确认本次{{labels[confirmation.payload.decision]}}</h2><p>{{numberText(detail.snapshot.number)}} · 第 {{detail.revision}} 版</p><p class="preserve">{{confirmation.payload.comment}}</p><p class="muted">{{approvalConfirmationNotice}}</p><div class="actions"><button :disabled="busy" @click="confirmation=null">返回核对</button><button class="primary" :disabled="busy" @click="confirm">{{busy?'正在提交…':'确认提交'}}</button></div></section></div></Teleport>
  <Teleport to="body"><div v-if="transferConfirmation" class="modal-shade"><section class="modal" role="dialog" aria-modal="true" aria-label="确认转交审批席位"><h2>确认转交审批席位</h2><p>{{numberText(detail.snapshot.number)}} · {{transferTargetName(transferConfirmation.payload.target_user_id)}}</p><p class="preserve">{{transferConfirmation.payload.reason}}</p><p class="muted">确认后当前待审批席位将转给目标人员，你将不能再提交该席位的审批决定。系统会再次校验版本和权限。</p><div class="actions"><button :disabled="busy" @click="transferConfirmation=null">返回核对</button><button class="primary" :disabled="busy" @click="confirmTransfer">{{busy?'正在转交…':'确认转交'}}</button></div></section></div></Teleport>
</template>
