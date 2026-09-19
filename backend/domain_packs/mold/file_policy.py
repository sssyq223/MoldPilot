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
    if not contact_links and not contract_links and not design_links and not signing_links:
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
    return any(_contract_readable(db, user, link.contract_subject_id) for link in signing_links)
