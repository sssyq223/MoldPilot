"""受控销售合同文档接收、分类、重试与人工复核工具。"""
from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError, model_validator
from sqlalchemy import select

from agent_core.host_ports import host_ports
from domain_packs.mold import models as m
from domain_packs.mold.authorization import fingerprint
from domain_packs.mold.erp.commercial import contract_intake
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel


PROPOSAL_TOOLS = {
    "prepare_document_intake",
    "prepare_document_type_confirmation",
    "prepare_document_ocr_retry",
    "prepare_sales_contract_intake_review",
}


class DocumentIntakeQueryInput(StrictModel):
    document_intake_id: UUID | None = None
    file_id: UUID | None = None

    @model_validator(mode='after')
    def one_selector(self):
        if self.document_intake_id and self.file_id:
            raise ValueError('document_intake_id 与 file_id 不能同时提供')
        return self


class PrepareDocumentIntakeInput(StrictModel):
    file_ids: list[UUID] = Field(min_length=1, max_length=10)


class DocumentTypeConfirmation(StrictModel):
    intake_file_id: UUID
    document_type: Literal[
        "BID_NOTICE", "CUSTOMER_START_NOTICE", "SALES_CONTRACT", "MOLD_DRAWING", "ENGINEERING_CONTACT", "OTHER",
    ]
    contract_group_key: str | None = Field(default=None, max_length=60)


class PrepareDocumentTypeConfirmationInput(StrictModel):
    document_intake_id: UUID
    expected_version: int = Field(ge=1)
    files: list[DocumentTypeConfirmation] = Field(min_length=1, max_length=10)


class PrepareDocumentOcrRetryInput(StrictModel):
    document_intake_id: UUID
    expected_version: int = Field(ge=1)


class ConfirmedFieldInput(StrictModel):
    field_id: UUID
    confirmed_value: dict


class MoldMappingInput(StrictModel):
    row_key: str = Field(min_length=1, max_length=80)
    mold_id: UUID


class ContractRelationshipInput(StrictModel):
    relation_type: Literal["NEW", "DUPLICATE", "REVISION", "SUPPLEMENT", "REPLACEMENT"]
    target_contract_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=2000)


class PrepareSalesContractIntakeReviewInput(StrictModel):
    contract_intake_group_id: UUID
    expected_version: int = Field(ge=1)
    project_id: UUID
    project_version: int = Field(ge=1)
    confirmed_fields: list[ConfirmedFieldInput] = Field(min_length=1, max_length=1000)
    mold_mappings: list[MoldMappingInput] = Field(max_length=500)
    relationship: ContractRelationshipInput


MODELS = {
    "query_document_intake": DocumentIntakeQueryInput,
    "prepare_document_intake": PrepareDocumentIntakeInput,
    "prepare_document_type_confirmation": PrepareDocumentTypeConfirmationInput,
    "prepare_document_ocr_retry": PrepareDocumentOcrRetryInput,
    "prepare_sales_contract_intake_review": PrepareSalesContractIntakeReviewInput,
}


def schema(key):
    return MODELS[key].model_json_schema()


def _parse(key, arguments):
    try:
        return MODELS[key].model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "销售合同文档参数无效：" + error.errors()[0]["msg"]) from None


def _require_run(run, user):
    if not run or run.user_id != user.id:
        raise DomainError("FILE_CONTEXT_INVALID", "文档接收工具必须绑定当前 Agent Run", 403)
    return run


def _run_files(db, user, run):
    return host_ports().run_files(db, user, run)


def _bound_intakes(db, user, run):
    bound = _run_files(db, user, run)
    file_ids = [str(row['id']) for row in bound]
    if not file_ids:
        # 自动上传不创建 Agent Run；后续会话仍可在当前用户/当前会话范围内
        # 查询已登记的接收批次，但不会把这些文件变成当前 Run 的写入授权。
        return contract_intake.list_for_conversation(db, user, run.conversation_id)
    return list(db.scalars(select(m.DocumentIntake).join(
        m.DocumentIntakeFile, m.DocumentIntakeFile.intake_id == m.DocumentIntake.id,
    ).where(
        m.DocumentIntake.created_by == user.id,
        m.DocumentIntake.conversation_id == run.conversation_id,
        m.DocumentIntakeFile.file_id.in_(file_ids),
    ).distinct().order_by(m.DocumentIntake.created_at.desc(), m.DocumentIntake.id.desc())))


