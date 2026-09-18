"""Mold-specific authorization for files linked to engineering contacts."""
from sqlalchemy import select

from domain_packs.mold import contacts, models as m


def readable(db, user, blob):
    links = list(db.scalars(select(m.ContactAttachment).where(
        m.ContactAttachment.file_id == blob.id
    )))
    if not links:
        return None
    return any(
        contacts.permitted(db, user, "read", db.get(m.ContactCase, link.case_id))
        for link in links
    )
