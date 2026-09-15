"""Create a disposable SQLite fixture for built-in-browser smoke tests."""
import argparse
from datetime import date,timedelta
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app import bpm,models as m,project_control_tools as tools
from app.authorization import fingerprint
from app.bpm import content_hash
from app.db import Base,now
from app.security import hasher


def build(path:Path,password:str):
    path=path.resolve()
    if path.exists():raise RuntimeError('Refusing to overwrite an existing browser test database')
    path.parent.mkdir(parents=True,exist_ok=True)
    engine=create_engine('sqlite+pysqlite:///'+path.as_posix(),connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    factory=sessionmaker(engine,expire_on_commit=False)
    with factory.begin() as db:
        user=m.User(username='browser_admin',display_name='浏览器验收员',department='项目管理',
            password_hash=hasher.hash(password),super_admin=True)
        project=m.Project(code='SMOKE-M001',name='浏览器验收模具项目',status='ACTIVE')
        db.add_all([user,project]);db.flush()
        db.add(m.ProjectProfile(project_id=project.id,owner_user_id=user.id,execution_mode='INTERNAL',
            customer_due_date=now().date()+timedelta(days=45)))
        plan=m.BusinessSubject(kind='project_plan',number='SMOKE-PLAN-001',project_id=project.id,
            created_by=user.id,status='EFFECTIVE')
        db.add(plan);db.flush();db.add(m.PlanDetail(subject_id=plan.id,reason='浏览器验收基线'))
        db.add_all([
            m.PlanTask(plan_id=plan.id,key='design',name='结构设计',owner_user_id=user.id,
                planned_start=now().date(),planned_end=now().date()+timedelta(days=7),status='RUNNING',actual_start=now().date()),
            m.PlanTask(plan_id=plan.id,key='manufacture',name='模具加工',owner_user_id=user.id,
                planned_start=now().date()+timedelta(days=8),planned_end=now().date()+timedelta(days=25),status='PLANNED')])
        config={'business_type':'pause_resume','nodes':[{'key':'owner_review','name':'项目负责人核对',
            'mode':'ALL','users':[user.id],'reject_rules':[]}]}
        xml=bpm.compile_bpmn(config)
        workflow=m.WorkflowDefinition(process_key='project_pause_resume',version=1,name='项目暂停恢复审批（浏览器验收）',
            config=config,bpmn_xml=xml,status='PUBLISHED',package_hash=bpm.content_hash({'config':config,'xml':xml}))
        conversation=m.Conversation(user_id=user.id,title='项目暂停业务流验收')
        db.add_all([workflow,conversation]);db.flush()
        run=m.Run(conversation_id=conversation.id,user_id=user.id,security_version=user.security_version,
            prompt='请根据客户通知暂停 SMOKE-M001 项目，并保留客户承诺交期。',status='SUCCEEDED')
        db.add(run);db.flush()
        arguments={'project_id':project.id,'project_version':project.row_version,'effective_date':date.today().isoformat(),
            'expected_resume_date':(date.today()+timedelta(days=5)).isoformat(),'reason':'客户要求等待最终产品确认',
            'evidence':'客户暂停通知（浏览器验收合成材料）','workflow_definition_id':workflow.id}
        result=tools.execute_tool(db,user,'prepare_project_pause',arguments)
        step=m.Step(run_id=run.id,sequence=0,tool='prepare_project_pause',
            request_hash=content_hash({'key':'prepare_project_pause','arguments':arguments}),result=result)
        db.add(step);db.flush()
        run.checkpoint={'authorization_hash':fingerprint(db,user)}
        run.result={'response_kind':'BUSINESS','summary':'已根据当前项目与计划资料准备整体暂停申请，请核对影响范围后确认提交审批。',
            'evidence_ids':[step.id],'suggestions':['审批生效前项目仍处于执行中。'],
            'evidence':[{'id':step.id,'tool':step.tool,**result}]}
    engine.dispose()
    return {'database':str(path),'username':'browser_admin','password':password,'conversation_id':conversation.id}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--password',required=True)
    print(build(parser.parse_args().database,parser.parse_args().password))
