<script setup lang="ts">
import {ref} from 'vue'
import {post} from '../api'
import {clean,commandNames} from '../businessForms'
import SchemaFields from './SchemaFields.vue'
import BusinessFacts from './BusinessFacts.vue'
const props=defineProps<{action:string;resourceId:string;schema:any;options?:Record<string,any[]>;initial?:any;context?:string}>()
const emit=defineEmits<{changed:[];error:[message:string];close:[]}>()
const value=ref<any>({...props.initial}),intent=ref<any>(null),busy=ref(false)
async function prepare(){busy.value=true;try{intent.value=await post('/business/command-intents',{action:props.action,resource_id:props.resourceId,payload:clean(value.value)})}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
async function confirm(){busy.value=true;try{await post('/human-actions/'+intent.value.id+'/confirm',{challenge:intent.value.challenge});intent.value=null;emit('changed');emit('close')}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
</script>
<template><form class="surface form-stack" @submit.prevent="prepare"><h3>{{commandNames[action]||action}}</h3><p class="muted">{{context}} · 请根据实际材料核对，提交将记录你的正式确认。</p><SchemaFields v-model="value" :schema="schema" :root="schema" :options="options"/><div class="actions"><button type="button" @click="emit('close')">取消</button><button class="primary" :disabled="busy">核对确认内容</button></div></form>
<Teleport to="body"><div v-if="intent" class="modal-shade"><section class="modal" role="dialog" aria-modal="true" aria-label="确认业务操作"><h2>{{commandNames[action]}}</h2><p>{{context}}</p><BusinessFacts :value="intent.payload"/><div class="actions"><button :disabled="busy" @click="intent=null">返回核对</button><button class="primary" :disabled="busy" @click="confirm">确认提交</button></div></section></div></Teleport></template>
