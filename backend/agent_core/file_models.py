from sqlalchemy import String, Integer, ForeignKey, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import Mapped,mapped_column
from agent_core.model_base import Base, IdentityMixin


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


class RunFile(Base):
    __tablename__='agent_run_file'
    run_id:Mapped[str]=mapped_column(ForeignKey('ai_run.id'),primary_key=True)
    file_id:Mapped[str]=mapped_column(ForeignKey('file_object.id'),primary_key=True)
