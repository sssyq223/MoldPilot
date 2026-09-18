<script setup lang="ts">
import { ref } from 'vue'
import { Check, Circle, FileText, ShieldCheck } from 'lucide-vue-next'
import { api, post, shanghai } from '../api'
import FileMaterial from './FileMaterial.vue'
import BusinessFacts from '@domain-pack/components/BusinessFacts.vue'
import {statusName,numberText} from '@domain-pack/uiText'
const props = defineProps<{ detail: any }>()
const emit = defineEmits<{ changed: []; error: [message: string] }>()
const comment = ref(''), busy = ref(false), confirmation = ref<any>(null)
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
</script>
<template>
  <div class="approval-head"><div><span class="muted">{{detail.definition.name}}</span><h2>{{ numberText(detail.snapshot.number) }}</h2></div><span class="status">{{ detail.status==='RUNNING'?'待审批':detail.status==='COMPLETED'?'审批已完成':statusName(detail.status) }}</span></div>
  <div class="approval-layout">
    <div>
      <dl class="facts"><div><dt>提交人</dt><dd>{{detail.snapshot.submitter.name}}</dd></div><div><dt>提交时间</dt><dd>{{shanghai(detail.snapshot.submitted_at)}}</dd></div><div><dt>部门</dt><dd>{{detail.snapshot.submitter.department || '—'}}</dd></div><div><dt>材料版本</dt><dd>第 {{detail.revision}} 版</dd></div></dl>
      <section v-if="detail.business_type==='purchase_request'" class="surface"><h3>采购申请明细</h3><div class="table-scroll"><table><thead><tr><th>物料名称</th><th>数量</th><th>单位</th><th>需求日期</th></tr></thead><tbody><tr v-for="(line,i) in detail.snapshot.lines" :key="i"><td>{{line.material_name ?? '无字段权限'}}</td><td>{{line.quantity ?? '—'}}</td><td>{{line.unit ?? '—'}}</td><td>{{line.due_date ?? '—'}}</td></tr></tbody></table></div></section>
      <section v-else class="surface"><h3>提交审批的业务材料</h3><BusinessFacts :value="detail.snapshot.detail"/></section>
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
  <p v-if="detail.material_notice" class="warning">{{detail.material_notice}}</p>
  <div v-if="detail.rejection_reasons.length" class="warning"><strong>命中必须驳回的条件</strong><p v-for="reason in detail.rejection_reasons" :key="reason">{{reason}}</p><small>请核对原因并确认驳回。不能继续同意或以退回代替。</small></div>
  <div v-if="!detail.materials_complete" class="warning">当前权限不足以读取全部必需审批资料，不能提交决定。</div>
  <div class="decision-box" v-if="detail.allowed_actions.length"><h3><ShieldCheck :size="17"/> 本次审批意见</h3><textarea v-model="comment" placeholder="填写判断依据和审批意见…" rows="3" aria-label="审批意见"/><div class="actions"><button v-for="action in detail.allowed_actions" :key="action" :class="action==='APPROVE'?'primary':''" :disabled="busy" @click="prepare(action)">{{labels[action]}}</button></div></div>
  <Teleport to="body"><div v-if="confirmation" class="modal-shade"><section class="modal" role="dialog" aria-modal="true" aria-label="确认审批决定"><h2>确认本次{{labels[confirmation.payload.decision]}}</h2><p>{{numberText(detail.snapshot.number)}} · 第 {{detail.revision}} 版</p><p class="preserve">{{confirmation.payload.comment}}</p><p class="muted">提交后将记录你的正式审批决定。审批通过不代表已下单或已发货。</p><div class="actions"><button :disabled="busy" @click="confirmation=null">返回核对</button><button class="primary" :disabled="busy" @click="confirm">{{busy?'正在提交…':'确认提交'}}</button></div></section></div></Teleport>
</template>
