"""Formal-start business material and passive contract follow-up projection."""
from datetime import date

from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError


TRACKING_ROLES = (
    ("FINANCE_OWNER", "财务负责人"),
    ("BUSINESS_OWNER", "业务负责人"),
    ("MARKETING_OWNER", "市场负责人"),
)


def _revision_for_project(db, project_id, revision_id):
    revision = db.get(m.BidIntakeRevision, revision_id)
    case = db.get(m.BidIntakeCase, revision.case_id) if revision else None
    if not revision or not case or case.project_id != project_id:
        raise DomainError(
            "BID_INTAKE_REVISION_MISMATCH",
            "正式开工引用的中标接收版本不属于当前项目",
            409,
        )
    return revision


def _molds(db, project_id):
    return [
        {
            "id": mold.id,
            "internal_number": mold.internal_number,
            "name": mold.name,
            "status": mold.status,
        }
        for _, mold in db.execute(
            select(m.ProjectMold, m.Mold)
            .join(m.Mold, m.Mold.id == m.ProjectMold.mold_id)
            .where(m.ProjectMold.project_id == project_id)
            .order_by(m.Mold.internal_number, m.Mold.id)
        )
    ]


def _effective_contracts(db, project_id):
    result = []
    rows = db.scalars(
        select(m.BusinessSubject)
        .where(
            m.BusinessSubject.project_id == project_id,
            m.BusinessSubject.kind == "sales_contract",
            m.BusinessSubject.status == "EFFECTIVE",
        )
        .order_by(m.BusinessSubject.created_at.desc(), m.BusinessSubject.id)
    )
    for subject in rows:
        detail = db.get(m.ContractDetail, subject.id)
        receipt = db.get(m.ContractReceiptEvidence, subject.id)
        attachments = list(
            db.scalars(
                select(m.ContractAttachment).where(
                    m.ContractAttachment.contract_subject_id == subject.id
                )
            )
        )
        result.append(
            {
                "id": subject.id,
                "number": subject.number,
                "contract_number": detail.contract_number if detail else None,
                "received_date": receipt.received_date.isoformat() if receipt else None,
                "attachment_count": len(attachments),
            }
        )
    return result


def build(
    db,
    project,
    revision_id,
    effective_date,
    expected_contract_date=None,
    contract_visibility=True,
):
    """Build and validate the exact business material shown on the start card."""
    revision = _revision_for_project(db, project.id, revision_id)
    molds = _molds(db, project.id)
    if not molds:
        raise DomainError(
            "INTERNAL_MOLD_REQUIRED",
            "正式开工前必须先在 ERP 确认项目关联的唯一内部模具号",
            409,
        )
    internal_numbers = {row["internal_number"] for row in molds}
    if (
        revision.historical_mold_number
        and revision.historical_mold_number not in internal_numbers
    ):
        raise DomainError(
            "HISTORICAL_MOLD_MISMATCH",
            "已有模具设变必须复用已关联的原内部模具号，请先核对 ERP 模具关系",
            409,
        )

    authoritative_contracts = _effective_contracts(db, project.id)
    contracts = authoritative_contracts if contract_visibility else []
    if contract_visibility and not contracts and not expected_contract_date:
        raise DomainError(
            "EXPECTED_CONTRACT_DATE_REQUIRED",
            "销售合同尚未到达时必须填写预计到达日期；合同晚到不阻塞开工",
            409,
        )
    if contract_visibility and contracts and expected_contract_date:
        raise DomainError(
            "EXPECTED_CONTRACT_DATE_NOT_APPLICABLE",
            "当前已有生效销售合同，不应再填写未到合同的预计到达日期",
            409,
        )

    profile = db.get(m.ProjectProfile, project.id)
    customer = db.get(m.Customer, profile.customer_id) if profile and profile.customer_id else None
    return {
        "project": {
            "id": project.id,
            "code": project.code,
            "name": project.name,
            "row_version": project.row_version,
        },
        "customer": {
            "id": customer.id if customer else None,
            "code": customer.code if customer else None,
            "name": customer.name if customer else revision.customer_company,
            "company_from_intake": revision.customer_company,
            "contact": revision.customer_contact,
        },
        "external_order_number": revision.external_order_number,
        "customer_mold_number": revision.customer_mold_number,
        "customer_model_or_material": revision.customer_model_or_material,
        "processing_kind": (
            "MOLD_CHANGE" if revision.historical_mold_number else "NEW_MOLD"
        ),
        "historical_mold_number": revision.historical_mold_number,
        "internal_molds": molds,
        "external_start_date": (
            revision.external_start_date.isoformat()
            if revision.external_start_date
            else None
        ),
        "formal_start_date": effective_date.isoformat(),
        "customer_due_date": (
            revision.customer_due_date.isoformat()
            if revision.customer_due_date
            else None
        ),
        "sales_contracts_at_issue": contracts,
        "contract_state_at_issue": (
            "RECEIVED"
            if contracts
            else "EXPECTED"
            if contract_visibility
            else "UNKNOWN_TO_INITIATOR"
        ),
        "expected_contract_date": (
            expected_contract_date.isoformat() if expected_contract_date else None
        ),
        "bid_intake_revision": {
            "id": revision.id,
            "version": revision.version,
            "source_kind": revision.source_kind,
            "source_ref": revision.source_ref,
        },
    }


