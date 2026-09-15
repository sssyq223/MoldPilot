from sqlalchemy import String, Integer, ForeignKey, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import Mapped,mapped_column
from .models import IdentityMixin
from .db import Base


class FileObject(IdentityMixin,Base):
    __tablename__='file_object'
    owner_id:Mapped[str]=mapped_column(ForeignKey('app_user.id'),index=True)
    conversation_id:Mapped[str]=mapped_column(ForeignKey('ai_conversation.id'),index=True)
    request_key:Mapped[str]=mapped_column(String(36))
    filename:Mapped[str]=mapped_column(String(200))
    media_type:Mapped[str]=mapped_column(String(120))
    size:Mapped[int]=mapped_column(Integer)
    sha256:Mapped[str]=mapped_column(String(64))
    backend:Mapped[str]=mapped_column(String(10))
    storage_namespace:Mapped[str]=mapped_column(String(200))
    object_key:Mapped[str]=mapped_column(String(150),unique=True)
    storage_version:Mapped[str|None]=mapped_column(String(1024))
    __table_args__=(UniqueConstraint('owner_id','request_key'),CheckConstraint('size > 0'),CheckConstraint("backend IN ('local','s3')"))


class ContactAttachment(IdentityMixin,Base):
    __tablename__='contact_attachment'
    case_id:Mapped[str]=mapped_column(ForeignKey('contact_case.id'),index=True)
    file_id:Mapped[str]=mapped_column(ForeignKey('file_object.id'),index=True)
    document_id:Mapped[str]=mapped_column(String(36))
    version:Mapped[int]=mapped_column(Integer)
    title:Mapped[str]=mapped_column(String(150))
    previous_id:Mapped[str|None]=mapped_column(ForeignKey('contact_attachment.id'))
    created_by:Mapped[str]=mapped_column(ForeignKey('app_user.id'))
    __table_args__=(UniqueConstraint('case_id','document_id','version'),UniqueConstraint('case_id','file_id'),CheckConstraint('version > 0'))


class RunFile(Base):
    __tablename__='agent_run_file'
    run_id:Mapped[str]=mapped_column(ForeignKey('ai_run.id'),primary_key=True)
    file_id:Mapped[str]=mapped_column(ForeignKey('file_object.id'),primary_key=True)
