from datetime import date
from decimal import Decimal
from fastapi import Depends
from pydantic import Field
from sqlalchemy import select, func, literal, text
from domain_packs.mold.ports.schemas import StrictModel, SubmitInput
from domain_packs.mold.ports.security import current_user
from domain_packs.mold.ports.db import get_db
from domain_packs.mold import models as m, domains, domain_schemas as s, procurement, business
from domain_packs.mold.authorization import PERMISSIONS, require, predicate, select_fields
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.events import record


class MasterInput(StrictModel):
    code: str = Field(min_length=1,max_length=80)
    name: str = Field(min_length=1,max_length=150)
    category: str | None = None
    unit: str | None = None


class ProjectInput(StrictModel):
    code: str = Field(min_length=1,max_length=80)
    name: str = Field(min_length=1,max_length=150)
    customer_id: str | None = None
    owner_user_id: str
    execution_mode: str = 'INTERNAL'


class OrderLineInput(StrictModel):
    id: str
    unit_price: Decimal = Field(ge=0,max_digits=18,decimal_places=6)
    price_subject_id: str
    agreed_ship_date: date


class OrderInput(StrictModel):
    version: int
    supplier_id: str
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    lines: list[OrderLineInput] = Field(min_length=1,max_length=100)


class CommandInput(StrictModel):
    action: str
    resource_id: str
    payload: dict


class RiskInput(StrictModel):
    near_due_days: int = Field(ge=0,le=90)
    reason: str = Field(min_length=1,max_length=2000)


class LineInput(StrictModel):
    material_id: str
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    due_date: date


class PurchaseInput(StrictModel):
    project_id: str
    remark: str = Field(default="", max_length=4000)
    lines: list[LineInput] = Field(min_length=1, max_length=100)


class PlanDepartmentConfirmationInput(StrictModel):
    expected_version: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=1000)