def create(db, user, subject, revision_id, material, expected_contract_date=None):
    existing = db.get(m.InternalStartSnapshot, subject.id)
    if existing:
        return existing
    row = m.InternalStartSnapshot(
        start_subject_id=subject.id,
        project_id=subject.project_id,
        bid_intake_revision_id=revision_id,
        linked_business=material,
        expected_contract_date=expected_contract_date,
        frozen_by=user.id,
        frozen_at=now(),
    )
    db.add(row)
    db.flush()
    return row


def card(db, start_subject_id):
    if not start_subject_id:
        return None
    row = db.get(m.InternalStartSnapshot, start_subject_id)
    if not row:
        return None
    return {
        "start_subject_id": row.start_subject_id,
        "project_id": row.project_id,
        "bid_intake_revision_id": row.bid_intake_revision_id,
        "linked_business": row.linked_business,
        "expected_contract_date": (
            row.expected_contract_date.isoformat()
            if row.expected_contract_date
            else None
        ),
        "frozen_by": row.frozen_by,
        "frozen_at": row.frozen_at.isoformat(),
    }


def frozen_material_model_context(snapshot):
    """Return the compact, unambiguous provider view of a frozen snapshot."""
    linked = (
        snapshot.get("linked_business", {})
        if isinstance(snapshot, dict) else {}
    )
    order_number = linked.get("external_order_number")
    return {
        "is_frozen": bool(snapshot),
        "frozen_at": snapshot.get("frozen_at") if isinstance(snapshot, dict) else None,
        "customer_order": {
            "number": order_number,
            "frozen_in_snapshot": bool(snapshot and order_number),
        },
        "customer_order_number": order_number,
        "external_order_number": order_number,
        "customer_mold_number": linked.get("customer_mold_number"),
        "customer_model_or_material": linked.get("customer_model_or_material"),
        "internal_mold_numbers": [
            item.get("internal_number")
            for item in linked.get("internal_molds", [])
        ],
        "expected_contract_date": (
            snapshot.get("expected_contract_date")
            if isinstance(snapshot, dict) else None
        ),
        "customer_due_date": linked.get("customer_due_date"),
    }


def contract_follow_up_model_context(follow_up):
    """Flatten reminder people and roles without discarding the audit receipt."""
    follow_up = follow_up if isinstance(follow_up, dict) else {}
    reminder = follow_up.get("passive_reminder")
    reminder = reminder if isinstance(reminder, dict) else {}
    recipients = reminder.get("recipients")
    recipients = recipients if isinstance(recipients, list) else []
    recipient_names = list(dict.fromkeys(
        person.get("name")
        for person in recipients
        if isinstance(person, dict) and person.get("name")
    ))
    recipient_roles = []
    for person in recipients:
        if not isinstance(person, dict):
            continue
        roles = person.get("roles")
        if not isinstance(roles, list):
            roles = [{"role_name": person.get("role_name")}]
        for role in roles:
            role_name = role.get("role_name") if isinstance(role, dict) else None
            if role_name and role_name not in recipient_roles:
                recipient_roles.append(role_name)
    contracts = follow_up.get("contracts")
    contracts = contracts if isinstance(contracts, list) else []
    return {
        "state": follow_up.get("state"),
        "expected_date": follow_up.get("expected_date"),
        "actual_received_date": follow_up.get("actual_received_date"),
        "contract_count": len(contracts),
        "contracts": contracts,
        "overdue_reminder": {
            "active": bool(reminder),
            "kind": reminder.get("kind"),
            "reason": reminder.get("reason"),
            "recipient_names": recipient_names,
            "recipient_roles": recipient_roles,
            "required_role_gaps": reminder.get("required_role_gaps", []),
        },
    }


