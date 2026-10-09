"""Mold-specific authorization for files linked to governed business records."""
from sqlalchemy import select

from domain_packs.mold import contacts, models as m
from domain_packs.mold.ports.errors import DomainError


def _contract_readable(db, user, subject_id):
    from domain_packs.mold.erp.core import domains
    subject = db.get(m.BusinessSubject, subject_id)
    if not subject or subject.kind not in {'sales_contract', 'full_outsource_contract'}:
        return False
    try:
        domains.authorize(db, user, subject, 'read')
        return True
    except DomainError:
        return False


def _design_readable(db, user, subject_id):
    from domain_packs.mold.erp.core import domains
    subject = db.get(m.BusinessSubject, subject_id)
    if not subject or subject.kind != "design_route":
        return False
    try:
        domains.authorize(db, user, subject, "read")
        return True
    except DomainError:
        return False


def _trial_readable(db, user, subject_id):
    from domain_packs.mold.erp.core import domains
    subject = db.get(m.BusinessSubject, subject_id)
    if not subject or subject.kind != "trial_request":
        return False
    try:
        domains.authorize(db, user, subject, "read")
        return True
    except DomainError:
        return False


def _acceptance_readable(db, user, acceptance_id):
    from domain_packs.mold import authorization
    acceptance = db.get(m.CustomerAcceptanceRecord, acceptance_id)
    if not acceptance:
        return False
    try:
        return authorization.access(
            db,
            user,
            "project_close.read",
            {"project_id": acceptance.project_id},
        ).allowed
    except DomainError:
        return False


def _outbound_release_readable(db, user, release_id):
    from domain_packs.mold import authorization
    release = db.get(m.OutboundReleaseRecord, release_id)
    if not release:
        return False
    try:
        return authorization.access(
            db,
            user,
            "project_close.read",
            {"project_id": release.project_id},
        ).allowed
    except DomainError:
        return False


def _signature_readable(db, user, signature_id):
    from domain_packs.mold import authorization
    signature = db.get(m.CustomerDeliverySignature, signature_id)
    if not signature:
        return False
    for permission in ("project.dossier.read", "project_close.read"):
        try:
            if authorization.access(
                db,
                user,
                permission,
                {"project_id": signature.project_id},
            ).allowed:
                return True
        except DomainError:
            continue
    return False


def readable(db, user, blob):
    contact_links = list(db.scalars(select(m.ContactAttachment).where(
        m.ContactAttachment.file_id == blob.id
    )))
    contract_links = list(db.scalars(select(m.ContractAttachment).where(
        m.ContractAttachment.file_id == blob.id
    )))
    design_links = list(db.scalars(select(m.DesignAttachment).where(
        m.DesignAttachment.file_id == blob.id
    )))
    signing_links = list(db.scalars(select(m.ContractSigningRecord).where(
        m.ContractSigningRecord.signed_file_id == blob.id
    )))
    trial_links = list(db.scalars(select(m.TrialResultAttachment).where(
        m.TrialResultAttachment.file_id == blob.id
    )))
    acceptance_links = list(db.scalars(select(m.CustomerAcceptanceAttachment).where(
        m.CustomerAcceptanceAttachment.file_id == blob.id
    )))
    release_links = list(db.scalars(select(m.OutboundReleaseAttachment).where(
        m.OutboundReleaseAttachment.file_id == blob.id
    )))
    signature_links = list(db.scalars(select(m.CustomerDeliverySignatureAttachment).where(
        m.CustomerDeliverySignatureAttachment.file_id == blob.id
    )))
    mail_links = list(db.scalars(select(m.MailDocument).where(
        m.MailDocument.file_object_id == blob.id
    )))
    if mail_links:
        from domain_packs.mold.authorization import access
        return any(
            access(db, user, 'mail.read', {
                'account_id': message.account_id,
            }).allowed
            for link in mail_links
            if (message := db.get(m.MailMessage, link.message_id)) is not None
        )
    if not contact_links and not contract_links and not design_links and not signing_links and not trial_links and not acceptance_links and not release_links and not signature_links:
        return None
    if any(
        contacts.permitted(db, user, "read", db.get(m.ContactCase, link.case_id))
        for link in contact_links
    ):
        return True
    if any(_contract_readable(db, user, link.contract_subject_id) for link in contract_links):
        return True
    if any(_design_readable(db, user, link.design_subject_id) for link in design_links):
        return True
    if any(_contract_readable(db, user, link.contract_subject_id) for link in signing_links):
        return True
    return any(
        _trial_readable(db, user, db.get(m.TrialResult, link.trial_result_id).trial_id)
        for link in trial_links
        if db.get(m.TrialResult, link.trial_result_id)
    ) or any(_acceptance_readable(db, user, link.customer_acceptance_id) for link in acceptance_links) or any(
        _outbound_release_readable(db, user, link.outbound_release_id)
        for link in release_links
    ) or any(_signature_readable(db, user, link.signature_id) for link in signature_links)