def install(app):
    @app.get("/api/catalog")
    def legacy_catalog(user=Depends(current_user)):
        return {"permissions": PERMISSIONS, "categories": [
            {"id": "hardware", "name": "五金"},
            {"id": "raw_material", "name": "原材"},
            {"id": "outsource", "name": "委外"},
        ]}

    @app.get("/api/projects")
    def legacy_projects(user=Depends(current_user), db=Depends(get_db)):
        allowed = predicate(db, user, "project.read", {"project_id": m.Project.id})
        return [select_fields(
            {"id": row.id, "code": row.code, "name": row.name, "status": row.status},
            require(db, user, "project.read", {"project_id": row.id}),
        ) for row in db.scalars(select(m.Project).where(allowed).limit(100))]

    @app.get("/api/materials")
    def legacy_materials(project_id: str, user=Depends(current_user), db=Depends(get_db)):
        allowed = predicate(db, user, "purchase.create", {
            "project_id": literal(project_id), "category": m.Material.category,
        })
        return [{"id": row.id, "code": row.code, "name": row.name,
                 "category": row.category, "unit": row.unit}
                for row in db.scalars(select(m.Material).where(allowed).limit(100))]

    @app.get("/api/purchases")
    def legacy_purchases(user=Depends(current_user), db=Depends(get_db)):
        query = business.visible_requests(db, user).order_by(
            m.PurchaseRequest.created_at.desc()).limit(100)
        return [business.request_data(db, user, row) for row in db.scalars(query)]

    @app.post("/api/purchases")
    def legacy_create_purchase(data: PurchaseInput, user=Depends(current_user), db=Depends(get_db)):
        row = business.create_request(db, user, data)
        db.commit()
        return {"id": row.id, "number": row.number, "status": row.status}

    @app.post("/api/purchases/{request_id}/submit-intent")
    def legacy_submit_purchase(request_id: str, data: SubmitInput,
                               user=Depends(current_user), db=Depends(get_db)):
        result = business.create_intent(db, user, "purchase.submit", request_id, data.model_dump())
        db.commit()
        return result

    @app.get('/api/business/catalog')
    def catalog(user=Depends(current_user)):
        result=[]
        for key,spec in s.CATALOG.items():
            schema=spec['schema'].model_json_schema()
            if 'decisions' in spec:schema['properties']['decision']['enum']=spec['decisions']
            result.append({'key':key,'name':spec['name'],'risk':spec['risk'],'schema':schema})
        return result

    @app.get('/api/business/subjects')
    def subjects(kind:str|None=None,user=Depends(current_user),db=Depends(get_db)):
        return domains.visible(db,user,kind)

    @app.get('/api/business/options')
    def reference_options(kind:str,project_id:str,category:str|None=None,user=Depends(current_user),db=Depends(get_db)):
        if kind not in s.CATALOG:raise DomainError('KIND_UNKNOWN','业务类型未登记')
        require(db,user,kind+'.create',{'project_id':project_id,'category':category})
        project=db.get(m.Project,project_id)
        if not project:raise DomainError('NOT_FOUND','项目不存在',404)
        profile=db.get(m.ProjectProfile,project_id)
        from domain_packs.mold.authorization import access
        users=select(m.User).where(m.User.active.is_(True))
        if not access(db,user,'identity.reference',{}).allowed:
            users=users.where(m.User.id.in_([user.id]+([profile.owner_user_id] if profile else [])))
        identities=[{'id':u.id,'label':u.display_name+' · '+u.department} for u in db.scalars(users.limit(200))]
        result={key:identities for key in ('owner_user_id','reviewer_id','supervisor_id','responsible_id')}
        # A picker is a scoped read. It cannot expose unrelated domains merely because the user can create a draft.
        if category in {'hardware','raw_material','outsource'}:
            result['supplier_id']=[{'id':r.id,'label':r.name} for r in db.scalars(select(m.Supplier).where(m.Supplier.category==category,m.Supplier.active.is_(True)).limit(200))]
            result['material_id']=[{'id':r.id,'label':r.code+' · '+r.name} for r in db.scalars(select(m.Material).where(m.Material.category==category).limit(200))]
        elif user.super_admin:
            result['supplier_id']=[{'id':r.id,'label':r.name} for r in db.scalars(select(m.Supplier).where(m.Supplier.active.is_(True)).limit(200))]
            result['material_id']=[{'id':r.id,'label':r.code+' · '+r.name} for r in db.scalars(select(m.Material).limit(200))]
        result['customer_id']=[]
        customers=select(m.Customer).where(m.Customer.active.is_(True))
        if not user.super_admin:customers=customers.where(m.Customer.id==(profile.customer_id if profile else None))
        result['customer_id']=[{'id':r.id,'label':r.name} for r in db.scalars(customers.limit(200))]
        return result

    @app.post('/api/business/subjects')
    def create_subject(data:s.SubjectInput,user=Depends(current_user),db=Depends(get_db)):
        subject=domains.create(db,user,data);db.commit()
        return {'id':subject.id,'number':subject.number,'revision':subject.revision,'status':subject.status}

    @app.get('/api/business/subjects/{subject_id}')
    def subject(subject_id:str,user=Depends(current_user),db=Depends(get_db)):
        row=db.get(m.BusinessSubject,subject_id)
        if not row:raise DomainError('NOT_FOUND','业务单据不存在',404)
        return domains.data(db,user,row)

    @app.post('/api/business/subjects/{subject_id}/submit-intent')
    def submit(subject_id:str,data:SubmitInput,user=Depends(current_user),db=Depends(get_db)):
        result=business.create_intent(db,user,'business.submit',subject_id,data.model_dump());db.commit();return result

    @app.post('/api/business/command-intents')
    def command(data:CommandInput,user=Depends(current_user),db=Depends(get_db)):
        result=business.create_intent(db,user,'domain.'+data.action,data.resource_id,data.payload);db.commit();return result

    @app.post("/api/plan-department-confirmations/{confirmation_id}/confirm")
    def confirm_plan_department(confirmation_id: str, data: PlanDepartmentConfirmationInput,
                                user=Depends(current_user), db=Depends(get_db)):
        from domain_packs.mold import plan_confirmations
        result = plan_confirmations.confirm(
            db, user, confirmation_id, data.expected_version, data.note
        )
        db.commit()
        return result

    @app.get('/api/business/commands')
    def commands(user=Depends(current_user)):
        from domain_packs.mold.erp.core.domain_commands import COMMANDS
        return [{'key':key,'permission':permission,'schema':schema.model_json_schema()}
                for key,(_,schema,permission) in COMMANDS.items()]

    @app.post('/api/projects')
    def create_project(data:ProjectInput,user=Depends(current_user),db=Depends(get_db)):
        require(db,user,'master.manage')
        owner=db.get(m.User,data.owner_user_id)
        if not owner or not owner.active:raise DomainError('OWNER_INVALID','项目负责人无效')
        if data.execution_mode not in {'INTERNAL','FULL_OUTSOURCE'}:raise DomainError('MODE_INVALID','加工方式无效')
        if data.customer_id and not db.get(m.Customer,data.customer_id):raise DomainError('CUSTOMER_UNKNOWN','客户不存在')
        project=m.Project(code=data.code,name=data.name);db.add(project);db.flush()
        db.add(m.ProjectProfile(project_id=project.id,customer_id=data.customer_id,owner_user_id=data.owner_user_id,execution_mode=data.execution_mode))
        record(db,user,'project.created',project.id);db.commit();return domains.values(project)

    @app.get('/api/master/{kind}')
    def masters(kind:str,project_id:str|None=None,user=Depends(current_user),db=Depends(get_db)):
        tables={'suppliers':m.Supplier,'customers':m.Customer,'warehouses':m.Warehouse,'materials':m.Material,'molds':m.Mold}
        if kind not in tables:raise DomainError('KIND_UNKNOWN','主数据类型无效')
        model=tables[kind];query=select(model)
        if not user.super_admin:
            if kind=='suppliers' and project_id:
                query=query.where(predicate(db,user,'order.edit',{'project_id':literal(project_id),'category':m.Supplier.category}))
            elif kind=='warehouses':query=query.where(predicate(db,user,'warehouse.read',{'warehouse_id':m.Warehouse.id}))
            else:require(db,user,'master.manage')
        return [domains.values(row) for row in db.scalars(query.limit(100))]

    @app.post('/api/master/{kind}')
    def create_master(kind:str,data:MasterInput,user=Depends(current_user),db=Depends(get_db)):
        require(db,user,'master.manage')
        if kind=='suppliers':
            if data.category not in {'hardware','raw_material','outsource'}:raise DomainError('CATEGORY_REQUIRED','须选择有效责任域')
            row=m.Supplier(code=data.code,name=data.name,category=data.category)
        elif kind=='materials':
            if data.category not in {'hardware','raw_material','outsource'} or not data.unit:raise DomainError('MATERIAL_INVALID','料品须有分类及单位')
            row=m.Material(code=data.code,name=data.name,category=data.category,unit=data.unit)
        elif kind=='customers':row=m.Customer(code=data.code,name=data.name)
        elif kind=='warehouses':row=m.Warehouse(code=data.code,name=data.name)
        elif kind=='molds':row=m.Mold(internal_number=data.code,name=data.name)
        else:raise DomainError('KIND_UNKNOWN','主数据类型无效')
        db.add(row);db.flush();record(db,user,'master.created',row.id,{'kind':kind});db.commit();return domains.values(row)

    @app.get('/api/orders')
    def orders(user=Depends(current_user),db=Depends(get_db)):
        return procurement.visible_orders(db,user)

    @app.put('/api/orders/{order_id}')
    def edit_order(order_id:str,data:OrderInput,user=Depends(current_user),db=Depends(get_db)):
        result=procurement.edit_order(db,user,order_id,data);db.commit();return result

    @app.get('/api/warehouse/receipts')
    def receipts(user=Depends(current_user),db=Depends(get_db)):
        allowed=predicate(db,user,'warehouse.read',{'project_id':m.PurchaseOrder.project_id,
            'category':m.Material.category,'warehouse_id':m.GoodsReceipt.warehouse_id})
        query=select(m.GoodsReceipt,m.Material).join(m.SupplierShipment).join(m.OrderLine).join(m.PurchaseOrder).join(m.Material,m.Material.id==m.OrderLine.material_id).where(allowed)
        return [{**domains.values(receipt),'material':material.name,
                 'inspections':[domains.values(i) for i in domains.rows(db,m.ReceiptInspection,receipt_id=receipt.id)]} for receipt,material in db.execute(query.limit(100))]

    @app.get('/api/warehouse/stock')
    def stock(user=Depends(current_user),db=Depends(get_db)):
        allowed=predicate(db,user,'warehouse.read',{'project_id':m.StockBalance.project_id,
                'category':m.Material.category,'warehouse_id':m.StockBalance.warehouse_id})
        return [{**domains.values(balance),'material':material.name,'unit':material.unit} for balance,material in db.execute(select(m.StockBalance,m.Material).join(m.Material).where(allowed).limit(100))]

    @app.post('/api/risk/policy')
    def risk_policy(data:RiskInput,user=Depends(current_user),db=Depends(get_db)):
        require(db,user,'risk.configure')
        # Policy serialization must not upgrade the login identity's shared lock.
        db.execute(text('SELECT pg_advisory_xact_lock(72931, 1)'))
        version=(db.scalar(select(func.max(m.RiskPolicy.version))) or 0)+1
        policy=m.RiskPolicy(version=version,near_due_days=data.near_due_days,configured_by=user.id)
        db.add(policy);record(db,user,'risk.policy.published',str(version),{'reason':data.reason,'near_due_days':data.near_due_days});db.commit()
        return {'version':version,'near_due_days':data.near_due_days}

    @app.get('/api/risk/policy')
    def current_risk_policy(user=Depends(current_user),db=Depends(get_db)):
        row=db.scalar(select(m.RiskPolicy).order_by(m.RiskPolicy.version.desc()).limit(1))
        return {'version':row.version,'near_due_days':row.near_due_days} if row else None

    @app.post('/api/risk/analyze')
    def analyze(project_id:str|None=None,user=Depends(current_user),db=Depends(get_db)):
        result=procurement.delivery_risks(db,user,project_id);db.commit();return result
