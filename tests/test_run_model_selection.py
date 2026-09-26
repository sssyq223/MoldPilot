"""任务级选择，不以全局配置或 Worker checkpoint 覆盖发起时的模型。"""
from datetime import timedelta
import json
import pytest
from sqlalchemy import select
from app import config, models as m
from app.db import now
from conftest import sign_in
from test_agent_api import worker_headers
from test_model_catalog import catalog_file


def create(client, model='legacy-glm', effort='high', cid=None):
    response=client.post('/api/runs',json={'prompt':'只说明当前模型','conversation_id':cid,
                                         'model_profile_id':model,'reasoning_effort':effort})
    assert response.status_code==200,response.text
    return response.json()


def test_two_conversations_pin_separate_model_and_reasoning(client,data,catalog_file):
    from app.model_catalog import public_catalog,save_model
    state=public_catalog()
    state=save_model({'provider_id':state['providers'][0]['id'],'model':'second-model'},expected_revision=state['revision'])
    second=next(item['id'] for item in state['models'] if item['model']=='second-model')
    sign_in(client)
    first_run=create(client)
    second_run=create(client,second,'')
    first=client.post('/internal/runs/claim',headers=worker_headers()).json()['run']
    second_context=client.post('/internal/runs/claim',headers=worker_headers()).json()['run']
    assert first['id']==first_run['id'] and first['model_selection']['model']=='GLM-5.3-Flash'
    assert first['model_selection']['reasoning_effort']=='high'
    assert second_context['id']==second_run['id'] and second_context['model_selection']['model']=='second-model'
    assert second_context['model_selection']['reasoning_effort']==''
    assert 'synthetic-secret' not in json.dumps([first,second_context])
    with data[1]() as db:
        events=list(db.scalars(select(m.AuditEvent).where(m.AuditEvent.action=='agent.run.created')))
        assert 'synthetic-secret' not in json.dumps([e.detail for e in events])


def test_selected_model_survives_global_default_change_and_hostile_checkpoint(client,data,catalog_file):
    sign_in(client)
    run=create(client)
    # 旧 v2 激活开关变化不能漂移已创建任务。
    raw=json.loads(catalog_file.read_text(encoding='utf-8'))
    raw['profiles'].append({**raw['profiles'][0],'id':'another','name':'另一个','llm_model':'other-model'})
    raw['active_profile_id']='another'
    catalog_file.write_text(json.dumps(raw),encoding='utf-8')
    with data[1].begin() as db:
        db.get(m.Run,run['id']).checkpoint={'model_selection':{'profile_id':'forged'}}
    claimed=client.post('/internal/runs/claim',headers=worker_headers()).json()['run']
    assert claimed['model_selection']['profile_id']=='legacy-glm'
    assert claimed['model_selection']['reasoning_effort']=='high'
    response=client.post(f"/internal/runs/{run['id']}/checkpoint",headers=worker_headers(),json={
        'epoch':claimed['epoch'],'checkpoint':{'model_selection':{'profile_id':'another','reasoning_effort':'low'}}})
    assert response.status_code==200,response.text
    with data[1].begin() as db:
        stored=db.get(m.Run,run['id'])
        assert stored.checkpoint['model_selection']['profile_id']=='legacy-glm'
        stored.lease_until=now()-timedelta(seconds=1)
    reclaimed=client.post('/internal/runs/claim',headers=worker_headers()).json()['run']
    assert reclaimed['model_selection']==claimed['model_selection']


@pytest.mark.parametrize('profile_id',['legacy-glm','missing-profile'])
def test_unprivileged_user_cannot_override_model_or_reasoning(client,catalog_file,profile_id):
    sign_in(client,'test_buyer')
    response=client.post('/api/runs',json={'prompt':'查询','model_profile_id':profile_id,'reasoning_effort':'high'})
    assert response.status_code==403,response.text


def test_unsupported_reasoning_rejected_before_queuing(client,data,catalog_file):
    sign_in(client)
    response=client.post('/api/runs',json={'prompt':'查询','model_profile_id':'legacy-glm','reasoning_effort':'medium'})
    assert response.status_code==400,response.text
    with data[1]() as db:assert db.scalar(select(m.Run)) is None