def _tracking_people(db, project_id):
    result = []
    by_user = {}

    def add_person(person, role_key, role_name):
        existing = by_user.get(person.id)
        if existing:
            if role_key not in existing["role_keys"]:
                existing["role_keys"].append(role_key)
                existing["roles"].append(
                    {"role_key": role_key, "role_name": role_name}
                )
            return
        row = {
            "user_id": person.id,
            "name": person.display_name,
            "role_key": role_key,
            "role_name": role_name,
            "role_keys": [role_key],
            "roles": [{"role_key": role_key, "role_name": role_name}],
        }
        by_user[person.id] = row
        result.append(row)

    profile = db.get(m.ProjectProfile, project_id)
    if profile and profile.owner_user_id:
        person = db.get(m.User, profile.owner_user_id)
        if person and person.active:
            add_person(person, "PROJECT_OWNER", "项目负责人")
    for role_key, role_name in TRACKING_ROLES:
        rows = db.execute(
            select(m.User)
            .join(m.ProjectRoleMember, m.ProjectRoleMember.user_id == m.User.id)
            .where(
                m.ProjectRoleMember.project_id == project_id,
                m.ProjectRoleMember.role_key == role_key,
                m.User.active.is_(True),
            )
            .order_by(m.User.display_name, m.User.id)
        )
        for (person,) in rows:
            add_person(person, role_key, role_name)
    return result


def contract_follow_up(db, project_id, start_subject_id, visible_contracts=None):
    snapshot = card(db, start_subject_id)
    if not snapshot:
        return {
            "state": "NOT_TRACKED",
            "expected_date": None,
            "actual_received_date": None,
            "contracts": [],
            "passive_reminder": None,
        }
    if visible_contracts is None:
        return {
            "state": "CONTRACT_VISIBILITY_REQUIRED",
            "expected_date": snapshot.get("expected_contract_date"),
            "actual_received_date": None,
            "contracts": [],
            "passive_reminder": None,
        }
    contracts = list(visible_contracts)
    received = []
    for item in contracts:
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else item
        if item.get("status") == "EFFECTIVE" or "contract_number" in detail:
            received.append(
                {
                    "id": item.get("id"),
                    "number": item.get("number"),
                    "contract_number": detail.get("contract_number"),
                    "received_date": detail.get("received_date"),
                    "attachment_count": len(detail.get("attachments") or []),
                }
            )
    expected = snapshot.get("expected_contract_date")
    actual_dates = [row["received_date"] for row in received if row.get("received_date")]
    if received and actual_dates and all(row["attachment_count"] for row in received):
        state = "RECEIVED"
    elif received and not actual_dates:
        state = "RECEIVED_DATE_MISSING"
    elif received:
        state = "RECEIVED_ATTACHMENT_MISSING"
    elif not expected:
        state = "NOT_REQUIRED"
    elif expected < now().date().isoformat():
        state = "OVERDUE"
    elif expected == now().date().isoformat():
        state = "DUE_TODAY"
    else:
        state = "PENDING"

    reminder = None
    if state == "OVERDUE":
        recipients = _tracking_people(db, project_id)
        present_roles = {
            role_key
            for row in recipients
            for role_key in row.get("role_keys", [row["role_key"]])
        }
        expected_roles = {"PROJECT_OWNER", "FINANCE_OWNER"}
        reminder = {
            "kind": "PASSIVE_WARNING",
            "reason": "销售合同超过预计到达日期仍未收到",
            "recipients": recipients,
            "required_role_gaps": sorted(expected_roles - present_roles),
            "tracking_roles": ["PROJECT_OWNER", "FINANCE_OWNER", "BUSINESS_OWNER", "MARKETING_OWNER"],
            "delivery_semantics": "用户查询时返回预警；不会伪造合同到达或自动签订合同",
        }
    return {
        "state": state,
        "expected_date": expected,
        "actual_received_date": min(actual_dates) if actual_dates else None,
        "contracts": received,
        "passive_reminder": reminder,
    }
