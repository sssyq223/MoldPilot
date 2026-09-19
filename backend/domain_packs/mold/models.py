"""ORM models owned by the mold business pack."""
from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from agent_core.model_base import Base, IdentityMixin
from agent_core import models as _core_models

# Business services may reference host-owned identities, workflow records and
# audit records through the pack model namespace.  The dependency points from
# the pack to the stable host model port; the host model facade never leaks
# mold types back into the framework implementation.
for _name, _value in vars(_core_models).items():
    if isinstance(_value, type) and hasattr(_value, "__table__"):
        globals()[_name] = _value


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


from domain_packs.mold import attachment_models as _attachments  # noqa: E402
from domain_packs.mold import contact_models as _contacts  # noqa: E402
from domain_packs.mold import domain_models as _domain  # noqa: E402
from domain_packs.mold.erp.commercial import contract_models as _contract_models  # noqa: E402
from domain_packs.mold.erp.commercial import quotation_models as _quotation_models  # noqa: E402
from domain_packs.mold.erp.design import design_models as _design_models  # noqa: E402

_exports = {
    "Project": Project,
    "Material": Material,
    "PurchaseRequest": PurchaseRequest,
    "PurchaseLine": PurchaseLine,
    **_mapped_exports(_domain),
    **_mapped_exports(_contacts),
    **_mapped_exports(_attachments),
    **_mapped_exports(_contract_models),
    **_mapped_exports(_quotation_models),
    **_mapped_exports(_design_models),
}
globals().update(_exports)
EXPORTED_MODELS = tuple(_exports)
__all__ = EXPORTED_MODELS
