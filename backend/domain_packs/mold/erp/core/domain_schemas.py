from datetime import date, timedelta
from decimal import Decimal
from typing import Literal
from pydantic import Field, model_validator
from domain_packs.mold.ports.schemas import StrictModel

Amount = Decimal


class StageInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    condition: str = Field(min_length=1, max_length=2000)
    ratio_percent: Decimal | None = Field(default=None, gt=0, le=100, max_digits=7, decimal_places=4)
    trigger_event: str | None = Field(default=None, min_length=1, max_length=120)
    trigger_date: date | None = None
    credit_days: int | None = Field(default=None, ge=0, le=3650)
    expected_due_date: date | None = None
    schedule_evidence: str | None = Field(default=None, min_length=1, max_length=4000)
    trigger_evidence: str | None = Field(default=None, min_length=1, max_length=4000)
    special_mark: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode='after')
    def validate_schedule(self):
        if self.trigger_date and not self.trigger_evidence:
            raise ValueError('填写触发日期时必须同时填写触发依据')
        if self.trigger_date and self.expected_due_date and self.expected_due_date < self.trigger_date:
            raise ValueError('预计到期日期不能早于触发日期')
        if self.trigger_date and self.credit_days is not None:
            derived = self.trigger_date + timedelta(days=self.credit_days)
            if self.expected_due_date is not None and self.expected_due_date != derived:
                raise ValueError('预计到期日期必须与触发日期加账期天数一致')
            self.expected_due_date = derived
        return self


class ContractInput(StrictModel):
    customer_id: str | None = None
    supplier_id: str | None = None
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    contract_number: str = Field(min_length=1, max_length=100)
    expected_date: date | None = None
    replaces_id: str | None = None
    relation_type: Literal['ORIGINAL','REPLACEMENT','ADDITION'] = 'ORIGINAL'
    settlement_allocation_evidence: str | None = Field(default=None, min_length=1, max_length=4000)
    stages: list[StageInput] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def validate_relation(self):
        if self.relation_type == 'ORIGINAL':
            if self.replaces_id or self.settlement_allocation_evidence:
                raise ValueError('原始合同不能填写前序合同或历史收付款分配依据')
        elif self.relation_type == 'ADDITION':
            if not self.replaces_id:
                raise ValueError('追加合同必须关联前序合同')
            if self.settlement_allocation_evidence:
                raise ValueError('追加合同不迁移历史收付款，不应填写分配依据')
        elif not self.replaces_id or not self.settlement_allocation_evidence:
            raise ValueError('替代合同必须关联前序合同并填写历史收付款分配依据')
        return self


class PaymentInput(StrictModel):
    stage_id: str
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(pattern=r'^[A-Z]{3}$')


class TaskInput(StrictModel):
    key: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,79}$')
    name: str = Field(min_length=1, max_length=150)
    owner_user_id: str
    planned_start: date
    planned_end: date
    prerequisites: list[str] = Field(default_factory=list, max_length=100)


class PlanInput(StrictModel):
    previous_id: str | None = None
    reason: str = Field(min_length=1, max_length=4000)
    tasks: list[TaskInput] = Field(min_length=1, max_length=200)


class DecisionInput(StrictModel):
    source_subject_id: str | None = None
    decision: str = Field(min_length=1, max_length=40)
    execution_mode: Literal['INTERNAL','FULL_OUTSOURCE'] | None = None
    effective_date: date
    evidence: str = Field(min_length=1, max_length=4000)
    amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    currency: str | None = Field(default=None, pattern=r'^[A-Z]{3}$')


class PauseResumeInput(StrictModel):
    decision: Literal['PAUSE','RESUME']
    effective_date: date
    expected_resume_date: date | None = None
    reason: str = Field(min_length=1,max_length=4000)
    evidence: str = Field(min_length=1,max_length=4000)
    source_pause_subject_id: str | None = Field(default=None,max_length=36)


class ProjectCloseInput(StrictModel):
    decision: Literal['TERMINATE','NORMAL_CLOSE','SETTLEMENT_CLOSE']
    effective_date: date
    reason: str = Field(min_length=1,max_length=4000)
    evidence: str = Field(min_length=1,max_length=4000)
    project_version: int = Field(ge=1)
    closure_case_id: str | None = Field(default=None,max_length=36)
    closure_case_version: int | None = Field(default=None,ge=1)
    current_stage: str | None = Field(default=None,max_length=200)
    completed_work_summary: str | None = Field(default=None,max_length=10000)
    incurred_cost_summary: str | None = Field(default=None,max_length=10000)
    incurred_cost_amount: Decimal | None = Field(default=None,ge=0,max_digits=18,decimal_places=2)
    currency: str | None = Field(default=None,pattern=r'^[A-Z]{3}$')


class ImpactInput(StrictModel):
    task_id: str
    action: Literal['KEEP','PAUSE','CANCEL','REWORK']


class ChangeInput(StrictModel):
    problem: str = Field(min_length=1, max_length=4000)
    solution: str = Field(min_length=1, max_length=4000)
    customer_due_affected: bool = False
    customer_evidence: str | None = Field(default=None, max_length=4000)
    impacts: list[ImpactInput] = Field(min_length=1, max_length=100)


