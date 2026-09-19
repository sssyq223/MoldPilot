from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field, ValidationError
from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.authorization import access, fingerprint, predicate, require, select_fields
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import get_db, now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.project.plan_tools import ProjectPlanContextInput, _strength
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.ports.security import current_user


OUTSOURCE_KEYWORDS = ("委外", "供应商", "外协", "外包", "outsource", "supplier")
ISSUE_KEYWORDS = ("质量", "延期", "整改", "复验", "扣款", "索赔", "验收", "交付", "合同", "结算")
FULL_OUTSOURCE_PROPOSAL_TOOLS = {
    "prepare_supplier_material_handoff",
    "prepare_supplier_material_verification",
    "prepare_supplier_progress_policy",
    "prepare_supplier_progress_report",
}
PROGRESS_EVIDENCE_KINDS = Literal[
    "PHOTO", "DOCUMENT", "QUALITY_REPORT", "SCHEDULE", "ISSUE_LIST", "DELIVERY_PROOF", "OTHER"
]


class SupplierMaterialHandoffProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    supplier_id: str = Field(min_length=1, max_length=36)
    contract_subject_id: str | None = Field(default=None, max_length=36)
    file_id: str | None = Field(default=None, max_length=36,
        description='本轮明确附加或当前会话历史中被用户明确引用的交接文件 ID')
    document_title: str = Field(min_length=1, max_length=200)
    document_type: Literal["CUSTOMER_MATERIAL","DESIGN_DRAWING","TECHNICAL_SPEC","QUALITY_STANDARD","OTHER"] = "CUSTOMER_MATERIAL"
    approval_status: Literal["DRAFT","APPROVED","REVOKED"] = "APPROVED"
    provided_date: date
    provided_to: str = Field(min_length=1, max_length=150)
    handoff_channel: Literal["MANUAL","EMAIL","IMPORT","ERP","OTHER"] = "MANUAL"
    evidence: str = Field(min_length=1, max_length=4000)
    source_ref: str | None = Field(default=None, max_length=120)


class SupplierMaterialVerificationProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    supplier_id: str = Field(min_length=1, max_length=36)
    contract_subject_id: str = Field(min_length=1, max_length=36)
    handoff_id: str = Field(min_length=1, max_length=36)
    response_file_id: str | None = Field(default=None, max_length=36,
        description='本轮明确附加或当前会话历史中被用户明确引用的供应商回复文件 ID')
    response_date: date
    result: Literal["RECEIVED","ACCEPTED","NEEDS_CLARIFICATION","REJECTED"]
    supplier_contact: str = Field(min_length=1, max_length=150)
    response_channel: Literal["MANUAL","EMAIL","IMPORT","OTHER"] = "MANUAL"
    response_summary: str = Field(default="", max_length=4000)
    follow_up_due_date: date | None = None
    evidence: str = Field(min_length=1, max_length=4000)
    source_system: Literal["MANUAL","IMPORT"] = "MANUAL"
    source_ref: str = Field(min_length=1, max_length=120)


class SupplierProgressReportProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    supplier_id: str = Field(min_length=1, max_length=36)
    contract_subject_id: str = Field(min_length=1, max_length=36)
    plan_task_id: str | None = Field(default=None, max_length=36)
    stage_key: str = Field(min_length=1, max_length=80)
    stage_name: str = Field(min_length=1, max_length=150)
    report_date: date
    status: Literal["ON_TRACK","AT_RISK","BLOCKED","DONE","REWORK"]
    progress_percent: int | None = Field(default=None, ge=0, le=100)
    next_due_date: date | None = None
    issue_summary: str = Field(default="", max_length=4000)
    evidence: str = Field(min_length=1, max_length=4000)
    evidence_items: list[PROGRESS_EVIDENCE_KINDS] = Field(default_factory=list, max_length=7)
    source_system: Literal["MANUAL","IMPORT"] = "MANUAL"
    source_ref: str = Field(min_length=1, max_length=120)
    followed_by: str | None = Field(default=None, max_length=36)


class SupplierProgressPolicyProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    supplier_id: str = Field(min_length=1, max_length=36)
    contract_subject_id: str = Field(min_length=1, max_length=36)
    plan_task_id: str | None = Field(default=None, max_length=36)
    stage_key: str = Field(min_length=1, max_length=80)
    stage_name: str = Field(min_length=1, max_length=150)
    frequency_days: int = Field(ge=1, le=90)
    effective_from: date
    first_due_date: date
    evidence_requirements: list[PROGRESS_EVIDENCE_KINDS] = Field(min_length=1, max_length=7)
    basis: str = Field(min_length=1, max_length=4000)
    source_ref: str = Field(min_length=1, max_length=120)
    replaces_policy_id: str | None = Field(default=None, max_length=36)


def supplier_material_handoff_schema():
    return SupplierMaterialHandoffProposalInput.model_json_schema()


def supplier_material_verification_schema():
    return SupplierMaterialVerificationProposalInput.model_json_schema()


def supplier_progress_report_schema():
    return SupplierProgressReportProposalInput.model_json_schema()


def supplier_progress_policy_schema():
    return SupplierProgressPolicyProposalInput.model_json_schema()


def parse_supplier_material_handoff(arguments):
    try:
        data = SupplierMaterialHandoffProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "供应商资料交接参数不完整或不符合要求：" + error.errors()[0]["msg"]) from None
    if data.approval_status == "APPROVED" and not data.contract_subject_id:
        raise DomainError("INVALID_TOOL_INPUT", "正式获准资料交接必须关联已生效整套委外合同")
    return data


def parse_supplier_material_verification(arguments):
    try:
        data = SupplierMaterialVerificationProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "供应商资料核验参数不完整或不符合要求：" + error.errors()[0]["msg"]) from None
    today = now().date()
    if data.response_date > today:
        raise DomainError("INVALID_TOOL_INPUT", "供应商资料核验日期不能晚于当前日期")
    if data.follow_up_due_date and data.follow_up_due_date < data.response_date:
        raise DomainError("INVALID_TOOL_INPUT", "资料核验跟进日期不能早于供应商回复日期")
    if data.result in {"NEEDS_CLARIFICATION", "REJECTED"}:
        if not data.response_summary.strip():
            raise DomainError("INVALID_TOOL_INPUT", "供应商要求澄清或退回资料时必须填写回复说明")
        if not data.follow_up_due_date:
            raise DomainError("INVALID_TOOL_INPUT", "供应商要求澄清或退回资料时必须填写跟进日期")
    return data


def parse_supplier_progress_report(arguments):
    try:
        data = SupplierProgressReportProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "供应商节点上报参数不完整或不符合要求：" + error.errors()[0]["msg"]) from None
    if data.report_date > now().date():
        raise DomainError("INVALID_TOOL_INPUT", "供应商节点上报日期不能晚于当前日期")
    if data.next_due_date and data.next_due_date < data.report_date:
        raise DomainError("INVALID_TOOL_INPUT", "下次跟进日期不能早于上报日期")
    if data.status in {"AT_RISK", "BLOCKED", "REWORK"}:
        if not data.issue_summary.strip():
            raise DomainError("INVALID_TOOL_INPUT", "风险、阻塞或返工上报必须填写问题摘要")
        if not data.next_due_date:
            raise DomainError("INVALID_TOOL_INPUT", "风险、阻塞或返工上报必须填写下次跟进日期")
    if data.status == "DONE" and data.progress_percent != 100:
        raise DomainError("INVALID_TOOL_INPUT", "节点完成时进度必须为 100%")
    if data.progress_percent == 100 and data.status != "DONE":
        raise DomainError("INVALID_TOOL_INPUT", "进度为 100% 时节点状态必须为已完成")
    if len(set(data.evidence_items)) != len(data.evidence_items):
        raise DomainError("INVALID_TOOL_INPUT", "供应商节点上报证据类型不能重复")
    return data


def parse_supplier_progress_policy(arguments):
    try:
        data = SupplierProgressPolicyProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "供应商上报规则参数不完整或不符合要求：" + error.errors()[0]["msg"]) from None
    if data.first_due_date < data.effective_from:
        raise DomainError("INVALID_TOOL_INPUT", "首次上报日期不能早于规则生效日期")
    if len(set(data.evidence_requirements)) != len(data.evidence_requirements):
        raise DomainError("INVALID_TOOL_INPUT", "供应商上报规则的证据类型不能重复")
    return data


def _active_progress_policy(db, project_id, supplier_id, contract_subject_id, stage_key):
    return db.scalar(
        select(m.SupplierProgressPolicy).where(
            m.SupplierProgressPolicy.project_id == project_id,
            m.SupplierProgressPolicy.supplier_id == supplier_id,
            m.SupplierProgressPolicy.contract_subject_id == contract_subject_id,
            m.SupplierProgressPolicy.stage_key == stage_key,
            m.SupplierProgressPolicy.active.is_(True),
        ).order_by(m.SupplierProgressPolicy.version.desc(), m.SupplierProgressPolicy.created_at.desc())
    )


