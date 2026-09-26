"""Agent tools that turn a reviewed OCR intake into an approval proposal."""
from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import Field, ValidationError
from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.authorization import fingerprint, require
from domain_packs.mold.erp.commercial import contract_documents, contract_intake
from domain_packs.mold.erp.core import domain_schemas as s, domains
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel


class ContractIntakeQueryInput(StrictModel):
    contract_intake_group_id: str | None = Field(default=None, min_length=1, max_length=36)


class ContractIntakeProposalInput(StrictModel):
    contract_intake_group_id: str = Field(min_length=1, max_length=36)
    expected_version: int = Field(ge=1)
    workflow_definition_id: str = Field(min_length=1, max_length=36)


def query_schema():
    return ContractIntakeQueryInput.model_json_schema()


def proposal_schema():
    return ContractIntakeProposalInput.model_json_schema()


def _parse(model, arguments):
    try:
        return model.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "合同识别参数无效：" + error.errors()[0]["msg"]) from None


def _files(db, group_id):
    return list(db.execute(
        select(m.DocumentIntakeFile, m.FileObject)
        .join(m.FileObject, m.FileObject.id == m.DocumentIntakeFile.file_id)
        .where(m.DocumentIntakeFile.contract_group_id == group_id)
        .order_by(m.DocumentIntakeFile.created_at, m.DocumentIntakeFile.id)
    ))


def _workflow(db, project_id, definition_id):
    from domain_packs.mold import workflow_assignment

    definition = db.get(m.WorkflowDefinition, definition_id)
    if not definition or definition.status != "PUBLISHED":
        raise DomainError("WORKFLOW_MISMATCH", "销售合同审批流程不可用", 409)
    config = definition.config if isinstance(definition.config, dict) else {}
    nodes = config.get("nodes") if isinstance(config.get("nodes"), list) else []
    expected_roles = ["SALES_SUPERVISOR", "FINANCE_OWNER"]
    if config.get("business_type") != "sales_contract" or len(nodes) != 2:
        raise DomainError("WORKFLOW_MISMATCH", "销售合同必须使用业务主管、财务顺序两级流程", 409)
    for node, expected_role in zip(nodes, expected_roles):
        assignment = node.get("assignment") if isinstance(node, dict) else None
        if (
            not isinstance(assignment, dict)
            or assignment.get("domain_roles") != [expected_role]
            or node.get("users")
            or assignment.get("roles")
            or assignment.get("departments")
        ):
            raise DomainError("WORKFLOW_MISMATCH", "销售合同审批节点角色或顺序不符合要求", 409)
        users, _sources = workflow_assignment.resolve(
            db, [expected_role], {"project_id": project_id}
        )
        if not users:
            raise DomainError("ASSIGNMENT_BLOCKED", f"项目尚未配置{expected_role}人员", 409)
        for user_id in users:
            assignee = db.get(m.User, user_id)
            if not assignee or not assignee.active:
                raise DomainError("ASSIGNMENT_BLOCKED", f"{expected_role}人员不存在或已停用", 409)
            try:
                require(db, assignee, "sales_contract.read", {"project_id": project_id})
                require(db, assignee, "sales_contract.approve", {"project_id": project_id})
            except DomainError:
                raise DomainError("ASSIGNMENT_BLOCKED", f"{expected_role}人员缺少合同读取或审批权限", 409) from None
    return definition