class SubjectInput(StrictModel):
    kind: str
    project_id: str
    category: str | None = None
    warehouse_id: str | None = None
    remark: str = Field(default='', max_length=4000)
    detail: dict


class DesignItemInput(StrictModel):
    material_id: str
    quantity: Decimal = Field(gt=0,max_digits=18,decimal_places=6)
    route: Literal['INTERNAL','PURCHASE','OUTSOURCE']
    task_id: str | None = None


class DesignInput(StrictModel):
    design_type: Literal['NEW_MOLD', 'MOLD_CHANGE'] = Field(description='新模或改模，选择适用审批流程的依据')
    drawing_revision: str = Field(min_length=1,max_length=100)
    drawing_evidence: str = Field(min_length=1,max_length=4000)
    reviewer_id: str
    items: list[DesignItemInput] = Field(min_length=1,max_length=200)


class PriceInput(StrictModel):
    supplier_id: str
    material_id: str
    unit_price: Decimal = Field(ge=0,max_digits=18,decimal_places=6)
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    valid_from: date
    valid_to: date
    quote_evidence: str = Field(min_length=1,max_length=4000)


class AssemblyInput(StrictModel):
    design_id: str
    supervisor_id: str
    prerequisites_evidence: str = Field(min_length=1,max_length=4000)
    planned_date: date


class TrialInput(StrictModel):
    assembly_id: str
    planned_date: date
    location: str = Field(min_length=1,max_length=200)
    acceptance_criteria: str = Field(min_length=1,max_length=4000)
    responsible_id: str


class CorrectionInput(StrictModel):
    original_payment_id: str
    reason: str = Field(min_length=1,max_length=4000)
    reversal_evidence: str = Field(min_length=1,max_length=4000)
    reversal_date: date


class ContactResolutionInput(StrictModel):
    case_id: str = Field(min_length=1,max_length=36)
    case_revision: int = Field(ge=1)
    solution: str = Field(min_length=1,max_length=10000)
    customer_due_affected: bool
    customer_evidence: str | None = Field(default=None,max_length=4000)


CATALOG = {
    'contact_resolution': {'name':'联络单处理方案审批','schema':ContactResolutionInput,'risk':'H09'},
    'design_route': {'name':'设计、BOM 与加工路线', 'schema':DesignInput, 'risk':'H09'},
    'purchase_price': {'name':'采购价格审批', 'schema':PriceInput, 'risk':'H02'},
    'assembly_issue': {'name':'装配任务下发', 'schema':AssemblyInput, 'risk':'H08'},
    'trial_request': {'name':'试模申请', 'schema':TrialInput, 'risk':'H08'},
    'finance_correction': {'name':'财务冲正审批', 'schema':CorrectionInput, 'risk':'H04'},
    'quote_acceptance': {'name':'报价与承接决定', 'schema':DecisionInput, 'decisions':['ACCEPT','REJECT'], 'risk':'H01'},
    'internal_start': {'name':'正式开工通知', 'schema':DecisionInput, 'decisions':['START'], 'risk':'H12'},
    'sales_contract': {'name':'销售合同', 'schema':ContractInput, 'risk':'H01'},
    'full_outsource_contract': {'name':'整套委外合同', 'schema':ContractInput, 'risk':'H03'},
    'project_plan': {'name':'项目计划', 'schema':PlanInput, 'risk':'H10'},
    'plan_change': {'name':'计划变更', 'schema':PlanInput, 'risk':'H10'},
    'pause_resume': {'name':'暂停与恢复', 'schema':PauseResumeInput, 'risk':'H12'},
    'supplier_payment': {'name':'供应商付款申请', 'schema':PaymentInput, 'risk':'H04'},
    'engineering_change': {'name':'工程联络单', 'schema':ChangeInput, 'risk':'H09'},
    'project_close': {'name':'项目终止与关闭', 'schema':ProjectCloseInput, 'risk':'H12'},
}

READ_FIELDS = ['id','kind','number','project_id','category','warehouse_id','remark','status','revision',
               'created_by','created_at','detail']
PERMISSIONS = {f'{kind}.{action}': READ_FIELDS if action=='read' else ['*']
               for kind in CATALOG for action in ['read','create','submit','approve','execute']}
PERMISSIONS.update({
    'master.manage':['*'], 'order.read':['id','number','project_id','supplier_id','status','currency','version','lines'],
    'order.edit':['*'], 'order.issue':['*'], 'shipment.confirm':['*'], 'exception.report':['*'],
    'exception.close':['*'], 'warehouse.read':['*'], 'warehouse.configure':['*'], 'receipt.confirm':['*'],
    'inspection.confirm':['*'], 'stock.issue':['*'], 'finance.confirm':['*'], 'finance.condition':['*'],
    'customer_receipt.read':['*'], 'customer_receipt.confirm':['*'],
    'plan.execute':['*'], 'change.implement':['*'], 'change.recheck':['*'], 'risk.read':['*'], 'risk.configure':['*'],
    'assembly.execute':['*'], 'trial.confirm':['*'], 'identity.reference':['*'],
})
