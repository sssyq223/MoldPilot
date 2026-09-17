"""ORM models owned by the mold business pack."""
from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.model_base import Base, IdentityMixin


class Project(IdentityMixin, Base):
    __tablename__ = "project"
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class Material(IdentityMixin, Base):
    __tablename__ = "material"
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    category: Mapped[str] = mapped_column(String(60))
    unit: Mapped[str] = mapped_column(String(20))


class PurchaseRequest(IdentityMixin, Base):
    __tablename__ = "purchase_request"
    number: Mapped[str] = mapped_column(String(80), unique=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"))
    created_by: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    remark: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    round_no: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','RETURNED')"),
    )


class PurchaseLine(IdentityMixin, Base):
    __tablename__ = "purchase_request_line"
    request_id: Mapped[str] = mapped_column(ForeignKey("purchase_request.id"), index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("material.id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    due_date: Mapped[date] = mapped_column(Date)
    __table_args__ = (CheckConstraint("quantity > 0"),)


def _mapped_exports(module):
    return {
        name: value
        for name, value in vars(module).items()
        if isinstance(value, type)
        and getattr(value, "__module__", None) == module.__name__
        and hasattr(value, "__table__")
    }


from . import attachment_models as _attachments  # noqa: E402
from . import contact_models as _contacts  # noqa: E402
from . import domain_models as _domain  # noqa: E402

_exports = {
    "Project": Project,
    "Material": Material,
    "PurchaseRequest": PurchaseRequest,
    "PurchaseLine": PurchaseLine,
    **_mapped_exports(_domain),
    **_mapped_exports(_contacts),
    **_mapped_exports(_attachments),
}
globals().update(_exports)
EXPORTED_MODELS = tuple(_exports)
__all__ = EXPORTED_MODELS