def preview_supplier_progress_policy(db, user, data: SupplierProgressPolicyProposalInput):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    scope = {"project_id": project.id, "category": "outsource"}
    require(db, user, "project.read", {"project_id": project.id})
    require(db, user, "full_outsource_contract.read", scope)
    require(db, user, "full_outsource_contract.execute", scope)
    if project.row_version != data.project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备", 409)
    supplier = db.get(m.Supplier, data.supplier_id)
    if not supplier or not supplier.active:
        raise DomainError("SUPPLIER_INVALID", "供应商上报规则必须关联有效委外供应商", 409)
    contract = db.get(m.BusinessSubject, data.contract_subject_id)
    contract_detail = db.get(m.ContractDetail, data.contract_subject_id) if contract else None
    if not contract or contract.project_id != project.id or contract.kind != "full_outsource_contract" or not contract_detail:
        raise DomainError("CONTRACT_NOT_FOUND", "整套委外合同不存在或不属于该项目", 404)
    if contract_detail.supplier_id != supplier.id:
        raise DomainError("CONTRACT_SUPPLIER_MISMATCH", "整套委外合同供应商与上报规则供应商不一致", 409)
    if contract.status not in {"EFFECTIVE", "CLOSED"}:
        raise DomainError("CONTRACT_NOT_EFFECTIVE", "供应商上报规则必须关联已生效或已关闭整套委外合同", 409)
    plan_task = None
    if data.plan_task_id:
        plan_task = db.get(m.PlanTask, data.plan_task_id)
        plan = db.get(m.BusinessSubject, plan_task.plan_id) if plan_task else None
        if not plan_task or not plan or plan.project_id != project.id or plan.kind not in {"project_plan", "plan_change"}:
            raise DomainError("PLAN_TASK_NOT_FOUND", "计划节点不存在或不属于该项目", 404)
        if data.stage_key != plan_task.key or data.stage_name != plan_task.name:
            raise DomainError("PLAN_TASK_MISMATCH", "节点标识或名称与所选计划任务不一致，请重新查询后准备", 409)
    current = _active_progress_policy(db, project.id, supplier.id, contract.id, data.stage_key)
    if current and data.replaces_policy_id != current.id:
        raise DomainError("SUPPLIER_PROGRESS_POLICY_CHANGED", "当前供应商上报规则已变化，请重新查询后替换", 409)
    if not current and data.replaces_policy_id:
        raise DomainError("SUPPLIER_PROGRESS_POLICY_CHANGED", "待替换的供应商上报规则已失效，请重新查询", 409)
    if db.scalar(select(m.SupplierProgressPolicy.id).where(
        m.SupplierProgressPolicy.project_id == project.id,
        m.SupplierProgressPolicy.supplier_id == supplier.id,
        m.SupplierProgressPolicy.contract_subject_id == contract.id,
        m.SupplierProgressPolicy.stage_key == data.stage_key,
        m.SupplierProgressPolicy.source_ref == data.source_ref,
    )):
        raise DomainError("SUPPLIER_PROGRESS_POLICY_DUPLICATE", "该供应商上报规则来源已登记", 409)
    version = (current.version + 1) if current else 1
    display = {
        "操作": "配置供应商节点上报规则",
        "项目": project.code + " · " + project.name,
        "项目版本": project.row_version,
        "供应商": supplier.name,
        "整套委外合同": contract_detail.contract_number,
        "计划节点": (plan_task.key + " · " + plan_task.name) if plan_task else "未关联计划任务",
        "上报阶段": data.stage_key + " · " + data.stage_name,
        "填报频率": f"每 {data.frequency_days} 天",
        "生效日期": data.effective_from.isoformat(),
        "首次应报日期": data.first_due_date.isoformat(),
        "必需证据类型": list(data.evidence_requirements),
        "规则版本": version,
        "替换规则": current.id if current else "首次配置",
        "来源引用": data.source_ref,
        "制定依据": data.basis,
        "说明": "本人确认后建立版本化上报规则；不会代替供应商上报、ERP 执行事实、收货质检或客户验收。",
    }
    return project, supplier, contract, plan_task, current, version, display


def create_supplier_progress_policy(db, user, data: SupplierProgressPolicyProposalInput):
    project, supplier, contract, plan_task, current, version, _ = preview_supplier_progress_policy(db, user, data)
    if current:
        current.active = False
    row = m.SupplierProgressPolicy(
        project_id=project.id,
        supplier_id=supplier.id,
        contract_subject_id=contract.id,
        plan_task_id=plan_task.id if plan_task else None,
        stage_key=data.stage_key,
        stage_name=data.stage_name,
        frequency_days=data.frequency_days,
        effective_from=data.effective_from,
        first_due_date=data.first_due_date,
        evidence_requirements=list(data.evidence_requirements),
        basis=data.basis,
        source_ref=data.source_ref,
        version=version,
        active=True,
        supersedes_id=current.id if current else None,
        created_by=user.id,
    )
    db.add(row)
    db.flush()
    return row


def preview_supplier_progress_report(db, user, data: SupplierProgressReportProposalInput):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    scope = {"project_id": project.id, "category": "outsource"}
    require(db, user, "project.read", {"project_id": project.id})
    require(db, user, "full_outsource_contract.read", scope)
    require(db, user, "full_outsource_contract.execute", scope)
    if project.row_version != data.project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备", 409)
    supplier = db.get(m.Supplier, data.supplier_id)
    if not supplier or not supplier.active:
        raise DomainError("SUPPLIER_INVALID", "供应商节点上报必须关联有效委外供应商", 409)
    contract = db.get(m.BusinessSubject, data.contract_subject_id)
    contract_detail = db.get(m.ContractDetail, data.contract_subject_id) if contract else None
    if not contract or contract.project_id != project.id or contract.kind != "full_outsource_contract" or not contract_detail:
        raise DomainError("CONTRACT_NOT_FOUND", "整套委外合同不存在或不属于该项目", 404)
    if contract_detail.supplier_id != supplier.id:
        raise DomainError("CONTRACT_SUPPLIER_MISMATCH", "整套委外合同供应商与节点上报供应商不一致", 409)
    if contract.status not in {"EFFECTIVE", "CLOSED"}:
        raise DomainError("CONTRACT_NOT_EFFECTIVE", "供应商节点上报必须关联已生效或已关闭整套委外合同", 409)
    plan_task = None
    if data.plan_task_id:
        plan_task = db.get(m.PlanTask, data.plan_task_id)
        plan = db.get(m.BusinessSubject, plan_task.plan_id) if plan_task else None
        if not plan_task or not plan or plan.project_id != project.id or plan.kind not in {"project_plan", "plan_change"}:
            raise DomainError("PLAN_TASK_NOT_FOUND", "计划节点不存在或不属于该项目", 404)
        if data.stage_key != plan_task.key or data.stage_name != plan_task.name:
            raise DomainError("PLAN_TASK_MISMATCH", "节点标识或名称与所选计划任务不一致，请重新查询后准备", 409)
    follower = db.get(m.User, data.followed_by) if data.followed_by else user
    if not follower or not follower.active:
        raise DomainError("FOLLOWER_INVALID", "采购跟进人不存在或账号已停用", 409)
    if db.scalar(select(m.SupplierProgressReport.id).where(
        m.SupplierProgressReport.project_id == project.id,
        m.SupplierProgressReport.supplier_id == supplier.id,
        m.SupplierProgressReport.stage_key == data.stage_key,
        m.SupplierProgressReport.report_date == data.report_date,
        m.SupplierProgressReport.source_ref == data.source_ref,
    )):
        raise DomainError("SUPPLIER_PROGRESS_REPORT_DUPLICATE", "该供应商节点上报来源已登记", 409)
    policy = _active_progress_policy(db, project.id, supplier.id, contract.id, data.stage_key)
    missing_evidence = sorted(set(policy.evidence_requirements or []) - set(data.evidence_items)) if policy else []
    if missing_evidence:
        raise DomainError("SUPPLIER_PROGRESS_EVIDENCE_MISSING", "供应商节点上报缺少规则要求的证据类型：" + "、".join(missing_evidence), 409)
    display = {
        "操作": "登记供应商节点上报证据",
        "项目": project.code + " · " + project.name,
        "项目版本": project.row_version,
        "供应商": supplier.name,
        "整套委外合同": contract_detail.contract_number,
        "计划节点": (plan_task.key + " · " + plan_task.name) if plan_task else "未关联计划任务",
        "上报阶段": data.stage_key + " · " + data.stage_name,
        "上报日期": data.report_date.isoformat(),
        "节点状态": data.status,
        "进度": (str(data.progress_percent) + "%") if data.progress_percent is not None else "未填写",
        "下次跟进日期": data.next_due_date.isoformat() if data.next_due_date else "不适用",
        "问题摘要": data.issue_summary or "无",
        "结构化证据类型": list(data.evidence_items) if data.evidence_items else "未配置规则或未分类",
        "适用上报规则": (f"第 {policy.version} 版 · 每 {policy.frequency_days} 天") if policy else "当前未配置结构化规则",
        "采购跟进人": follower.display_name,
        "录入方式": data.source_system,
        "来源引用": data.source_ref,
        "依据": data.evidence,
        "说明": "本人确认后仅登记供应商节点上报证据；不创建供应商门户，不代表我方收货、质检、客户验收或 ERP 生产节点已完成。",
    }
    return project, supplier, contract, plan_task, follower, display


def create_supplier_progress_report(db, user, data: SupplierProgressReportProposalInput):
    _, supplier, contract, plan_task, follower, _ = preview_supplier_progress_report(db, user, data)
    row = m.SupplierProgressReport(project_id=data.project_id, supplier_id=supplier.id,
        contract_subject_id=contract.id, plan_task_id=plan_task.id if plan_task else None,
        stage_key=data.stage_key, stage_name=data.stage_name, report_date=data.report_date,
        status=data.status, progress_percent=data.progress_percent, next_due_date=data.next_due_date,
        issue_summary=data.issue_summary, evidence=data.evidence, evidence_items=list(data.evidence_items), source_system=data.source_system,
        source_ref=data.source_ref, reported_by=user.id, followed_by=follower.id)
    db.add(row)
    db.flush()
    return row


def preview_supplier_material_handoff(db, user, data: SupplierMaterialHandoffProposalInput, run=None):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    scope = {"project_id": project.id, "category": "outsource"}
    require(db, user, "project.read", {"project_id": project.id})
    require(db, user, "full_outsource_contract.read", scope)
    require(db, user, "full_outsource_contract.execute", scope)
    if project.row_version != data.project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备", 409)
    supplier = db.get(m.Supplier, data.supplier_id)
    if not supplier or not supplier.active:
        raise DomainError("SUPPLIER_INVALID", "资料交接必须关联有效委外供应商", 409)
    contract = None
    contract_detail = None
    if data.contract_subject_id:
        contract = db.get(m.BusinessSubject, data.contract_subject_id)
        if not contract or contract.project_id != project.id or contract.kind != "full_outsource_contract":
            raise DomainError("CONTRACT_NOT_FOUND", "整套委外合同不存在或不属于该项目", 404)
        contract_detail = db.get(m.ContractDetail, contract.id)
        if not contract_detail or contract_detail.supplier_id != supplier.id:
            raise DomainError("CONTRACT_SUPPLIER_MISMATCH", "整套委外合同供应商与资料交接供应商不一致", 409)
        if data.approval_status == "APPROVED" and contract.status not in {"EFFECTIVE", "CLOSED"}:
            raise DomainError("CONTRACT_NOT_EFFECTIVE", "正式获准资料交接必须关联已生效或已关闭整套委外合同", 409)
    if data.file_id:
        from domain_packs.mold.ports.files import reference_run_file, uploaded_file
        (reference_run_file(db, user, run, data.file_id) if run
         else uploaded_file(db, user, data.file_id))
    if data.source_ref and db.scalar(select(m.SupplierMaterialHandoff.id).where(
        m.SupplierMaterialHandoff.project_id == project.id,
        m.SupplierMaterialHandoff.supplier_id == supplier.id,
        m.SupplierMaterialHandoff.document_title == data.document_title,
        m.SupplierMaterialHandoff.provided_date == data.provided_date,
        m.SupplierMaterialHandoff.source_ref == data.source_ref,
    )):
        raise DomainError("SUPPLIER_MATERIAL_HANDOFF_DUPLICATE", "该供应商资料交接来源已登记", 409)
    display = {
        "操作": "登记供应商资料交接证据",
        "项目": project.code + " · " + project.name,
        "项目版本": project.row_version,
        "供应商": supplier.name,
        "整套委外合同": contract_detail.contract_number if contract_detail else "未关联",
        "资料标题": data.document_title,
        "资料类型": data.document_type,
        "审批状态": data.approval_status,
        "交接日期": data.provided_date.isoformat(),
        "交接对象": data.provided_to,
        "交接渠道": data.handoff_channel,
        "资料文件": data.file_id or "未关联文件",
        "来源引用": data.source_ref or "未填写",
        "依据": data.evidence,
        "说明": "本人确认后仅登记向供应商提供资料的证据；不创建供应商门户、不代表供应商已核验、不触发 ERP 发货或生产执行。",
    }
    return project, supplier, contract, display


