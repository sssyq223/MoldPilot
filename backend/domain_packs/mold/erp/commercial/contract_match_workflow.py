"""合同 OCR 完成后，为已确认承接方向的草稿生成匹配候选。"""
from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.erp.commercial import contract_intake
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.events import record


def confirm_contract_match_candidate(db, user, candidate_id, *, expected_target_version, operation_id):
    if not user or not user.super_admin:
        raise DomainError('SUPER_ADMIN_REQUIRED', '只有超级管理员可以确认合同匹配候选', 403)
    candidate = db.scalar(select(m.StartContractMatchCandidate).where(
        m.StartContractMatchCandidate.id == str(candidate_id),
    ).with_for_update())
    if not candidate:
        raise DomainError('NOT_FOUND', '合同匹配候选不存在', 404)
    existing = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == 'start_contract_match.confirmed',
        m.AuditEvent.resource_id == candidate.id,
        m.AuditEvent.detail['operation_id'].as_string() == operation_id,
    ).limit(1))
    if existing:
        return dict(existing.detail or {})
    if candidate.status != 'PROPOSED':
        raise DomainError('MATCH_CANDIDATE_ALREADY_RESOLVED', '合同匹配候选已经处理', 409)
    if candidate.target_version != expected_target_version:
        raise DomainError('VERSION_CONFLICT', '合同识别版本已变化，请重新生成候选', 409)
    candidate.status = 'CONFIRMED'
    candidate.confirmed_by = user.id
    candidate.confirmed_at = now()
    detail = {
        'candidate_id': candidate.id,
        'draft_id': candidate.draft_id,
        'target_type': candidate.target_type,
        'target_id': candidate.target_id,
        'target_version': candidate.target_version,
        'status': candidate.status,
        'operation_id': operation_id,
        'formal_binding': 'PENDING_EXISTING_START_NOTICE_GATE',
    }
    record(db, user, 'start_contract_match.confirmed', candidate.id, detail)
    db.flush()
    return detail


def propose_contract_matches(db, user, event_id):
    event = db.get(m.AuditEvent, str(event_id))
    if not event or event.action != "contract.ocr.ready":
        raise DomainError("CONTRACT_EVENT_INVALID", "合同 OCR 事件不存在或类型不匹配", 409)
    group = db.get(m.ContractIntakeGroup, event.resource_id)
    if not group:
        raise DomainError("CONTRACT_GROUP_NOT_FOUND", "合同识别分组不存在", 404)
    if group.status not in {"AWAITING_FIELD_CONFIRMATION", "READY_FOR_DRAFT"}:
        raise DomainError("CONTRACT_OCR_NOT_READY", "合同 OCR 结果尚不可匹配", 409)
    candidates = contract_intake.project_candidates(db, user, group.id)
    drafts = list(db.scalars(select(m.AdminStartNoticeDraft).where(
        m.AdminStartNoticeDraft.status == "READY_FOR_CONTRACT_MATCH",
    )))
    created = []
    for draft in drafts:
        project_id = draft.project_id or (draft.material_snapshot or {}).get("project_id")
        internal_mold_numbers = list((draft.material_snapshot or {}).get("internal_mold_numbers") or [])
        for row in candidates["projects"]:
            if project_id and row["id"] != project_id:
                continue
            evidence = {
                "contract_intake_group_id": group.id,
                "contract_group_version": group.row_version,
                "project_id": row["id"],
                "project_version": row["row_version"],
                "score": row["score"],
                "matched_by": row["matched_by"],
                "resolution": candidates["resolution"],
                "expected_internal_mold_numbers": internal_mold_numbers,
            }
            fingerprint = content_hash(evidence)
            existing = db.scalar(select(m.StartContractMatchCandidate).where(
                m.StartContractMatchCandidate.draft_id == draft.id,
                m.StartContractMatchCandidate.target_type == "CONTRACT_INTAKE_GROUP",
                m.StartContractMatchCandidate.target_id == group.id,
                m.StartContractMatchCandidate.target_version == group.row_version,
                m.StartContractMatchCandidate.target_fingerprint == fingerprint,
            ))
            if existing:
                continue
            candidate = m.StartContractMatchCandidate(
                draft_id=draft.id,
                draft_revision=draft.current_revision,
                target_type="CONTRACT_INTAKE_GROUP",
                target_id=group.id,
                target_version=group.row_version,
                target_fingerprint=fingerprint,
                status="PROPOSED",
                evidence_snapshot=evidence,
                proposed_by=user.id,
            )
            db.add(candidate)
            db.flush()
            record(db, user, "start_contract_match.proposed", candidate.id, {
                "candidate_id": candidate.id,
                "draft_id": draft.id,
                "target_type": candidate.target_type,
                "target_id": candidate.target_id,
                "target_version": candidate.target_version,
                "evidence": evidence,
            })
            created.append(candidate)
    db.flush()
    return {
        "event_id": event.id,
        "contract_intake_group_id": group.id,
        "resolution": candidates["resolution"],
        "candidate_ids": [row.id for row in created],
        "status": "PROPOSED",
        "as_of": now().isoformat(),
    }
