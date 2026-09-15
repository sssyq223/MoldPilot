"""Create a disposable SQLite fixture for built-in-browser smoke tests."""
import argparse
from datetime import date,timedelta
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app import bpm,models as m,project_control_tools as pause_tools,project_closure_tools as closure_tools,contact_tools
from app.authorization import fingerprint
from app.bpm import content_hash
from app.db import Base,now
from app.security import hasher


def build(path:Path,password:str,scenario:str='pause'):
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
        business_type='contact_resolution' if scenario=='contact' else 'project_close' if scenario=='closure' else 'pause_resume'
        config={'business_type':business_type,'nodes':[{'key':'owner_review','name':'项目负责人核对',
            'mode':'ALL','users':[user.id],'reject_rules':[]}]}
        xml=bpm.compile_bpmn(config)
        workflow=m.WorkflowDefinition(process_key=business_type,version=1,
            name=('工程联络处理方案审批（浏览器验收）' if scenario=='contact' else '项目终止与关闭审批（浏览器验收）' if scenario=='closure' else '项目暂停恢复审批（浏览器验收）'),
            config=config,bpmn_xml=xml,status='PUBLISHED',package_hash=bpm.content_hash({'config':config,'xml':xml}))
        conversation=m.Conversation(user_id=user.id,title='工程联络影响项验收' if scenario=='contact' else '项目终止业务流验收' if scenario=='closure' else '项目暂停业务流验收')
        db.add_all([workflow,conversation]);db.flush()
        run=m.Run(conversation_id=conversation.id,user_id=user.id,security_version=user.security_version,
            prompt=('请将受影响图纸纳入工程联络单，明确返工、交期与费用影响。' if scenario=='contact'
                else '请根据客户终止通知终止 SMOKE-M001 项目，并转入处置和终止结算。' if scenario=='closure'
                else '请根据客户通知暂停 SMOKE-M001 项目，并保留客户承诺交期。'),status='SUCCEEDED')
        db.add(run);db.flush()
        if scenario=='contact':
            group=m.AssignmentGroup(kind='DEPARTMENT',name='设计部',active=True,version=1)
            db.add(group);db.flush();db.add(m.AssignmentMember(group_id=group.id,user_id=user.id,is_head=True))
            case=m.ContactCase(project_id=project.id,category='hardware',title='装配尺寸与图纸不一致',
                description='试装时发现型腔关键尺寸与客户确认图纸不一致，需要评估返工。',mode='ONLINE',created_by=user.id,
                request_key='browser-contact-case',request_hash='b'*64,revision=1,customer_ref='ERP-CUSTOMER-008',
                customer_name='浏览器验收客户',mold_number='SMOKE-MOLD-01',product_ref='PART-A100',
                application_date=date.today(),problem_source='ASSEMBLY_ISSUE',current_stage='装配阶段',
                change_type='EXCEPTION',urgency='URGENT')
            db.add(case);db.flush()
            tool='prepare_contact_task'
            arguments={'case_id':case.id,'revision':case.revision,'department_id':group.id,'title':'按第二版图纸返工并复测',
                'affected_type':'DRAWING','affected_ref':'DRAWING-A100-R2','impact_description':'型腔尺寸须按客户确认第二版图纸返工并重新检测',
                'planned_action':'REWORK','delivery_impact_days':2,'estimated_amount':'3500.00','currency':'CNY',
                'source_system':'ERP','source_ref':'erp:drawing:DRAWING-A100-R2','source_as_of':now().isoformat()}
            result=contact_tools.execute_tool(db,user,tool,arguments)
            summary='已按当前联络单资料准备结构化影响与责任事项，请核对对象、返工动作、交期、金额和来源后确认。'
            suggestions=['确认后只新增联络协作事项；方案审批、实际执行和独立复验仍分别办理。']
        elif scenario=='closure':
            tool='prepare_project_termination'
            arguments={'project_id':project.id,'project_version':project.row_version,'effective_date':date.today().isoformat(),
                'current_stage':'制造加工阶段','reason':'客户书面要求终止项目',
                'evidence':'客户终止通知（浏览器验收合成材料）','completed_work_summary':'结构设计已完成，模具加工进行中',
                'incurred_cost_summary':'设计与当前加工费用已由项目负责人汇总，待财务在终止清单复核',
                'incurred_cost_amount':'128000.00','currency':'CNY','workflow_definition_id':workflow.id}
            result=closure_tools.execute_tool(db,user,tool,arguments)
            summary='已根据项目当前状态准备终止申请，请核对当前环节、完成工作、已发生费用和执行限制后确认提交。'
            suggestions=['确认后仅提交审批；审批生效才会停止本地正常计划执行并建立终止处置清单。']
        else:
            tool='prepare_project_pause'
            arguments={'project_id':project.id,'project_version':project.row_version,'effective_date':date.today().isoformat(),
                'expected_resume_date':(date.today()+timedelta(days=5)).isoformat(),'reason':'客户要求等待最终产品确认',
                'evidence':'客户暂停通知（浏览器验收合成材料）','workflow_definition_id':workflow.id}
            result=pause_tools.execute_tool(db,user,tool,arguments)
            summary='已根据当前项目与计划资料准备整体暂停申请，请核对影响范围后确认提交审批。'
            suggestions=['审批生效前项目仍处于执行中。']
        step=m.Step(run_id=run.id,sequence=0,tool=tool,
            request_hash=content_hash({'key':tool,'arguments':arguments}),result=result)
        db.add(step);db.flush()
        run.checkpoint={'authorization_hash':fingerprint(db,user)}
        run.result={'response_kind':'BUSINESS','summary':summary,
            'evidence_ids':[step.id],'suggestions':suggestions,
            'evidence':[{'id':step.id,'tool':step.tool,**result}]}
    engine.dispose()
    return {'database':str(path),'username':'browser_admin','password':password,'conversation_id':conversation.id}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--password',required=True)
    parser.add_argument('--scenario',choices=['pause','closure','contact'],default='pause')
    args=parser.parse_args()
    print(build(args.database,args.password,args.scenario))