def create_supplier_material_handoff(db, user, data: SupplierMaterialHandoffProposalInput):
    _, supplier, contract, _ = preview_supplier_material_handoff(db, user, data)
    row = m.SupplierMaterialHandoff(project_id=data.project_id, supplier_id=supplier.id,
        contract_subject_id=contract.id if contract else None, file_id=data.file_id,
        document_title=data.document_title, document_type=data.document_type,
        approval_status=data.approval_status, provided_date=data.provided_date,
        provided_to=data.provided_to, handoff_channel=data.handoff_channel,
        evidence=data.evidence, source_system="MANUAL", source_ref=data.source_ref,
        provided_by=user.id, verified_by=user.id if data.approval_status == "APPROVED" else None)
    db.add(row)
    db.flush()
    return row


def preview_supplier_material_verification(db, user, data: SupplierMaterialVerificationProposalInput, run=None):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    scope = {"project_id": project.id, "category": "outsource"}
    require(db, user, "project.read", {"project_id": project.id})
    require(db, user, "full_outsource_contract.read", scope)
    require(db, user, "full_outsource_contract.execute", scope)
    if project.row_version != data.project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备", 409)
    supplier = db.get(m.Supplier, data.supplier_id)
    if not supplier or not supplier.active:
        raise DomainError("SUPPLIER_INVALID", "资料核验必须关联有效委外供应商", 409)
    contract = db.get(m.BusinessSubject, data.contract_subject_id)
    contract_detail = db.get(m.ContractDetail, data.contract_subject_id) if contract else None
    if not contract or contract.project_id != project.id or contract.kind != "full_outsource_contract" or not contract_detail:
        raise DomainError("CONTRACT_NOT_FOUND", "整套委外合同不存在或不属于该项目", 404)
    if contract_detail.supplier_id != supplier.id:
        raise DomainError("CONTRACT_SUPPLIER_MISMATCH", "整套委外合同供应商与资料核验供应商不一致", 409)
    if contract.status not in {"EFFECTIVE", "CLOSED"}:
        raise DomainError("CONTRACT_NOT_EFFECTIVE", "供应商资料核验必须关联已生效或已关闭整套委外合同", 409)
    handoff = db.get(m.SupplierMaterialHandoff, data.handoff_id)
    if (not handoff or handoff.project_id != project.id or handoff.supplier_id != supplier.id
            or handoff.contract_subject_id != contract.id):
        raise DomainError("SUPPLIER_MATERIAL_HANDOFF_NOT_FOUND", "资料交接记录不存在或与项目、供应商、合同不一致", 404)
    if handoff.approval_status != "APPROVED":
        raise DomainError("SUPPLIER_MATERIAL_HANDOFF_NOT_APPROVED", "只有已批准并实际交接的资料才能登记供应商核验结果", 409)
    if data.response_date < handoff.provided_date:
        raise DomainError("INVALID_TOOL_INPUT", "供应商回复日期不能早于资料交接日期")
    if data.response_file_id:
        from domain_packs.mold.ports.files import reference_run_file, uploaded_file
        (reference_run_file(db, user, run, data.response_file_id) if run
         else uploaded_file(db, user, data.response_file_id))
    if db.scalar(select(m.SupplierMaterialVerification.id).where(
        m.SupplierMaterialVerification.handoff_id == handoff.id,
        m.SupplierMaterialVerification.source_ref == data.source_ref,
    )):
        raise DomainError("SUPPLIER_MATERIAL_VERIFICATION_DUPLICATE", "该供应商资料核验来源已登记", 409)
    display = {
        "操作": "登记供应商资料核验结果",
        "项目": project.code + " · " + project.name,
        "项目版本": project.row_version,
        "供应商": supplier.name,
        "整套委外合同": contract_detail.contract_number,
        "资料交接记录": handoff.id,
        "资料标题": handoff.document_title,
        "资料类型": handoff.document_type,
        "交接日期": handoff.provided_date.isoformat(),
        "供应商回复日期": data.response_date.isoformat(),
        "核验结果": data.result,
        "供应商联系人": data.supplier_contact,
        "回复渠道": data.response_channel,
        "回复说明": data.response_summary or "未填写",
        "跟进日期": data.follow_up_due_date.isoformat() if data.follow_up_due_date else "无需跟进",
        "回复文件": data.response_file_id or "未关联文件",
        "来源系统": data.source_system,
        "来源引用": data.source_ref,
        "依据": data.evidence,
        "说明": "本人确认后仅追加供应商对本次资料交接的核验结果；已收到不等于已接受，退回或待澄清不会修改或覆盖原资料交接记录。",
    }
    return project, supplier, contract, handoff, display


def create_supplier_material_verification(db, user, data: SupplierMaterialVerificationProposalInput):
    _, supplier, contract, handoff, _ = preview_supplier_material_verification(db, user, data)
    row = m.SupplierMaterialVerification(
        project_id=data.project_id,
        supplier_id=supplier.id,
        contract_subject_id=contract.id,
        handoff_id=handoff.id,
        response_file_id=data.response_file_id,
        response_date=data.response_date,
        result=data.result,
        supplier_contact=data.supplier_contact,
        response_channel=data.response_channel,
        response_summary=data.response_summary,
        follow_up_due_date=data.follow_up_due_date,
        evidence=data.evidence,
        source_system=data.source_system,
        source_ref=data.source_ref,
        recorded_by=user.id,
    )
    db.add(row)
    db.flush()
    return row


def execute_full_outsource_tool(db, user, key, arguments, run=None):
    if key == "prepare_supplier_material_handoff":
        data = parse_supplier_material_handoff(arguments)
        _, _, _, display = preview_supplier_material_handoff(db, user, data, run)
        kind = "supplier_material_handoff"
        action = "confirm_supplier_material_handoff"
        limitation = "仅准备供应商资料交接证据登记建议；本人确认后才写入，不创建供应商门户、不代表供应商已核验。"
    elif key == "prepare_supplier_material_verification":
        data = parse_supplier_material_verification(arguments)
        _, _, _, _, display = preview_supplier_material_verification(db, user, data, run)
        kind = "supplier_material_verification"
        action = "confirm_supplier_material_verification"
        limitation = "仅准备供应商对一条已批准资料交接的核验结果；本人确认后才追加记录，已收到不等于已接受，不覆盖原交接事实。"
    elif key == "prepare_supplier_progress_policy":
        data = parse_supplier_progress_policy(arguments)
        _, _, _, _, _, _, display = preview_supplier_progress_policy(db, user, data)
        kind = "supplier_progress_policy"
        action = "confirm_supplier_progress_policy"
        limitation = "仅准备版本化供应商上报频率与证据规则；本人确认后才生效，不代表供应商已上报或 ERP 节点已完成。"
    elif key == "prepare_supplier_progress_report":
        data = parse_supplier_progress_report(arguments)
        _, _, _, _, _, display = preview_supplier_progress_report(db, user, data)
        kind = "supplier_progress_report"
        action = "confirm_supplier_progress_report"
        limitation = "仅准备供应商节点上报证据登记建议；本人确认后才写入，不代表收货、质检、客户验收或 ERP 节点完成。"
    else:
        raise DomainError("TOOL_UNKNOWN", "工具未实现", 403)
    proposal = {"kind": kind, "action": action,
        "requires_approval": False, "input": data.model_dump(mode="json"), "display": display,
        "confirmation_policy": proposal_confirmation_policy(run, requires_approval=False)}
    return {"data": [], "source": "agent_proposal", "as_of": now().isoformat(), "proposal": proposal,
        "limitations": [limitation]}


def _project_card(db, user, project, matched_by=()):
    fields = access(db, user, "project.read", {"project_id": project.id}).fields
    card = select_fields(
        {"id": project.id, "code": project.code, "name": project.name, "status": project.status, "row_version": project.row_version},
        fields,
    )
    card["matched_by"] = sorted(set(matched_by))
    return card


def _visible_projects(db, user):
    rows = list(
        db.scalars(
            select(m.Project)
            .where(predicate(db, user, "project.read", {"project_id": m.Project.id}))
            .order_by(m.Project.code)
            .limit(501)
        )
    )
    return rows[:500], len(rows) > 500


def _can_read_kind(kind, allowed_tools):
    context_tools = {
        "quote_acceptance": {"query_quote_evaluation_context", "query_quote_acceptance_context", "query_full_outsource_context"},
        "full_outsource_contract": {"query_contract_context", "query_full_outsource_context"},
        "project_plan": {"query_project_plan_context", "query_full_outsource_context"},
        "plan_change": {"query_project_plan_context", "query_full_outsource_context"},
        "engineering_change": {"query_engineering_change", "query_full_outsource_context"},
        "supplier_payment": {"query_supplier_payment", "query_full_outsource_context"},
        "project_close": {"query_project_closure_context", "query_full_outsource_context"},
    }
    return "query_" + kind in allowed_tools or bool(context_tools.get(kind, set()) & allowed_tools)