def _intake_for_bound_file(db, user, run, file_id):
    bound_ids = {str(row['id']) for row in _run_files(db, user, run)}
    if str(file_id) not in bound_ids:
        # 查询操作允许使用当前会话内的历史附件；跨会话或跨用户仍被拒绝。
        allowed = db.scalar(select(m.DocumentIntakeFile.id).join(
            m.DocumentIntake, m.DocumentIntakeFile.intake_id == m.DocumentIntake.id,
        ).where(
            m.DocumentIntakeFile.file_id == str(file_id),
            m.DocumentIntake.created_by == user.id,
            m.DocumentIntake.conversation_id == run.conversation_id,
        ))
        if not allowed:
            raise DomainError('FILE_CONTEXT_INVALID', '文件不属于当前 Agent Run 或当前会话', 403)
    intake = db.scalar(select(m.DocumentIntake).join(
        m.DocumentIntakeFile, m.DocumentIntakeFile.intake_id == m.DocumentIntake.id,
    ).where(
        m.DocumentIntakeFile.file_id == str(file_id),
        m.DocumentIntake.created_by == user.id,
        m.DocumentIntake.conversation_id == run.conversation_id,
    ).order_by(m.DocumentIntake.created_at.desc(), m.DocumentIntake.id.desc()))
    if not intake:
        raise DomainError('NOT_FOUND', '文件尚未登记文档接收批次', 404)
    return intake


def _preview_create(db, user, run, data):
    run = _require_run(run, user)
    bound = _run_files(db, user, run)
    by_id = {str(row["id"]): row for row in bound}
    requested = [str(value) for value in data.file_ids]
    if len(requested) != len(set(requested)) or set(requested) != set(by_id):
        raise DomainError("FILE_CONTEXT_INVALID", "必须一次接收本次 Agent Run 绑定的全部文档", 403)
    if any(by_id[file_id].get("media_type") not in {
        "application/pdf", "image/png", "image/jpeg",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    } for file_id in requested):
        raise DomainError("DOCUMENT_MEDIA_TYPE_UNSUPPORTED", "文档识别只接受 PDF、PNG、JPG 或 DOCX 文件")
    return {
        "操作": "创建销售合同文档接收批次并开始预分类",
        "会话": run.conversation_id,
        "文件": [{"id": file_id, "文件名": by_id[file_id]["filename"], "SHA256": by_id[file_id]["sha256"]}
                 for file_id in requested],
        "后续": "预分类由独立 Worker 异步执行；完成或失败后通知本人，本 Run 不等待或轮询。",
    }


def _preview_types(db, user, data):
    intake = contract_intake.load(db, user, str(data.document_intake_id))
    current = contract_intake.serialize(db, intake)
    if intake.row_version != data.expected_version:
        raise DomainError("STALE_VERSION", "文档接收记录已变化，请重新查询", 409)
    if intake.status != "AWAITING_TYPE_CONFIRMATION":
        raise DomainError("PRECLASSIFICATION_INCOMPLETE", "文档预分类尚未进入人工类型确认", 409)
    expected_ids = {row["id"] for row in current["files"]}
    supplied_ids = [str(row.intake_file_id) for row in data.files]
    if len(supplied_ids) != len(set(supplied_ids)) or set(supplied_ids) != expected_ids:
        raise DomainError("DOCUMENT_CONFIRMATION_INCOMPLETE", "必须一次确认本批次全部 PDF 类型")
    rows = []
    for row in data.files:
        group_key = (row.contract_group_key or "").strip()
        if row.document_type == "SALES_CONTRACT" and not group_key:
            raise DomainError("CONTRACT_GROUP_REQUIRED", "销售合同 PDF 必须选择合同分组")
        if row.document_type != "SALES_CONTRACT" and group_key:
            raise DomainError("CONTRACT_GROUP_INVALID", "非销售合同 PDF 不能加入合同分组")
        source = next(item for item in current["files"] if item["id"] == str(row.intake_file_id))
        rows.append({"文件名": source["filename"], "确认类型": row.document_type,
                     "合同分组": group_key or None})
    contact_rows = [row for row in rows if row["确认类型"] == "ENGINEERING_CONTACT"]
    if contact_rows and len(contact_rows) != 1:
        raise DomainError("ENGINEERING_CONTACT_FILE_REQUIRED", "一个接收批次必须且只能确认一个工程联络单文件", 409)
    return {"操作": "确认文档类型", "接收批次": intake.id, "当前版本": intake.row_version, "文件": rows,
            "后续": "工程联络单将由工程联络 Skill 生成创建 Proposal；销售合同才进入完整 OCR。" if contact_rows
                     else "只有人工确认为销售合同的文档才会进入完整 OCR。"}