def _ready(db, user, data):
    group = contract_intake.load_group(db, user, data.contract_intake_group_id)
    if group.row_version != data.expected_version:
        raise DomainError("STALE_VERSION", "合同识别记录已变化，请重新查询", 409)
    if group.status != "READY_FOR_DRAFT" or not group.project_id or not group.project_version:
        raise DomainError("CONTRACT_INTAKE_NOT_READY", "合同识别记录尚未完成人工复核", 409)
    project = db.get(m.Project, group.project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    require(db, user, "sales_contract.create", {"project_id": project.id})
    require(db, user, "sales_contract.submit", {"project_id": project.id})
    if project.row_version != group.project_version:
        raise DomainError("PROJECT_VERSION_CHANGED", "项目版本已变化，请重新复核合同", 409)
    fields = contract_intake._group_fields(db, group.id)
    if not fields or any(field.confirmed_value is None for field in fields):
        raise DomainError("FIELD_CONFIRMATION_INCOMPLETE", "合同字段尚未全部人工确认", 409)
    mold_rows = {field.row_key for field in fields if field.scope == "MOLD"}
    matches = list(db.scalars(select(m.ContractIntakeMoldMatch).where(
        m.ContractIntakeMoldMatch.group_id == group.id,
    )))
    if {row.row_key for row in matches} != mold_rows or any(not row.mold_id for row in matches):
        raise DomainError("MOLD_MAPPING_REQUIRED", "合同模具明细尚未全部关联", 409)
    files = _files(db, group.id)
    if not files or any(not blob.sha256 for _intake_file, blob in files):
        raise DomainError("CONTRACT_FILE_INVALID", "合同原始 PDF 不完整", 409)
    definition = _workflow(db, project.id, data.workflow_definition_id)
    return group, project, fields, matches, files, definition


def _value_map(fields):
    return {
        (field.scope, field.row_key, field.field_key): field.confirmed_value.get("value")
        for field in fields
    }


def _decimal(value, label, *, required=False):
    if value in (None, ""):
        if required:
            raise DomainError("CONTRACT_FIELD_INVALID", f"{label}不能为空")
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise DomainError("CONTRACT_FIELD_INVALID", f"{label}不是有效金额") from None
    if result <= 0:
        raise DomainError("CONTRACT_FIELD_INVALID", f"{label}必须大于零")
    return result


def _date(value, label):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise DomainError("CONTRACT_FIELD_INVALID", f"{label}不是有效日期") from None


def _detail(group, fields):
    values = _value_map(fields)
    amount = _decimal(values.get(("HEADER", "header", "amount")), "合同总额", required=True)
    currency = str(values.get(("HEADER", "header", "currency")) or "").upper()
    if len(currency) != 3:
        raise DomainError("CONTRACT_FIELD_INVALID", "合同币种必须是三位代码")
    payment_rows = sorted({field.row_key for field in fields if field.scope == "PAYMENT"})
    stages = []
    for sequence, row_key in enumerate(payment_rows, 1):
        stage_amount = _decimal(values.get(("PAYMENT", row_key, "amount")), "付款节点金额", required=True)
        stages.append(s.StageInput(
            name=str(values.get(("PAYMENT", row_key, "name")) or f"付款节点{sequence}"),
            sequence=sequence,
            amount=stage_amount,
            ratio=_decimal(values.get(("PAYMENT", row_key, "ratio")), "付款比例"),
            condition=str(values.get(("PAYMENT", row_key, "condition")) or "合同原文未识别条件，须由财务确认"),
            term_days=(int(values[("PAYMENT", row_key, "term_days")])
                       if values.get(("PAYMENT", row_key, "term_days")) not in (None, "") else None),
        ))
    return s.ContractInput(
        customer_id=group.customer_id,
        supplier_id=None,
        amount=amount,
        currency=currency,
        contract_number=str(values.get(("HEADER", "header", "contract_number")) or ""),
        signed_date=_date(values.get(("HEADER", "header", "signed_date")), "签订日期"),
        external_order_number=(str(values[("HEADER", "header", "external_order_number")])
                               if values.get(("HEADER", "header", "external_order_number")) else None),
        expected_date=_date(values.get(("HEADER", "header", "due_date")), "合同交期"),
        replaces_id=None,
        stages=stages,
    )


def _preview(db, user, data):
    group, project, fields, matches, files, definition = _ready(db, user, data)
    detail = _detail(group, fields)
    display = {
        "操作": "从人工确认的 OCR 结果登记销售合同",
        "项目": f"{project.code} · {project.name}",
        "项目版本": project.row_version,
        "合同号": detail.contract_number,
        "合同金额": f"{detail.amount} {detail.currency}",
        "原始PDF": [blob.filename for _row, blob in files],
        "模具明细": [row.customer_mold_number or row.row_key for row in matches],
        "付款节点": [f"{row.name}：{row.amount} {detail.currency}" for row in detail.stages],
        "复核提示": group.review_warnings,
        "合同关系": group.relation_type or "NEW",
        "审批流程": f"{definition.name} · 第{definition.version}版",
        "审批顺序": ["业务主管审核", "财务确认"],
        "说明": "本人确认后创建合同草稿并提交两级审批；财务确认前合同不会生效。",
    }
    return group, project, fields, matches, files, detail, display


def execute_tool(db, user, key, arguments, run=None):
    if key == "query_sales_contract_intake":
        data = _parse(ContractIntakeQueryInput, arguments)
        if data.contract_intake_group_id:
            groups = [contract_intake.load_group(db, user, data.contract_intake_group_id)]
        else:
            if not run or run.user_id != user.id:
                raise DomainError("CONTRACT_INTAKE_CONTEXT_REQUIRED", "未指定识别分组时必须绑定当前 Agent Run", 403)
            intakes = contract_intake.list_for_conversation(db, user, run.conversation_id)
            intake_ids = [row.id for row in intakes]
            groups = list(db.scalars(select(m.ContractIntakeGroup).where(
                m.ContractIntakeGroup.intake_id.in_(intake_ids or [""])
            ).order_by(m.ContractIntakeGroup.created_at.desc(), m.ContractIntakeGroup.id.desc())))
        return {
            "data": [contract_intake.serialize_group(db, group) for group in groups],
            "source": "agent_db",
            "as_of": now().isoformat(),
            "limitations": ["仅返回当前用户可见的识别记录，不自动确认项目、模具或合同字段。"],
        }
    if key != "prepare_sales_contract_from_intake":
        raise DomainError("TOOL_UNKNOWN", "工具未实现", 403)
    data = _parse(ContractIntakeProposalInput, arguments)
    _group, _project, _fields, _matches, _files_rows, _detail_row, display = _preview(db, user, data)
    requires_approval = _group.relation_type != "DUPLICATE"
    proposal = {
        "kind": "sales_contract_intake",
        "action": "sales_contract_from_intake",
        "requires_approval": requires_approval,
        "input": data.model_dump(mode="json"),
        "display": display,
        "confirmation_policy": proposal_confirmation_policy(run, requires_approval=requires_approval),
    }
    return {
        "data": [],
        "source": "agent_proposal",
        "as_of": now().isoformat(),
        "proposal": proposal,
        "limitations": ["本人确认后才创建合同并提交审批；业务主管与财务未全部通过前合同不生效。"],
    }


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
    if step.tool != "prepare_sales_contract_from_intake" or step.tool not in available_tools(db, user) or not proposal:
        raise DomainError("TOOL_FORBIDDEN", "操作能力不可用", 403)
    return proposal


def validate_intent(db, user, payload):
    proposal = source(db, user, payload["step_id"])
    if content_hash(proposal) != payload["proposal_hash"]:
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    data = _parse(ContractIntakeProposalInput, proposal["input"])
    *_rows, display = _preview(db, user, data)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "识别结果、项目、文件或流程资料已变化", 409)
    return proposal, data