def _subject_rows(db, user, project_id, kind, allowed_tools, limit=50):
    if not _can_read_kind(kind, allowed_tools):
        return []
    from domain_packs.mold.erp.core.domains import data as subject_data

    rows = []
    for subject in db.scalars(
        select(m.BusinessSubject)
        .where(m.BusinessSubject.project_id == project_id, m.BusinessSubject.kind == kind)
        .order_by(m.BusinessSubject.created_at.desc(), m.BusinessSubject.id)
        .limit(limit)
    ):
        try:
            rows.append(subject_data(db, user, subject))
        except DomainError:
            continue
    return rows


def _resolve(db, user, data: ProjectPlanContextInput, allowed_tools: set[str]):
    visible, truncated = _visible_projects(db, user)
    by_id = {project.id: project for project in visible}
    if data.project_id:
        project = by_id.get(data.project_id)
        return project, ([] if project else None), truncated

    scores = defaultdict(int)
    reasons = defaultdict(list)

    def add(project_id, value, label):
        if project_id not in by_id:
            return
        score = _strength(value, data.identifier)
        if score:
            scores[project_id] = max(scores[project_id], score)
            reasons[project_id].append(label)

    for project in visible:
        add(project.id, project.id, "项目ID")
        add(project.id, project.code, "项目编号")
        add(project.id, project.name, "项目名称")
    if by_id and _can_read_kind("full_outsource_contract", allowed_tools):
        for subject in db.scalars(
            select(m.BusinessSubject)
            .where(m.BusinessSubject.project_id.in_(list(by_id)), m.BusinessSubject.kind == "full_outsource_contract")
            .limit(501)
        ):
            try:
                row = _subject_rows(db, user, subject.project_id, "full_outsource_contract", allowed_tools, limit=100)
            except DomainError:
                row = []
            for record in row:
                if record.get("id") != subject.id:
                    continue
                detail = record.get("detail") if isinstance(record.get("detail"), dict) else {}
                add(subject.project_id, record.get("number"), "整套委外业务单号")
                add(subject.project_id, detail.get("contract_number"), "整套委外合同号")
    if ("query_purchase_orders" in allowed_tools or "query_orders" in allowed_tools) and by_id:
        for order in db.scalars(select(m.PurchaseOrder).where(m.PurchaseOrder.project_id.in_(list(by_id))).limit(501)):
            add(order.project_id, order.number, "采购/委外订单号")
    if "query_contact_cases" in allowed_tools and by_id:
        for case in db.scalars(select(m.ContactCase).where(m.ContactCase.project_id.in_(list(by_id))).limit(501)):
            add(case.project_id, case.title, "工程联络标题")
            add(case.project_id, case.customer_ref, "客户引用")
    if not scores:
        return None, [], truncated
    best = max(scores.values())
    ids = [project_id for project_id, score in scores.items() if score == best]
    if len(ids) != 1:
        return None, [_project_card(db, user, by_id[project_id], reasons[project_id]) for project_id in ids[:20]], truncated
    return by_id[ids[0]], reasons[ids[0]], truncated


def _profile(db, user, project_id):
    profile = db.get(m.ProjectProfile, project_id)
    if not profile:
        return None
    fields = access(db, user, "project.read", {"project_id": project_id}).fields
    return select_fields(
        {
            "customer_id": profile.customer_id,
            "owner_user_id": profile.owner_user_id,
            "execution_mode": profile.execution_mode,
            "customer_due_date": profile.customer_due_date.isoformat() if profile.customer_due_date else None,
            "settlement_status": profile.settlement_status,
        },
        fields | {"customer_id", "owner_user_id", "execution_mode", "customer_due_date", "settlement_status"},
    )


def _latest_outsource_acceptance(rows):
    effective = [row for row in rows if row.get("status") == "EFFECTIVE"]
    for row in effective:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        if detail.get("decision") == "ACCEPT" and detail.get("execution_mode") == "FULL_OUTSOURCE":
            return {
                "id": row.get("id"),
                "number": row.get("number"),
                "effective_date": detail.get("effective_date"),
                "amount": detail.get("amount"),
                "currency": detail.get("currency"),
                "evidence": detail.get("evidence"),
            }
    return None


def _contract_summary(rows):
    summaries = []
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        summaries.append(
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "contract_number": detail.get("contract_number"),
                "supplier_id": detail.get("supplier_id"),
                "amount": detail.get("amount"),
                "currency": detail.get("currency"),
                "expected_date": detail.get("expected_date"),
                "stage_count": len(detail.get("stages") or []),
                "signed_evidence_visible": bool(detail.get("contract_number") and row.get("status") == "EFFECTIVE"),
            }
        )
    return summaries


def _payment_summary(rows):
    result = []
    paid_total = Counter()
    requested_total = Counter()
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        currency = detail.get("currency")
        if currency and detail.get("amount"):
            requested_total[currency] += Decimal(str(detail.get("amount")))
        payments = detail.get("payments") or []
        for payment in payments:
            payment_currency = payment.get("currency") or currency
            if payment_currency and payment.get("amount"):
                paid_total[payment_currency] += Decimal(str(payment.get("amount")))
        result.append(
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "stage_id": detail.get("stage_id"),
                "amount": detail.get("amount"),
                "currency": currency,
                "reservation": detail.get("reservation"),
                "payment_count": len(payments),
            }
        )
    return {
        "requests": result,
        "totals": [
            {"currency": currency, "requested_amount": str(requested_total[currency]), "paid_amount": str(paid_total[currency])}
            for currency in sorted(set(requested_total) | set(paid_total))
        ],
    }


def _contract_signing_records(db, user, contract_rows, allowed_tools):
    if not contract_rows or not _can_read_kind("full_outsource_contract", allowed_tools):
        return []
    contract_ids = [row.get("id") for row in contract_rows if row.get("id")]
    if not contract_ids:
        return []
    allowed_contracts = {
        row.get("id")
        for row in contract_rows
        if access(db, user, "full_outsource_contract.read", {"project_id": row.get("project_id"), "category": row.get("category")}).allowed
    }
    if not allowed_contracts:
        return []
    rows = []
    q = (
        select(m.ContractSigningRecord)
        .where(m.ContractSigningRecord.contract_subject_id.in_(list(allowed_contracts)))
        .order_by(m.ContractSigningRecord.created_at.desc(), m.ContractSigningRecord.id)
        .limit(100)
    )
    for record in db.scalars(q):
        rows.append(
            {
                "id": record.id,
                "contract_subject_id": record.contract_subject_id,
                "template_name": record.template_name,
                "signing_method": record.signing_method,
                "status": record.status,
                "signed_date": record.signed_date.isoformat() if record.signed_date else None,
                "signed_file_id": record.signed_file_id,
                "signed_file_title": record.signed_file_title,
                "supplier_signer": record.supplier_signer,
                "buyer_reviewer_id": record.buyer_reviewer_id,
                "approved_by": record.approved_by,
                "evidence": record.evidence,
                "source_system": record.source_system,
                "source_ref": record.source_ref,
                "recorded_by": record.recorded_by,
            }
        )
    return rows


def _plan_tasks(rows):
    tasks = []
    active = next((row for row in rows if row.get("status") == "EFFECTIVE"), None)
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        for task in detail.get("tasks") or []:
            text = " ".join(str(task.get(key) or "") for key in ("key", "name"))
            if any(keyword in text for keyword in OUTSOURCE_KEYWORDS + ISSUE_KEYWORDS):
                tasks.append(
                    {
                        "id": task.get("id"),
                        "plan_id": row.get("id"),
                        "plan_number": row.get("number"),
                        "key": task.get("key"),
                        "name": task.get("name"),
                        "status": task.get("status"),
                        "planned_start": task.get("planned_start"),
                        "planned_end": task.get("planned_end"),
                        "actual_start": task.get("actual_start"),
                        "actual_end": task.get("actual_end"),
                        "prerequisites": task.get("prerequisites") or [],
                    }
                )
    return active, tasks[:50]


def _order_tracking(db, user, project_id, allowed_tools):
    if "query_purchase_orders" not in allowed_tools and "query_orders" not in allowed_tools:
        return {"orders": [], "totals": {}, "lines": []}
    from domain_packs.mold.erp.procurement.delivery_logistics import _augment_order_receipts, _order_headers, _order_rows, _shipment_tracking

    orders = _order_rows(db, user, project_id, allowed_tools)
    _augment_order_receipts(db, user, orders)
    tracking = _shipment_tracking(orders)
    return {"orders": _order_headers(orders), **tracking}


def _supplier_progress_policies(db, user, project_id, allowed_tools):
    if not _can_read_kind("full_outsource_contract", allowed_tools):
        return []
    if not access(db, user, "full_outsource_contract.read", {"project_id": project_id, "category": "outsource"}).allowed:
        return []
    today = now().date()
    rows = []
    policies = db.execute(
        select(m.SupplierProgressPolicy, m.Supplier)
        .join(m.Supplier, m.SupplierProgressPolicy.supplier_id == m.Supplier.id)
        .where(m.SupplierProgressPolicy.project_id == project_id, m.SupplierProgressPolicy.active.is_(True))
        .order_by(m.SupplierProgressPolicy.stage_key, m.SupplierProgressPolicy.version.desc())
        .limit(100)
    )
    for policy, supplier in policies:
        latest = db.scalar(
            select(m.SupplierProgressReport).where(
                m.SupplierProgressReport.project_id == project_id,
                m.SupplierProgressReport.supplier_id == policy.supplier_id,
                m.SupplierProgressReport.contract_subject_id == policy.contract_subject_id,
                m.SupplierProgressReport.stage_key == policy.stage_key,
                m.SupplierProgressReport.report_date >= policy.effective_from,
            ).order_by(m.SupplierProgressReport.report_date.desc(), m.SupplierProgressReport.created_at.desc())
        )
        completed = bool(latest and latest.status == "DONE")
        next_due = None if completed else (
            latest.report_date + timedelta(days=policy.frequency_days) if latest else policy.first_due_date
        )
        evidence_requirements = list(policy.evidence_requirements or [])
        latest_evidence = list(latest.evidence_items or []) if latest else []
        missing_evidence = sorted(set(evidence_requirements) - set(latest_evidence)) if latest else evidence_requirements
        rows.append({
            "id": policy.id,
            "supplier_id": supplier.id,
            "supplier_name": supplier.name,
            "contract_subject_id": policy.contract_subject_id,
            "plan_task_id": policy.plan_task_id,
            "stage_key": policy.stage_key,
            "stage_name": policy.stage_name,
            "frequency_days": policy.frequency_days,
            "effective_from": policy.effective_from.isoformat(),
            "first_due_date": policy.first_due_date.isoformat(),
            "next_due_date": next_due.isoformat() if next_due else None,
            "overdue": bool(next_due and next_due < today),
            "completed": completed,
            "evidence_requirements": evidence_requirements,
            "latest_report_id": latest.id if latest else None,
            "latest_report_date": latest.report_date.isoformat() if latest else None,
            "latest_missing_evidence": missing_evidence,
            "basis": policy.basis,
            "source_ref": policy.source_ref,
            "version": policy.version,
            "supersedes_id": policy.supersedes_id,
        })
    return rows