def _preview_retry(db, user, data):
    intake = contract_intake.load(db, user, str(data.document_intake_id))
    current = contract_intake.serialize(db, intake)
    if intake.row_version != data.expected_version:
        raise DomainError("STALE_VERSION", "文档接收记录已变化，请重新查询", 409)
    if intake.status != "OCR_FAILED":
        raise DomainError("OCR_RETRY_NOT_ALLOWED", "当前没有可人工重试的失败 OCR 任务", 409)
    failed = [row for row in current["files"] if row.get("ocr_status") == "FAILED"]
    if not failed:
        raise DomainError("OCR_RETRY_NOT_ALLOWED", "未找到失败 OCR 任务", 409)
    return {"操作": "重新排队失败的 OCR 任务", "接收批次": intake.id, "当前版本": intake.row_version,
            "失败文件": [{"文件名": row["filename"], "错误码": row.get("ocr_error")} for row in failed],
            "后续": "确认后只重置失败任务并交由独立 Worker 异步处理。"}


def _preview_review(db, user, data):
    group = contract_intake.load_group(db, user, str(data.contract_intake_group_id))
    if group.row_version != data.expected_version:
        raise DomainError("STALE_VERSION", "合同识别记录已变化，请重新查询", 409)
    if group.status != "AWAITING_FIELD_CONFIRMATION":
        raise DomainError("CONTRACT_OCR_NOT_READY", "合同 OCR 结果尚不可复核", 409)
    detail = contract_intake.serialize_group(db, group)
    candidates = contract_intake.project_candidates(db, user, group.id)
    return {
        "操作": "确认销售合同 OCR 字段、项目、模具和合同关系",
        "识别分组": group.id,
        "当前版本": group.row_version,
        "目标项目": str(data.project_id),
        "项目版本": data.project_version,
        "字段确认数": len(data.confirmed_fields),
        "OCR字段总数": len(detail["fields"]),
        "模具映射数": len(data.mold_mappings),
        "合同关系": data.relationship.model_dump(mode="json"),
        "项目候选状态": candidates["resolution"],
        "说明": "确认后形成 READY_FOR_DRAFT 复核事实；不会直接创建合同或绕过两级审批。",
    }


def _preview(db, user, key, data, run):
    if key == "prepare_document_intake":
        return _preview_create(db, user, run, data)
    if key == "prepare_document_type_confirmation":
        return _preview_types(db, user, data)
    if key == "prepare_document_ocr_retry":
        return _preview_retry(db, user, data)
    if key == "prepare_sales_contract_intake_review":
        return _preview_review(db, user, data)
    raise DomainError("TOOL_UNKNOWN", "工具未实现", 403)