def test_destination_change_does_not_silently_reroute_queued_task(client,data,catalog_file):
    sign_in(client);run=create(client)
    raw=json.loads(catalog_file.read_text(encoding='utf-8'))
    raw['profiles'][0]['llm_base_url']='https://changed.example/v1'
    catalog_file.write_text(json.dumps(raw),encoding='utf-8')
    claimed=client.post('/internal/runs/claim',headers=worker_headers())
    assert claimed.status_code==200,claimed.text
    assert claimed.json()['run'] is None
    with data[1]() as db:
        stored=db.get(m.Run,run['id'])
        assert stored.status=='FAILED'
        assert stored.result['error_code']=='MODEL_CONFIGURATION_CHANGED'


def test_proposal_resume_keeps_selection_when_global_default_is_disabled(client,data,catalog_file):
    from app.agent_resume import queue_after_proposal_decision
    sign_in(client);created=create(client)
    raw=json.loads(catalog_file.read_text(encoding='utf-8'))
    raw['profiles'].append({**raw['profiles'][0],'id':'disabled-default','name':'已停用默认',
                            'llm_model':'other-model','llm_enabled':False})
    raw['active_profile_id']='disabled-default'
    catalog_file.write_text(json.dumps(raw),encoding='utf-8')
    with data[1].begin() as db:
        run=db.get(m.Run,created['id']);selection=run.checkpoint['model_selection']
        run.status='SUCCEEDED';run.result={'summary':'等待人工决定'}
        step=m.Step(run_id=run.id,sequence=0,tool='synthetic-proposal',request_hash='0'*64,result={})
        db.add(step);db.flush()
        assert queue_after_proposal_decision(db,db.get(m.User,run.user_id),step.id,'dismissed')
        assert run.status=='QUEUED' and run.checkpoint['model_selection']==selection
    context=client.post('/internal/runs/claim',headers=worker_headers()).json()['run']
    assert context['model_selection']==selection


@pytest.mark.parametrize('action',['delete','disable'])
def test_removed_or_disabled_model_does_not_fall_back_to_default(client,data,catalog_file,action):
    from app.model_catalog import public_catalog,save_model,delete_model
    state=public_catalog()
    state=save_model({'provider_id':state['providers'][0]['id'],'model':'second-model'},expected_revision=state['revision'])
    mid=next(item['id'] for item in state['models'] if item['model']=='second-model')
    sign_in(client);created=create(client,mid,'')
    if action=='delete':delete_model(mid,expected_revision=state['revision'])
    else:save_model({'enabled':False},model_id=mid,expected_revision=state['revision'])
    assert client.post('/internal/runs/claim',headers=worker_headers()).json()['run'] is None
    with data[1]() as db:
        run=db.get(m.Run,created['id'])
        assert run.status=='FAILED'
        assert run.result['error_code']==('MODEL_PROFILE_NOT_FOUND' if action=='delete' else 'MODEL_DISABLED')


def test_worker_factory_uses_pinned_effort_and_allows_key_rotation(catalog_file,monkeypatch):
    import httpx
    from types import SimpleNamespace
    from app.run_model_selection import select_model, runtime_for_selection
    from app.agent_worker import create_model
    selection=select_model(SimpleNamespace(super_admin=True),'legacy-glm','low')
    raw=json.loads(catalog_file.read_text(encoding='utf-8'))
    raw['profiles'][0]['llm_api_key']='rotated-synthetic'
    catalog_file.write_text(json.dumps(raw),encoding='utf-8')
    runtime=runtime_for_selection(selection)
    monkeypatch.setattr(runtime,'worker_secret','synthetic-worker')
    adapter=create_model(runtime)
    def serve(request):
        assert request.headers['authorization']=='Bearer rotated-synthetic'
        assert json.loads(request.content)['reasoning_effort']=='low'
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'{}'},'finish_reason':'stop'}]})
    adapter.transport=httpx.MockTransport(serve)
    try:assert adapter.generate([],[])['content']=='{}'
    finally:adapter.close()