def _supplier_progress_reports(db, user, project_id, allowed_tools):
    if not _can_read_kind("full_outsource_contract", allowed_tools):
        return []
    if not access(db, user, "full_outsource_contract.read", {"project_id": project_id, "category": "outsource"}).allowed:
        return []
    rows = []
    active_policies = {
        (row.supplier_id, row.contract_subject_id, row.stage_key): row
        for row in db.scalars(select(m.SupplierProgressPolicy).where(
            m.SupplierProgressPolicy.project_id == project_id,
            m.SupplierProgressPolicy.active.is_(True),
        ))
    }
    q = (
        select(m.SupplierProgressReport, m.Supplier)
        .join(m.Supplier, m.SupplierProgressReport.supplier_id == m.Supplier.id)
        .where(m.SupplierProgressReport.project_id == project_id)
        .order_by(m.SupplierProgressReport.report_date.desc(), m.SupplierProgressReport.created_at.desc(), m.SupplierProgressReport.id)
        .limit(100)
    )
    today = now().date()
    for report, supplier in db.execute(q):
        overdue_followup = report.status in {"AT_RISK", "BLOCKED", "REWORK"} and report.next_due_date is not None and report.next_due_date < today
        policy = active_policies.get((report.supplier_id, report.contract_subject_id, report.stage_key))
        evidence_items = list(report.evidence_items or [])
        missing_evidence = sorted(set(policy.evidence_requirements or []) - set(evidence_items)) if policy else []
        rows.append(
            {
                "id": report.id,
                "supplier_id": supplier.id,
                "supplier_name": supplier.name,
                "contract_subject_id": report.contract_subject_id,
                "plan_task_id": report.plan_task_id,
                "stage_key": report.stage_key,
                "stage_name": report.stage_name,
                "report_date": report.report_date.isoformat(),
                "status": report.status,
                "progress_percent": report.progress_percent,
                "next_due_date": report.next_due_date.isoformat() if report.next_due_date else None,
                "overdue_followup": overdue_followup,
                "issue_summary": report.issue_summary,
                "evidence": report.evidence,
                "evidence_items": evidence_items,
                "policy_id": policy.id if policy else None,
                "policy_version": policy.version if policy else None,
                "missing_policy_evidence": missing_evidence,
                "source_system": report.source_system,
                "source_ref": report.source_ref,
                "reported_by": report.reported_by,
                "followed_by": report.followed_by,
            }
        )
    return rows


def _material_handoffs(db, user, project_id, allowed_tools):
    if not _can_read_kind("full_outsource_contract", allowed_tools):
        return []
    if not access(db, user, "full_outsource_contract.read", {"project_id": project_id, "category": "outsource"}).allowed:
        return []
    rows = []
    q = (
        select(m.SupplierMaterialHandoff, m.Supplier)
        .join(m.Supplier, m.SupplierMaterialHandoff.supplier_id == m.Supplier.id)
        .where(m.SupplierMaterialHandoff.project_id == project_id)
        .order_by(m.SupplierMaterialHandoff.provided_date.desc(), m.SupplierMaterialHandoff.created_at.desc(), m.SupplierMaterialHandoff.id)
        .limit(100)
    )
    for handoff, supplier in db.execute(q):
        rows.append(
            {
                "id": handoff.id,
                "supplier_id": supplier.id,
                "supplier_name": supplier.name,
                "contract_subject_id": handoff.contract_subject_id,
                "file_id": handoff.file_id,
                "document_title": handoff.document_title,
                "document_type": handoff.document_type,
                "approval_status": handoff.approval_status,
                "provided_date": handoff.provided_date.isoformat(),
                "provided_to": handoff.provided_to,
                "handoff_channel": handoff.handoff_channel,
                "evidence": handoff.evidence,
                "source_system": handoff.source_system,
                "source_ref": handoff.source_ref,
                "provided_by": handoff.provided_by,
                "verified_by": handoff.verified_by,
            }
        )
    return rows


def _material_verifications(db, user, project_id, allowed_tools):
    if not _can_read_kind("full_outsource_contract", allowed_tools):
        return []
    if not access(db, user, "full_outsource_contract.read", {"project_id": project_id, "category": "outsource"}).allowed:
        return []
    rows = []
    seen_handoffs = set()
    q = (
        select(m.SupplierMaterialVerification, m.SupplierMaterialHandoff, m.Supplier)
        .join(m.SupplierMaterialHandoff, m.SupplierMaterialVerification.handoff_id == m.SupplierMaterialHandoff.id)
        .join(m.Supplier, m.SupplierMaterialVerification.supplier_id == m.Supplier.id)
        .where(m.SupplierMaterialVerification.project_id == project_id)
        .order_by(
            m.SupplierMaterialVerification.handoff_id,
            m.SupplierMaterialVerification.response_date.desc(),
            m.SupplierMaterialVerification.created_at.desc(),
            m.SupplierMaterialVerification.id,
        )
        .limit(100)
    )
    for verification, handoff, supplier in db.execute(q):
        is_latest = handoff.id not in seen_handoffs
        seen_handoffs.add(handoff.id)
        rows.append(
            {
                "id": verification.id,
                "project_id": verification.project_id,
                "supplier_id": supplier.id,
                "supplier_name": supplier.name,
                "contract_subject_id": verification.contract_subject_id,
                "handoff_id": handoff.id,
                "document_title": handoff.document_title,
                "document_type": handoff.document_type,
                "response_file_id": verification.response_file_id,
                "response_date": verification.response_date.isoformat(),
                "result": verification.result,
                "supplier_contact": verification.supplier_contact,
                "response_channel": verification.response_channel,
                "response_summary": verification.response_summary,
                "follow_up_due_date": verification.follow_up_due_date.isoformat() if verification.follow_up_due_date else None,
                "evidence": verification.evidence,
                "source_system": verification.source_system,
                "source_ref": verification.source_ref,
                "recorded_by": verification.recorded_by,
                "is_latest_for_handoff": is_latest,
            }
        )
    return rows


def _deduction_settlements(db, user, project_id, allowed_tools):
    if not _can_read_kind("full_outsource_contract", allowed_tools):
        return []
    if not access(db, user, "full_outsource_contract.read", {"project_id": project_id, "category": "outsource"}).allowed:
        return []
    rows = []
    q = (
        select(m.SupplierDeductionSettlement, m.Supplier)
        .join(m.Supplier, m.SupplierDeductionSettlement.supplier_id == m.Supplier.id)
        .where(m.SupplierDeductionSettlement.project_id == project_id)
        .order_by(m.SupplierDeductionSettlement.created_at.desc(), m.SupplierDeductionSettlement.id)
        .limit(100)
    )
    for settlement, supplier in db.execute(q):
        rows.append(
            {
                "id": settlement.id,
                "supplier_id": supplier.id,
                "supplier_name": supplier.name,
                "contract_subject_id": settlement.contract_subject_id,
                "contact_case_id": settlement.contact_case_id,
                "contact_task_id": settlement.contact_task_id,
                "reason": settlement.reason,
                "responsibility": settlement.responsibility,
                "deduction_amount": str(settlement.deduction_amount),
                "currency": settlement.currency,
                "status": settlement.status,
                "settlement_reference": settlement.settlement_reference,
                "responsibility_evidence": settlement.responsibility_evidence,
                "settlement_evidence": settlement.settlement_evidence,
                "confirmed_by": settlement.confirmed_by,
                "settled_by": settlement.settled_by,
                "settled_at": settlement.settled_at.isoformat() if settlement.settled_at else None,
                "source_system": settlement.source_system,
                "source_ref": settlement.source_ref,
            }
        )
    return rows


def _change_negotiations(db, user, project_id, allowed_tools):
    if not _can_read_kind("full_outsource_contract", allowed_tools):
        return []
    if not access(db, user, "full_outsource_contract.read", {"project_id": project_id, "category": "outsource"}).allowed:
        return []
    rows = []
    q = (
        select(m.OutsourceChangeNegotiation, m.Supplier)
        .join(m.Supplier, m.OutsourceChangeNegotiation.supplier_id == m.Supplier.id)
        .where(m.OutsourceChangeNegotiation.project_id == project_id)
        .order_by(m.OutsourceChangeNegotiation.created_at.desc(), m.OutsourceChangeNegotiation.id)
        .limit(100)
    )
    for negotiation, supplier in db.execute(q):
        rows.append(
            {
                "id": negotiation.id,
                "supplier_id": supplier.id,
                "supplier_name": supplier.name,
                "contract_subject_id": negotiation.contract_subject_id,
                "contact_case_id": negotiation.contact_case_id,
                "contact_task_id": negotiation.contact_task_id,
                "customer_quote_amount": str(negotiation.customer_quote_amount) if negotiation.customer_quote_amount is not None else None,
                "supplier_quote_amount": str(negotiation.supplier_quote_amount) if negotiation.supplier_quote_amount is not None else None,
                "negotiated_amount": str(negotiation.negotiated_amount) if negotiation.negotiated_amount is not None else None,
                "currency": negotiation.currency,
                "schedule_impact_days": negotiation.schedule_impact_days,
                "task_impact_summary": negotiation.task_impact_summary,
                "requires_contract_change": negotiation.requires_contract_change,
                "status": negotiation.status,
                "customer_evidence_present": bool(negotiation.customer_evidence),
                "supplier_evidence_present": bool(negotiation.supplier_evidence),
                "negotiation_evidence": negotiation.negotiation_evidence,
                "approved_by": negotiation.approved_by,
                "source_system": negotiation.source_system,
                "source_ref": negotiation.source_ref,
            }
        )
    return rows