def confirm(db, user, payload):
    from domain_packs.mold.erp.core.business import submit_subject
    from domain_packs.mold.ports.confirmation_policy import agent_permission_mode_from_proposal
    from domain_packs.mold.ports.events import record

    proposal, data = validate_intent(db, user, payload)
    group, project, fields, matches, files, detail, _display = _preview(db, user, data)
    group = db.scalar(select(m.ContractIntakeGroup).where(
        m.ContractIntakeGroup.id == group.id,
        m.ContractIntakeGroup.status == "READY_FOR_DRAFT",
        m.ContractIntakeGroup.row_version == data.expected_version,
    ).with_for_update())
    if not group:
        raise DomainError("STALE_VERSION", "合同识别记录已被处理", 409)
    if group.relation_type == "DUPLICATE":
        target = db.get(m.BusinessSubject, group.relation_target_contract_id)
        if not target or target.kind != "sales_contract" or target.project_id != project.id:
            raise DomainError("CONTRACT_RELATION_INVALID", "重复合同目标不存在或不属于当前项目", 409)
        group.duplicate_of_contract_id = target.id
        group.status = "CONTRACT_DRAFT_CREATED"
        group.row_version += 1
        record(db, user, "contract.relationship.confirmed", group.id, {
            "relation_type": "DUPLICATE", "target_contract_id": target.id,
        })
        return {
            "project_id": project.id,
            "contract_intake_group_id": group.id,
            "subject_id": target.id,
            "instance_id": None,
            "action": "sales_contract_duplicate_linked",
            "status": "DUPLICATE_LINKED",
        }
    subject = domains.create(db, user, s.SubjectInput(
        kind="sales_contract",
        project_id=project.id,
        category=None,
        remark=f"OCR合同 {detail.contract_number}",
        detail=detail.model_dump(mode="json"),
    ))
    contract_documents.link_intake(db, user, subject, files)
    for line_no, row in enumerate(matches, 1):
        db.add(m.ContractMoldLine(
            contract_subject_id=subject.id,
            line_no=line_no,
            mold_id=row.mold_id,
            customer_mold_number=row.customer_mold_number,
            machine_model=row.machine_model,
            material_number=row.material_number,
            amount=row.amount,
            currency=row.currency,
            due_date=row.due_date,
            source_row_key=row.row_key,
        ))
    if group.relation_type and group.relation_type != "NEW":
        db.add(m.ContractRelation(
            source_contract_id=subject.id,
            target_contract_id=group.relation_target_contract_id,
            relation_type=group.relation_type,
            reason=group.relation_reason,
            confirmed_by=user.id,
        ))
        record(db, user, "contract.relationship.confirmed", subject.id, {
            "relation_type": group.relation_type,
            "target_contract_id": group.relation_target_contract_id,
        })
    group.contract_subject_id = subject.id
    group.status = "CONTRACT_DRAFT_CREATED"
    group.row_version += 1
    db.flush()
    submitted = submit_subject(
        db,
        user,
        subject.id,
        subject.revision,
        data.workflow_definition_id,
        None,
        agent_permission_mode=agent_permission_mode_from_proposal(proposal),
    )
    record(db, user, "contract.document.linked", subject.id, {
        "group_id": group.id,
        "file_ids": [blob.id for _row, blob in files],
        "sha256": [blob.sha256 for _row, blob in files],
        "mold_ids": [row.mold_id for row in matches],
    })
    return {
        "project_id": project.id,
        "contract_intake_group_id": group.id,
        "subject_id": subject.id,
        "instance_id": submitted["instance_id"],
        "action": "sales_contract_from_intake",
        "status": "SUBMITTED",
    }
