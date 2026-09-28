"""Contract-file validation, immutable linkage and approval snapshot cards."""
from uuid import uuid4

from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.events import record
from domain_packs.mold.ports.files import reference_run_files


CONTRACT_MEDIA_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
ACTIVE_CONTRACT_STATES = ("DRAFT", "SUBMITTED", "RETURNED", "APPLY_BLOCKED", "EFFECTIVE")


def _card(link, blob, *, current=True):
    return {
        "id": link.id if link else blob.id,
        "file_id": blob.id,
        "filename": blob.filename,
        "media_type": blob.media_type,
        "size": blob.size,
        "sha256": blob.sha256,
        "title": link.title if link else blob.filename,
        "document_id": link.document_id if link else None,
        "version": link.version if link else 1,
        "source_kind": link.source_kind if link else None,
        "previous_id": link.previous_id if link else None,
        "uploaded_by": link.uploaded_by if link else blob.owner_id,
        "uploaded_at": (link.created_at if link else blob.created_at).isoformat(),
        "is_current": current,
    }


def validate_proposal_files(db, user, file_ids, run, project_id, contract_kind, existing_subject_id=None):
    if not run or run.user_id != user.id:
        raise DomainError("FILE_CONTEXT_INVALID", "合同附件必须来自当前本人会话任务", 403)
    referenced={blob.id:blob for blob in reference_run_files(db,user,run,file_ids)}
    blobs = []
    for file_id in file_ids:
        blob = referenced[file_id]
        if blob.media_type not in CONTRACT_MEDIA_TYPES:
            raise DomainError("CONTRACT_FILE_TYPE", "合同原件仅支持 PDF、图片或 DOCX", 409)
        duplicate = db.scalar(
            select(m.ContractAttachment.id)
            .join(m.FileObject, m.FileObject.id == m.ContractAttachment.file_id)
            .join(m.BusinessSubject, m.BusinessSubject.id == m.ContractAttachment.contract_subject_id)
            .where(
                m.BusinessSubject.project_id == project_id,
                m.BusinessSubject.kind == contract_kind,
                m.BusinessSubject.status.in_(ACTIVE_CONTRACT_STATES),
                m.BusinessSubject.id != existing_subject_id if existing_subject_id else True,
                m.FileObject.sha256 == blob.sha256,
            )
            .limit(1)
        )
        if duplicate:
            raise DomainError("CONTRACT_FILE_DUPLICATE", "该项目已有内容相同的有效合同附件，请勿重复提交", 409)
        if any(previous.sha256 == blob.sha256 for previous in blobs):
            raise DomainError("CONTRACT_FILE_DUPLICATE", "本轮合同不能重复选择内容相同的原件", 409)
        blobs.append(blob)
    return blobs


def preview_cards(blobs, source_kind):
    return [{**_card(None, blob), "source_kind": source_kind} for blob in blobs]


def link_initial(db, user, subject, blobs, source_kind):
    links = []
    for blob in blobs:
        existing = db.scalar(select(m.ContractAttachment).where(
            m.ContractAttachment.contract_subject_id == subject.id, m.ContractAttachment.file_id == blob.id))
        if existing:
            links.append(existing)
            continue
        link = m.ContractAttachment(
            contract_subject_id=subject.id,
            file_id=blob.id,
            document_id=str(uuid4()),
            version=1,
            title=blob.filename,
            source_kind=source_kind,
            previous_id=None,
            uploaded_by=user.id,
        )
        db.add(link)
        db.flush()
        record(db, user, "contract.attachment_linked", subject.id, {
            "attachment_id": link.id,
            "file_id": blob.id,
            "filename": blob.filename,
            "sha256": blob.sha256,
            "version": 1,
            "source_kind": source_kind,
        })
        links.append(link)
    return links


def link_intake(db, user, subject, files):
    """Link OCR source PDFs through the canonical immutable attachment model."""
    links = []
    for intake_file, blob in files:
        link = m.ContractAttachment(
            contract_subject_id=subject.id,
            file_id=blob.id,
            document_id=str(uuid4()),
            version=1,
            title=blob.filename,
            source_kind="ELECTRONIC",
            previous_id=None,
            uploaded_by=user.id,
            intake_file_id=intake_file.id,
            role=intake_file.confirmed_role or "OTHER",
        )
        db.add(link)
        db.flush()
        record(db, user, "contract.attachment_linked", subject.id, {
            "attachment_id": link.id,
            "file_id": blob.id,
            "filename": blob.filename,
            "sha256": blob.sha256,
            "version": 1,
            "source_kind": link.source_kind,
            "intake_file_id": intake_file.id,
            "role": link.role,
        })
        links.append(link)
    return links


def cards(db, contract_subject_id):
    rows = list(db.execute(
        select(m.ContractAttachment, m.FileObject)
        .join(m.FileObject, m.FileObject.id == m.ContractAttachment.file_id)
        .where(m.ContractAttachment.contract_subject_id == contract_subject_id)
        .order_by(m.ContractAttachment.document_id, m.ContractAttachment.version.desc())
    ))
    latest = {}
    for link, _ in rows:
        latest.setdefault(link.document_id, link.version)
    from .contract_materials import terms
    current_terms = terms(db, contract_subject_id)
    selected = set(current_terms.attachment_selection) if current_terms and current_terms.attachment_selection is not None else None
    if selected is not None and not selected <= {blob.id for _,blob in rows}:
        raise DomainError('CONTRACT_FILE_SELECTION_INVALID', '本轮合同原件选择包含未关联文件，请核对材料', 409)
    return [_card(link, blob, current=(blob.id in selected if selected is not None else link.version == latest[link.document_id]))
            for link, blob in rows]