def _engineering_changes(rows):
    result = []
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        text = " ".join(str(detail.get(key) or "") for key in ("problem", "solution"))
        impacts = detail.get("impacts") or []
        if any(keyword in text for keyword in OUTSOURCE_KEYWORDS + ISSUE_KEYWORDS) or any(
            impact.get("action") in {"REWORK", "PAUSE", "CANCEL"} for impact in impacts
        ):
            result.append(
                {
                    "id": row.get("id"),
                    "number": row.get("number"),
                    "status": row.get("status"),
                    "problem": detail.get("problem"),
                    "solution": detail.get("solution"),
                    "customer_due_affected": detail.get("customer_due_affected"),
                    "customer_evidence_present": bool(detail.get("customer_evidence")),
                    "impact_count": len(impacts),
                    "unimplemented_impacts": [
                        {
                            "task_id": impact.get("task_id"),
                            "action": impact.get("action"),
                            "implemented": bool(impact.get("implemented_by") and impact.get("implementation_evidence")),
                            "rechecked": impact.get("recheck_passed"),
                        }
                        for impact in impacts
                        if not impact.get("implemented_by") or impact.get("recheck_passed") is not True
                    ],
                }
            )
    return result[:50]


def _contact_issues(db, user, project_id, allowed_tools):
    if "query_contact_cases" not in allowed_tools:
        return []
    from domain_packs.mold.erp.change.contacts import permitted

    issues = []
    q = (
        select(m.ContactCase)
        .where(m.ContactCase.project_id == project_id, predicate(db, user, "contact.read", {"project_id": m.ContactCase.project_id, "category": m.ContactCase.category}))
        .order_by(m.ContactCase.created_at.desc(), m.ContactCase.id)
        .limit(100)
    )
    for case in db.scalars(q):
        if not permitted(db, user, "read", case):
            continue
        tasks = []
        for task in db.scalars(select(m.ContactTask).where(m.ContactTask.case_id == case.id, m.ContactTask.status != "CANCELLED").limit(100)):
            task_text = " ".join(str(getattr(task, key) or "") for key in ("title", "affected_type", "affected_ref", "impact_description", "planned_action"))
            if any(keyword in task_text for keyword in OUTSOURCE_KEYWORDS + ISSUE_KEYWORDS):
                tasks.append(
                    {
                        "id": task.id,
                        "title": task.title,
                        "status": task.status,
                        "affected_type": task.affected_type,
                        "affected_ref": task.affected_ref,
                        "planned_action": task.planned_action,
                        "delivery_impact_days": task.delivery_impact_days,
                        "estimated_amount": str(task.estimated_amount) if task.estimated_amount is not None else None,
                        "currency": task.currency,
                        "actual_completed_at": task.actual_completed_at.isoformat() if task.actual_completed_at else None,
                        "actual_amount": str(task.actual_amount) if task.actual_amount is not None else None,
                        "actual_currency": task.actual_currency,
                        "execution_evidence_present": bool(task.execution_evidence),
                    }
                )
        case_text = " ".join(str(getattr(case, key) or "") for key in ("title", "problem_source", "current_stage", "change_type"))
        if tasks or any(keyword in case_text for keyword in OUTSOURCE_KEYWORDS + ISSUE_KEYWORDS) or case.problem_source == "OUTSOURCE_DEFECT":
            issues.append(
                {
                    "id": case.id,
                    "title": case.title,
                    "collaboration_status": "CLOSED" if case.closed_at else "HISTORY_RECORD" if case.mode == "HISTORY" else "OPEN",
                    "problem_source": case.problem_source,
                    "current_stage": case.current_stage,
                    "change_type": case.change_type,
                    "urgency": case.urgency,
                    "tasks": tasks[:30],
                }
            )
    return issues[:30]


def _closure_items(db, user, project_id, allowed_tools):
    if "query_project_closure_context" not in allowed_tools and "query_full_outsource_context" not in allowed_tools:
        return []
    if not access(db, user, "project_close.read", {"project_id": project_id}).allowed:
        return []
    rows = []
    for case in db.scalars(
        select(m.ProjectClosureCase)
        .where(m.ProjectClosureCase.project_id == project_id)
        .order_by(m.ProjectClosureCase.created_at.desc(), m.ProjectClosureCase.id)
        .limit(20)
    ):
        for item in db.scalars(select(m.ProjectClosureItem).where(m.ProjectClosureItem.case_id == case.id).limit(100)):
            text = (item.item_key or "") + (item.label or "") + (item.result or "")
            if any(keyword in text for keyword in OUTSOURCE_KEYWORDS + ISSUE_KEYWORDS + ("回款", "付款", "关闭")):
                rows.append(
                    {
                        "case_id": case.id,
                        "mode": case.mode,
                        "case_status": case.status,
                        "item_key": item.item_key,
                        "label": item.label,
                        "status": item.status,
                        "result": item.result,
                        "evidence_present": bool(item.evidence),
                        "source_system": item.source_system,
                    }
                )
    return rows[:100]


def _customer_delivery_acceptance(db, user, project_id, allowed_tools):
    signatures = []
    for row in db.scalars(
        select(m.CustomerDeliverySignature)
        .where(m.CustomerDeliverySignature.project_id == project_id)
        .order_by(m.CustomerDeliverySignature.signed_date.desc(), m.CustomerDeliverySignature.created_at.desc(), m.CustomerDeliverySignature.id)
        .limit(50)
    ):
        signatures.append(
            {
                "id": row.id,
                "logistics_route_id": row.logistics_route_id,
                "shipment_reference": row.shipment_reference,
                "signed_date": row.signed_date.isoformat(),
                "signer_name": row.signer_name,
                "sign_status": row.sign_status,
                "move_type": row.move_type,
                "evidence": row.evidence,
                "recorded_by": row.recorded_by,
            }
        )

    can_read_acceptance = "query_project_closure_context" in allowed_tools and access(db, user, "project_close.read", {"project_id": project_id}).allowed
    acceptance_records = []
    if can_read_acceptance:
        for row in db.scalars(
            select(m.CustomerAcceptanceRecord)
            .where(m.CustomerAcceptanceRecord.project_id == project_id)
            .order_by(m.CustomerAcceptanceRecord.accepted_date.desc(), m.CustomerAcceptanceRecord.created_at.desc(), m.CustomerAcceptanceRecord.id)
            .limit(50)
        ):
            acceptance_records.append(
                {
                    "id": row.id,
                    "signature_id": row.signature_id,
                    "acceptance_type": row.acceptance_type,
                    "result": row.result,
                    "accepted_date": row.accepted_date.isoformat(),
                    "issue_description": row.issue_description,
                    "responsibility": row.responsibility,
                    "corrective_due_date": row.corrective_due_date.isoformat() if row.corrective_due_date else None,
                    "contact_case_id": row.contact_case_id,
                    "supplier_id": row.supplier_id,
                    "deduction_amount": str(row.deduction_amount) if row.deduction_amount is not None else None,
                    "currency": row.currency,
                    "schedule_impact_days": row.schedule_impact_days,
                    "contract_change_required": row.contract_change_required,
                    "evidence": row.evidence,
                    "confirmed_by": row.confirmed_by,
                }
            )

    signed = [row for row in signatures if row["sign_status"] == "SIGNED"]
    passed = [row for row in acceptance_records if row["result"] in {"PASSED", "CONDITIONALLY_PASSED"}]
    failed = [row for row in acceptance_records if row["result"] == "FAILED"]
    rechecks = [row for row in acceptance_records if row["acceptance_type"] == "RECHECK"]
    recheck_passed = [row for row in rechecks if row["result"] in {"PASSED", "CONDITIONALLY_PASSED"}]
    deductions = [row for row in acceptance_records if row["deduction_amount"] is not None]
    return {
        "signatures": signatures,
        "acceptance_records": acceptance_records,
        "visibility": {
            "signature_records_visible": True,
            "acceptance_records_visible": can_read_acceptance,
        },
        "derived_status": {
            "has_customer_signature": bool(signed),
            "has_customer_acceptance": bool(passed),
            "has_failed_customer_acceptance": bool(failed),
            "has_recheck_record": bool(rechecks),
            "has_recheck_passed": bool(recheck_passed),
            "has_acceptance_deduction": bool(deductions),
            "has_contract_change_required": any(row["contract_change_required"] for row in acceptance_records),
            "schedule_impact_days_total": sum(int(row["schedule_impact_days"] or 0) for row in acceptance_records),
        },
    }