def execute_tool(db, user, key, arguments, run=None):
    data = _parse(key, arguments)
    if key == "query_document_intake":
        run = _require_run(run, user)
        if data.file_id:
            rows = [_intake_for_bound_file(db, user, run, data.file_id)]
        elif data.document_intake_id:
            rows = [contract_intake.load(db, user, str(data.document_intake_id))]
            intake = rows[0]
            if intake.conversation_id != run.conversation_id:
                raise DomainError('FILE_CONTEXT_INVALID', '文档接收批次不属于当前会话', 403)
        else:
            rows = _bound_intakes(db, user, run)
        return {
            "data": [contract_intake.serialize(db, row) for row in rows],
            "source": "agent_db",
            "as_of": now().isoformat(),
            "limitations": ["只返回数据库中的权威接收、分类与 OCR 状态；不会轮询 Worker。"],
        }
    display = _preview(db, user, key, data, run)
    proposal = {
        "kind": "sales_contract_document_intake",
        "action": key.removeprefix("prepare_"),
        "requires_approval": False,
        "input": data.model_dump(mode="json"),
        "display": display,
        "confirmation_policy": proposal_confirmation_policy(run, requires_approval=False),
    }
    return {
        "data": [],
        "source": "agent_proposal",
        "as_of": now().isoformat(),
        "proposal": proposal,
        "limitations": ["只有本人确认 proposal 后才会写入接收、类型、重试或人工复核结果。"],
    }


def _source_context(db, user, step_id):
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
    if step.tool not in PROPOSAL_TOOLS or step.tool not in available_tools(db, user) or not proposal:
        raise DomainError("TOOL_FORBIDDEN", "操作能力不可用", 403)
    return proposal, step, run


def source(db, user, step_id):
    """通用确认接口只接收建议内容，不包含内部执行上下文。"""
    proposal, _step, _run = _source_context(db, user, step_id)
    return proposal


def validate_intent(db, user, payload):
    proposal, step, run = _source_context(db, user, payload["step_id"])
    if content_hash(proposal) != payload["proposal_hash"]:
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    data = _parse(step.tool, proposal["input"])
    display = _preview(db, user, step.tool, data, run)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "文档、识别状态或复核材料已变化", 409)
    return proposal, step.tool, data, run


def confirm(db, user, payload):
    _proposal, key, data, run = validate_intent(db, user, payload)
    if key == "prepare_document_intake":
        intake = contract_intake.create(
            db, user,
            conversation_id=run.conversation_id,
            file_ids=[str(value) for value in data.file_ids],
            request_key=run.id,
        )
        return {"action": "document_intake", "status": intake.status, "document_intake_id": intake.id,
                "conversation_id": intake.conversation_id}
    if key == "prepare_document_type_confirmation":
        intake = contract_intake.confirm_types(
            db, user, str(data.document_intake_id),
            expected_version=data.expected_version,
            confirmations=[{
                "intake_file_id": str(row.intake_file_id),
                "document_type": row.document_type,
                "contract_group_key": row.contract_group_key,
            } for row in data.files],
        )
        return {"action": "document_type_confirmation", "status": intake.status,
                "document_intake_id": intake.id, "row_version": intake.row_version}
    if key == "prepare_document_ocr_retry":
        intake = contract_intake.retry_failed_ocr(
            db, user, str(data.document_intake_id), expected_version=data.expected_version,
        )
        return {"action": "document_ocr_retry", "status": intake.status,
                "document_intake_id": intake.id, "row_version": intake.row_version}
    group = contract_intake.review_group(
        db, user, str(data.contract_intake_group_id),
        expected_version=data.expected_version,
        project_id=str(data.project_id),
        project_version=data.project_version,
        confirmed_fields=[{
            "field_id": str(row.field_id), "confirmed_value": row.confirmed_value,
        } for row in data.confirmed_fields],
        mold_mappings=[{
            "row_key": row.row_key, "mold_id": str(row.mold_id),
        } for row in data.mold_mappings],
        relationship={
            "relation_type": data.relationship.relation_type,
            "target_contract_id": (str(data.relationship.target_contract_id)
                                   if data.relationship.target_contract_id else None),
            "reason": data.relationship.reason,
        },
    )
    return {"action": "sales_contract_intake_review", "status": group.status,
            "contract_intake_group_id": group.id, "row_version": group.row_version}