def _analysis(project, profile, quote_acceptance, contracts, signing_records, active_plan, plan_tasks, supplier_progress_policies, supplier_progress_reports, material_handoffs, material_verifications, deduction_settlements, change_negotiations, order_tracking, engineering_changes, contacts, payments, closure_items, customer_delivery_acceptance):
    contract_effective = [row for row in contracts if row.get("status") == "EFFECTIVE"]
    signed_contracts = [row for row in signing_records if row.get("status") == "SIGNED"]
    non_signed_contracts = [row for row in signing_records if row.get("status") != "SIGNED"]
    totals = order_tracking["totals"]
    open_contacts = [row for row in contacts if row.get("collaboration_status") != "CLOSED"]
    open_change_impacts = [impact for row in engineering_changes for impact in row.get("unimplemented_impacts") or []]
    risky_reports = [row for row in supplier_progress_reports if row.get("status") in {"AT_RISK", "BLOCKED", "REWORK"}]
    overdue_reports = [row for row in supplier_progress_reports if row.get("overdue_followup")]
    overdue_policies = [row for row in supplier_progress_policies if row.get("overdue")]
    evidence_noncompliant = [row for row in supplier_progress_policies if row.get("latest_report_id") and row.get("latest_missing_evidence")]
    approved_handoffs = [row for row in material_handoffs if row.get("approval_status") == "APPROVED"]
    draft_or_revoked_handoffs = [row for row in material_handoffs if row.get("approval_status") != "APPROVED"]
    latest_material_verifications = [row for row in material_verifications if row.get("is_latest_for_handoff")]
    accepted_material_verifications = [row for row in latest_material_verifications if row.get("result") == "ACCEPTED"]
    received_only_material_verifications = [row for row in latest_material_verifications if row.get("result") == "RECEIVED"]
    open_material_verifications = [row for row in latest_material_verifications if row.get("result") in {"NEEDS_CLARIFICATION", "REJECTED"}]
    accepted_handoff_ids = {row["handoff_id"] for row in accepted_material_verifications}
    unverified_handoffs = [row for row in approved_handoffs if row.get("id") not in accepted_handoff_ids]
    confirmed_deductions = [row for row in deduction_settlements if row.get("responsibility") != "UNKNOWN" and row.get("status") in {"RESPONSIBILITY_CONFIRMED", "SETTLED"}]
    settled_deductions = [row for row in deduction_settlements if row.get("status") == "SETTLED"]
    pending_deductions = [row for row in deduction_settlements if row.get("status") == "PROPOSED" or row.get("responsibility") == "UNKNOWN"]
    approved_change_negotiations = [row for row in change_negotiations if row.get("status") == "APPROVED"]
    open_change_negotiations = [row for row in change_negotiations if row.get("status") in {"DRAFT", "NEGOTIATING", "AGREED"}]
    contract_change_negotiations = [row for row in change_negotiations if row.get("requires_contract_change")]
    deduction_tasks = [
        task
        for issue in contacts
        for task in issue.get("tasks") or []
        if task.get("estimated_amount") or task.get("actual_amount") or any(keyword in (task.get("title") or task.get("impact_description") or "") for keyword in ("扣款", "索赔"))
    ]
    acceptance_done = [item for item in closure_items if "ACCEPTANCE" in item.get("item_key", "") and item.get("status") == "DONE"]
    close_done = [item for item in closure_items if item.get("status") == "DONE" and any(keyword in item.get("item_key", "") for keyword in ("CLOSE", "SETTLEMENT", "PAYMENT"))]
    customer_status = customer_delivery_acceptance["derived_status"]
    has_customer_acceptance = bool(acceptance_done) or customer_status["has_customer_acceptance"]

    gaps = []
    warnings = []
    mode = (profile or {}).get("execution_mode")
    if mode != "FULL_OUTSOURCE" and not quote_acceptance:
        gaps.append("未见项目档案或有效承接记录明确当前为整套委外。")
    if mode == "FULL_OUTSOURCE" and not quote_acceptance:
        warnings.append("项目档案为整套委外，但未见有效承接/后续审批中明确整套委外路径的证据。")
    if quote_acceptance and mode and mode != "FULL_OUTSOURCE":
        warnings.append("有效承接为整套委外，但项目档案加工方式不是整套委外，需核对是否已有后续变更依据。")
    if not contract_effective:
        gaps.append("未见已生效整套委外合同；不能把合同草稿或报价委外金额当成合同已签署。")
    if contract_effective and not signed_contracts:
        gaps.append("未见整套委外合同的人工签署文件或签署依据；不能把模板草稿、合同号或审批上下文等同于已签署合同。")
    if non_signed_contracts:
        warnings.append("存在非已签署状态的合同签署记录，不能作为正式合同签署依据。")
    if contract_effective and not approved_handoffs:
        gaps.append("未见按合同或业务需要向供应商提供获准客户资料/设计资料的交接依据。")
    if draft_or_revoked_handoffs:
        warnings.append("存在草稿或已撤回的供应商资料交接记录，不能作为正式获准交接依据。")
    if approved_handoffs and unverified_handoffs:
        gaps.append("存在已批准并交接的供应商资料，但未见供应商对全部资料完成接受核验；已收到不能替代已接受。")
    if received_only_material_verifications:
        warnings.append("存在供应商仅确认收到、尚未确认接受的资料交接，需继续核验版本和适用性。")
    if open_material_verifications:
        warnings.append("存在供应商要求澄清或退回的资料，需按最新回复补充或更正后重新交接并核验。")
    if not active_plan:
        warnings.append("当前可见范围未见有效项目计划，无法核对供应商节点上报与项目同步节奏。")
    if not plan_tasks:
        gaps.append("未见供应商设计、采购、生产、质检、装配、试模、验收或交付等委外协同计划节点。")
    if plan_tasks and not supplier_progress_policies:
        gaps.append("未见版本化供应商节点上报频率与必需证据规则；无法判断各阶段应报日期和证据完整性。")
    if not supplier_progress_reports:
        gaps.append("未见结构化供应商节点上报/导入记录；无法核对供应商设计、采购、生产、质检、装配、试模、验收等阶段的最近进度与证据。")
    if risky_reports:
        warnings.append("存在供应商节点风险、阻塞或返工上报，需采购跟进并同步项目。")
    if overdue_reports:
        warnings.append("存在供应商风险/阻塞节点已超过下次跟进日期，需更新整改或复验进度。")
    if overdue_policies:
        warnings.append("存在供应商节点超过规则计算的应报日期，需按当前有效频率规则补充上报。")
    if evidence_noncompliant:
        warnings.append("存在历史供应商节点上报未满足当前规则的必需证据类型，需补充证据或登记新上报。")
    if not totals.get("supplier_shipments") and not totals.get("goods_receipts"):
        gaps.append("未见供应商发货、仓库收货或交付节点执行事实；不能据此认定委外交付完成。")
    if totals.get("supplier_shipments") and not totals.get("goods_receipts"):
        gaps.append("已有供应商发货记录，但未见我方收货/签收依据。")
    if totals.get("rejected_receipt_lines"):
        warnings.append("存在收货检验不合格数量，需核对整改、退换货、复验和扣款责任依据。")
    if open_contacts:
        warnings.append("存在未关闭委外质量、延期、验收或扣款相关工程联络事项，不能认定异常已闭环。")
    if open_change_impacts:
        warnings.append("存在设变或整改影响项未见执行与复验全部完成，不能把方案批准等同于整改完成。")
    if open_change_impacts and not approved_change_negotiations:
        warnings.append("存在委外设变或整改影响项，但未见已批准的设变议价记录；新增费用、交期和任务影响仍需采购、项目和供应商确认。")
    if open_change_negotiations:
        warnings.append("存在草稿、议价中或仅达成未审批的委外设变议价记录，不能作为已落实的费用或交期变更。")
    if contract_change_negotiations and not any(row.get("status") == "APPROVED" for row in contract_change_negotiations):
        warnings.append("存在要求合同变化的委外设变议价记录，但未见已审批结果；不能自动追加或变更合同。")
    if deduction_tasks and not contract_effective:
        warnings.append("存在扣款/费用影响线索，但未见已生效委外合同，不能确认责任与结算依据。")
    if deduction_tasks and not confirmed_deductions:
        warnings.append("存在扣款/费用影响线索，但未见责任已确认的供应商扣款结算依据；不能仅凭延期或质量问题自动认定供应商扣款。")
    if confirmed_deductions and not settled_deductions:
        warnings.append("存在责任已确认的供应商扣款，但未见已结算记录；需同步供应商结算或财务依据。")
    if pending_deductions:
        warnings.append("存在待确认责任或拟议状态的供应商扣款记录，不能作为正式结算结果。")
    if not customer_status["has_customer_signature"]:
        gaps.append("未见客户签收记录；不能用供应商发货、我方收货或库存移动推断客户已签收。")
    if customer_status["has_customer_signature"] and not has_customer_acceptance:
        gaps.append("已有客户签收记录，但未见客户质量验收通过或有条件通过依据；客户签收不等于客户验收。")
    if not has_customer_acceptance:
        gaps.append("未见委外项目客户验收完成、回款或关闭清单中的正式依据。")
    if customer_status["has_failed_customer_acceptance"] and not customer_status["has_recheck_passed"]:
        warnings.append("存在客户验收未通过记录，未见复验通过；不能认定委外交付闭环或进入关闭。")
    if customer_status["has_acceptance_deduction"] and not settled_deductions:
        warnings.append("客户验收记录涉及扣款，但未见已结算供应商扣款；需核对客户扣款与供应商结算联动。")
    if customer_status["has_acceptance_deduction"] and settled_deductions:
        warnings.append("客户验收记录涉及扣款，已见供应商扣款结算依据；仍需核对客户对我方扣款与我方对供应商扣款金额、币种和责任一致性。")
    if customer_status["has_contract_change_required"]:
        warnings.append("客户验收记录要求合同变化，需同步合同变更或客户确认材料，不能只以验收记录自动变更合同。")
    if customer_status["schedule_impact_days_total"]:
        warnings.append("客户验收记录存在交期影响天数，需与计划变更或客户交期确认联动。")
    if has_customer_acceptance and payments["requests"] and not close_done:
        warnings.append("已有客户验收或供应商付款申请，但未见关闭/结算清单完成；不能把付款申请等同于项目关闭。")
    gaps.append("当前未接入供应商门户和供应商在线签署；规则与上报仅代表已授权本地/导入事实，不代表 ERP 执行节点完成。")

    return {
        "latest_full_outsource_acceptance": quote_acceptance,
        "contract_signing_records": signing_records,
        "active_plan": (
            {"id": active_plan.get("id"), "number": active_plan.get("number"), "status": active_plan.get("status"), "created_at": active_plan.get("created_at")}
            if active_plan
            else None
        ),
        "outsource_plan_tasks": plan_tasks,
        "supplier_progress_policies": supplier_progress_policies,
        "supplier_progress_reports": supplier_progress_reports,
        "supplier_material_handoffs": material_handoffs,
        "supplier_material_verifications": material_verifications,
        "supplier_deduction_settlements": deduction_settlements,
        "outsource_change_negotiations": change_negotiations,
        "supplier_execution_tracking": order_tracking,
        "engineering_changes": engineering_changes,
        "outsource_quality_delay_contacts": contacts,
        "supplier_payment_summary": payments,
        "closure_and_settlement_items": closure_items,
        "customer_delivery_acceptance": customer_delivery_acceptance,
        "gaps": gaps,
        "warnings": warnings,
        "derived_status": {
            "project_status": project.status,
            "profile_execution_mode": mode,
            "has_full_outsource_mode": mode == "FULL_OUTSOURCE" or bool(quote_acceptance),
            "has_effective_full_outsource_contract": bool(contract_effective),
            "has_signed_full_outsource_contract_file": bool(signed_contracts),
            "has_unsigned_contract_signing_record": bool(non_signed_contracts),
            "has_outsource_plan_node": bool(plan_tasks),
            "has_supplier_progress_policy": bool(supplier_progress_policies),
            "has_overdue_supplier_progress_report": bool(overdue_policies),
            "has_supplier_progress_evidence_gap": bool(evidence_noncompliant),
            "has_supplier_progress_report": bool(supplier_progress_reports),
            "has_supplier_progress_risk": bool(risky_reports),
            "has_overdue_supplier_progress_followup": bool(overdue_reports),
            "has_approved_supplier_material_handoff": bool(approved_handoffs),
            "has_draft_or_revoked_supplier_material_handoff": bool(draft_or_revoked_handoffs),
            "has_supplier_material_acceptance": bool(accepted_material_verifications),
            "has_unverified_supplier_material_handoff": bool(unverified_handoffs),
            "has_supplier_material_received_only": bool(received_only_material_verifications),
            "has_open_supplier_material_clarification": bool(open_material_verifications),
            "has_confirmed_supplier_deduction": bool(confirmed_deductions),
            "has_settled_supplier_deduction": bool(settled_deductions),
            "has_pending_supplier_deduction": bool(pending_deductions),
            "has_approved_outsource_change_negotiation": bool(approved_change_negotiations),
            "has_open_outsource_change_negotiation": bool(open_change_negotiations),
            "has_contract_change_negotiation": bool(contract_change_negotiations),
            "has_supplier_shipment_or_receipt": bool(totals.get("supplier_shipments") or totals.get("goods_receipts")),
            "has_rejected_receipt": bool(totals.get("rejected_receipt_lines")),
            "has_open_outsource_issue": bool(open_contacts or open_change_impacts),
            "has_deduction_or_cost_impact_signal": bool(deduction_tasks),
            "has_supplier_payment_request": bool(payments["requests"]),
            "has_customer_signature": customer_status["has_customer_signature"],
            "has_customer_acceptance_record": customer_status["has_customer_acceptance"],
            "has_failed_customer_acceptance": customer_status["has_failed_customer_acceptance"],
            "has_customer_recheck_passed": customer_status["has_recheck_passed"],
            "has_customer_acceptance_deduction": customer_status["has_acceptance_deduction"],
            "has_customer_acceptance_contract_change": customer_status["has_contract_change_required"],
            "has_customer_acceptance_or_close_evidence": has_customer_acceptance,
        },
    }


def query(db, user, data: ProjectPlanContextInput, allowed_tools: set[str]):
    project, alternatives, truncated = _resolve(db, user, data, allowed_tools)
    limitations = [
        "只读取当前用户可见项目；承接、合同、计划、订单、工程联络、付款和结项材料分别受对应工具与权限约束。",
        "本工具只核对整套委外协同上下文，不创建供应商门户、不生成或签署合同、不下达委外、不登记扣款、不确认付款或客户验收。",
        "报价委外金额、整套委外合同、供应商节点上报、我方收货、客户验收、扣款和结算是不同事实，不能相互替代。",
    ]
    if truncated:
        limitations.append("最多检查前500个可见项目，结果可能未覆盖全部可见范围。")
    if project:
        profile = _profile(db, user, project.id)
        quote_rows = _subject_rows(db, user, project.id, "quote_acceptance", allowed_tools)
        contract_rows = _subject_rows(db, user, project.id, "full_outsource_contract", allowed_tools)
        signing_records = _contract_signing_records(db, user, contract_rows, allowed_tools)
        plan_rows = _subject_rows(db, user, project.id, "project_plan", allowed_tools) + _subject_rows(db, user, project.id, "plan_change", allowed_tools)
        active_plan, plan_tasks = _plan_tasks(plan_rows)
        supplier_progress_policies = _supplier_progress_policies(db, user, project.id, allowed_tools)
        supplier_progress_reports = _supplier_progress_reports(db, user, project.id, allowed_tools)
        material_handoffs = _material_handoffs(db, user, project.id, allowed_tools)
        material_verifications = _material_verifications(db, user, project.id, allowed_tools)
        deduction_settlements = _deduction_settlements(db, user, project.id, allowed_tools)
        change_negotiations = _change_negotiations(db, user, project.id, allowed_tools)
        engineering_changes = _engineering_changes(_subject_rows(db, user, project.id, "engineering_change", allowed_tools))
        contacts = _contact_issues(db, user, project.id, allowed_tools)
        payments = _payment_summary(_subject_rows(db, user, project.id, "supplier_payment", allowed_tools))
        closure_items = _closure_items(db, user, project.id, allowed_tools)
        customer_delivery_acceptance = _customer_delivery_acceptance(db, user, project.id, allowed_tools)
        order_tracking = _order_tracking(db, user, project.id, allowed_tools)
        skipped = []
        for kind, label in (
            ("quote_acceptance", "报价承接/加工方式"),
            ("full_outsource_contract", "整套委外合同"),
            ("project_plan", "项目计划/委外节点"),
            ("engineering_change", "设变/整改业务记录"),
            ("supplier_payment", "供应商付款/结算"),
        ):
            if not _can_read_kind(kind, allowed_tools):
                skipped.append(label)
        if "query_purchase_orders" not in allowed_tools and "query_orders" not in allowed_tools:
            skipped.append("正式订单/供应商发货/收货")
        if "query_contact_cases" not in allowed_tools:
            skipped.append("工程联络质量延期扣款事项")
        if "query_project_closure_context" not in allowed_tools:
            skipped.append("项目关闭/客户验收清单")
        if not customer_delivery_acceptance["visibility"]["acceptance_records_visible"]:
            skipped.append("客户验收/复验/扣款记录")
        if skipped:
            limitations.append("未分配对应查询工具或权限，未返回：" + "、".join(skipped))
        return {
            "resolution": "RESOLVED",
            "data": [
                {
                    "project": _project_card(db, user, project, alternatives or ("项目定位",)),
                    "profile": profile,
                    "full_outsource_contracts": _contract_summary(contract_rows),
                    "analysis": _analysis(
                        project,
                        profile,
                        _latest_outsource_acceptance(quote_rows),
                        contract_rows,
                        signing_records,
                        active_plan,
                        plan_tasks,
                        supplier_progress_policies,
                        supplier_progress_reports,
                        material_handoffs,
                        material_verifications,
                        deduction_settlements,
                        change_negotiations,
                        order_tracking,
                        engineering_changes,
                        contacts,
                        payments,
                        closure_items,
                        customer_delivery_acceptance,
                    ),
                }
            ],
            "source": "agent_db",
            "as_of": now().isoformat(),
            "limitations": limitations,
        }
    if alternatives is None:
        return {"resolution": "NOT_FOUND_OR_FORBIDDEN", "data": [], "source": "agent_db", "as_of": now().isoformat(), "limitations": limitations}
    if alternatives:
        return {
            "resolution": "MULTIPLE_CANDIDATES",
            "data": alternatives,
            "source": "agent_db",
            "as_of": now().isoformat(),
            "limitations": limitations + ["线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。"],
        }
    return {"resolution": "NOT_FOUND", "data": [], "source": "agent_db", "as_of": now().isoformat(), "limitations": limitations}


def source(db, user, step_id):
    from domain_packs.mold.tool_gateway import available_tools
    step = db.get(m.Step, step_id)
    run = db.get(m.Run, step.run_id) if step else None
    if not run or run.user_id != user.id:
        raise DomainError("NOT_FOUND", "操作建议不存在或无权访问", 404)
    if run.status not in {"RUNNING", "SUCCEEDED"}:
        raise DomainError("PROPOSAL_STOPPED", "任务已停止，请重新准备操作", 409)
    if run.security_version != user.security_version or run.checkpoint.get("authorization_hash") != fingerprint(db, user):
        raise DomainError("AUTHORIZATION_CHANGED", "授权已变化，请重新准备操作", 403)
    proposal = step.result.get("proposal")
    if step.tool not in available_tools(db, user) or step.tool not in FULL_OUTSOURCE_PROPOSAL_TOOLS or not proposal:
        raise DomainError("TOOL_FORBIDDEN", "操作能力不可用", 403)
    return proposal


def validate_intent(db, user, payload):
    proposal = source(db, user, payload["step_id"])
    if content_hash(proposal) != payload["proposal_hash"]:
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    kind = proposal.get("kind")
    if kind == "supplier_material_handoff":
        data = parse_supplier_material_handoff(proposal["input"])
        _, _, _, display = preview_supplier_material_handoff(db, user, data)
    elif kind == "supplier_material_verification":
        data = parse_supplier_material_verification(proposal["input"])
        _, _, _, _, display = preview_supplier_material_verification(db, user, data)
    elif kind == "supplier_progress_policy":
        data = parse_supplier_progress_policy(proposal["input"])
        _, _, _, _, _, _, display = preview_supplier_progress_policy(db, user, data)
    elif kind == "supplier_progress_report":
        data = parse_supplier_progress_report(proposal["input"])
        _, _, _, _, _, display = preview_supplier_progress_report(db, user, data)
    else:
        raise DomainError("TOOL_FORBIDDEN", "操作建议类型不可用", 403)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "项目、合同、供应商或权限资料已变化，请重新准备", 409)
    return proposal, data


def confirm(db, user, payload):
    proposal, data = validate_intent(db, user, payload)
    if proposal["kind"] == "supplier_material_handoff":
        row = create_supplier_material_handoff(db, user, data)
        return {"project_id": data.project_id, "supplier_id": data.supplier_id,
            "supplier_material_handoff_id": row.id, "action": "supplier_material_handoff", "status": "CONFIRMED"}
    if proposal["kind"] == "supplier_material_verification":
        row = create_supplier_material_verification(db, user, data)
        return {"project_id": data.project_id, "supplier_id": data.supplier_id,
            "supplier_material_verification_id": row.id, "supplier_material_handoff_id": row.handoff_id,
            "action": "supplier_material_verification", "status": "CONFIRMED"}
    if proposal["kind"] == "supplier_progress_policy":
        row = create_supplier_progress_policy(db, user, data)
        return {"project_id": data.project_id, "supplier_id": data.supplier_id,
            "supplier_progress_policy_id": row.id, "version": row.version,
            "action": "supplier_progress_policy", "status": "CONFIRMED"}
    row = create_supplier_progress_report(db, user, data)
    return {"project_id": data.project_id, "supplier_id": data.supplier_id,
        "supplier_progress_report_id": row.id, "action": "supplier_progress_report", "status": "CONFIRMED"}


router = APIRouter()


@router.get("/api/full-outsource-proposals/{step_id}")
def proposal_status(step_id: str, user=Depends(current_user), db=Depends(get_db)):
    source(db, user, step_id)
    intent = db.scalar(select(m.HumanIntent).where(m.HumanIntent.user_id == user.id,
        m.HumanIntent.action == "full_outsource.execute", m.HumanIntent.resource_id == step_id,
        m.HumanIntent.receipt.is_not(None)).order_by(m.HumanIntent.created_at.desc()))
    return {"receipt": intent.receipt if intent else None}


@router.post("/api/full-outsource-proposals/{step_id}/intent")
def intent(step_id: str, user=Depends(current_user), db=Depends(get_db)):
    from domain_packs.mold.erp.core.business import create_intent
    proposal = source(db, user, step_id)
    payload = {"step_id": step_id, "proposal_hash": content_hash(proposal)}
    result = create_intent(db, user, "full_outsource.execute", step_id, payload)
    result["display"] = proposal["display"]
    result["confirmation_policy"] = proposal.get("confirmation_policy")
    db.commit()
    return result
