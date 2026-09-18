"""Typed local business facts. Shared approval envelopes do not replace domain tables."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import String, Date, DateTime, Integer, Boolean, Text, Numeric, ForeignKey, UniqueConstraint, CheckConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column
from agent_core.model_base import IdentityMixin, Base, J


class BusinessSubject(IdentityMixin, Base):
    __tablename__ = 'business_subject'
    kind: Mapped[str] = mapped_column(String(60), index=True)
    number: Mapped[str] = mapped_column(String(80), unique=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), index=True)
    category: Mapped[str | None] = mapped_column(String(60))
    warehouse_id: Mapped[str | None] = mapped_column(ForeignKey('warehouse.id'))
    created_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    status: Mapped[str] = mapped_column(String(30), default='DRAFT')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    round_no: Mapped[int] = mapped_column(Integer, default=0)
    remark: Mapped[str] = mapped_column(Text, default='')
    __table_args__ = (CheckConstraint("status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','RETURNED','APPLY_BLOCKED','EFFECTIVE','CANCELLED','CLOSED')", name='subject_status'),)


class Supplier(IdentityMixin, Base):
    __tablename__ = 'supplier'
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    category: Mapped[str] = mapped_column(String(60))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Customer(IdentityMixin, Base):
    __tablename__ = 'customer'
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    rule_key: Mapped[str] = mapped_column(String(80), default='standard')
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Mold(IdentityMixin, Base):
    __tablename__ = 'mold'
    internal_number: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(30), default='ACTIVE')


class ProjectMold(IdentityMixin, Base):
    __tablename__ = 'project_mold'
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'))
    mold_id: Mapped[str] = mapped_column(ForeignKey('mold.id'))
    __table_args__ = (UniqueConstraint('project_id','mold_id'),)


class ProjectProfile(Base):
    __tablename__ = 'project_profile'
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), primary_key=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey('customer.id'))
    owner_user_id: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    execution_mode: Mapped[str] = mapped_column(String(30), default='INTERNAL')
    customer_due_date: Mapped[date | None] = mapped_column(Date)
    settlement_status: Mapped[str] = mapped_column(String(30), default='OPEN')
    __table_args__ = (CheckConstraint("execution_mode IN ('INTERNAL','FULL_OUTSOURCE')"),)


class ProjectRoleConfig(Base):
    """Version boundary for project-scoped workflow role membership."""
    __tablename__ = 'project_role_config'
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class ProjectRoleMember(IdentityMixin, Base):
    """A BPM selector only; membership never grants business permissions."""
    __tablename__ = 'project_role_member'
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), index=True)
    role_key: Mapped[str] = mapped_column(String(60), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('app_user.id'), index=True)
    __table_args__ = (
        UniqueConstraint('project_id', 'role_key', 'user_id'),
        Index('ix_project_role_member_lookup', 'project_id', 'role_key'),
    )


class Warehouse(IdentityMixin, Base):
    __tablename__ = 'warehouse'
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    scope_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    opening_evidence: Mapped[str | None] = mapped_column(Text)


class PurchaseOrder(IdentityMixin, Base):
    __tablename__ = 'purchase_order'
    request_id: Mapped[str] = mapped_column(ForeignKey('purchase_request.id'), unique=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), index=True)
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey('supplier.id'))
    number: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(30), default='DRAFT')
    currency: Mapped[str] = mapped_column(String(3), default='CNY')
    version: Mapped[int] = mapped_column(Integer, default=1)
    issued_by: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("status IN ('DRAFT','ISSUED','CLOSED','CANCELLED')"),)


class OrderLine(IdentityMixin, Base):
    __tablename__ = 'purchase_order_line'
    order_id: Mapped[str] = mapped_column(ForeignKey('purchase_order.id'), index=True)
    source_line_id: Mapped[str] = mapped_column(ForeignKey('purchase_request_line.id'), unique=True)
    material_id: Mapped[str] = mapped_column(ForeignKey('material.id'))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18,6))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18,6))
    agreed_ship_date: Mapped[date] = mapped_column(Date)
    __table_args__ = (CheckConstraint('quantity > 0 AND (unit_price IS NULL OR unit_price >= 0)'),)


class SupplierShipment(IdentityMixin, Base):
    __tablename__ = 'supplier_shipment'
    order_line_id: Mapped[str] = mapped_column(ForeignKey('purchase_order_line.id'), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18,6))
    shipped_date: Mapped[date] = mapped_column(Date)
    reference: Mapped[str] = mapped_column(String(150))
    evidence: Mapped[str] = mapped_column(Text)
    confirmed_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    __table_args__ = (UniqueConstraint('order_line_id','reference'), CheckConstraint('quantity > 0'))


class DeliveryException(IdentityMixin, Base):
    __tablename__ = 'delivery_exception'
    order_line_id: Mapped[str] = mapped_column(ForeignKey('purchase_order_line.id'), index=True)
    reason: Mapped[str] = mapped_column(Text)
    expected_ship_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30), default='OPEN')
    reported_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    evidence: Mapped[str] = mapped_column(Text)
    closed_by: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    resolution: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("status IN ('OPEN','CLOSED')"),)


class GoodsReceipt(IdentityMixin, Base):
    __tablename__ = 'goods_receipt'
    shipment_id: Mapped[str] = mapped_column(ForeignKey('supplier_shipment.id'), index=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey('warehouse.id'))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18,6))
    reference: Mapped[str] = mapped_column(String(150))
    received_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    evidence: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint('shipment_id','reference'), CheckConstraint('quantity > 0'))


class LogisticsRoute(IdentityMixin, Base):
    """Approved route master data for delivery/logistics price matching."""
    __tablename__ = 'logistics_route'
    project_id: Mapped[str | None] = mapped_column(ForeignKey('project.id'), index=True)
    route_code: Mapped[str] = mapped_column(String(80), unique=True)
    origin: Mapped[str] = mapped_column(String(200))
    destination: Mapped[str] = mapped_column(String(200))
    carrier_name: Mapped[str] = mapped_column(String(150))
    vehicle_type: Mapped[str] = mapped_column(String(80))
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))
    transport_mode: Mapped[str] = mapped_column(String(40), default='TRUCK')
    price_unit: Mapped[str] = mapped_column(String(40))
    tax_mode: Mapped[str] = mapped_column(String(30), default='TAX_INCLUDED')
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    evidence: Mapped[str] = mapped_column(Text, default='')
    source_ref: Mapped[str | None] = mapped_column(String(120))
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("transport_mode IN ('TRUCK','EXPRESS','SEA','AIR','RAIL','OTHER')", name='logistics_route_transport_mode'),
        CheckConstraint("tax_mode IN ('TAX_INCLUDED','TAX_EXCLUDED','UNKNOWN')", name='logistics_route_tax_mode'),
        CheckConstraint('weight_kg IS NULL OR weight_kg > 0', name='logistics_route_positive_weight'),
        CheckConstraint('valid_from IS NULL OR valid_to IS NULL OR valid_to >= valid_from', name='logistics_route_valid_range'),
        UniqueConstraint('source_ref', name='logistics_route_unique_source'),
    )


class LogisticsQuote(IdentityMixin, Base):
    """Versioned logistics quote; settlement price is explicit, not inferred."""
    __tablename__ = 'logistics_quote'
    route_id: Mapped[str] = mapped_column(ForeignKey('logistics_route.id'), index=True)
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey('supplier.id'))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18,2))
    currency: Mapped[str] = mapped_column(String(3), default='CNY')
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30), default='EFFECTIVE')
    settlement_for_project_id: Mapped[str | None] = mapped_column(ForeignKey('project.id'), index=True)
    quote_evidence: Mapped[str] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pricing_method: Mapped[str] = mapped_column(String(30), default='LEGACY')
    comparison_count: Mapped[int] = mapped_column(Integer, default=0)
    comparison_summary: Mapped[str | None] = mapped_column(Text)
    reconciliation_basis: Mapped[str | None] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    supersedes_quote_id: Mapped[str | None] = mapped_column(ForeignKey('logistics_quote.id'))
    __table_args__ = (
        CheckConstraint('unit_price >= 0', name='logistics_quote_nonnegative_price'),
        CheckConstraint('valid_to >= valid_from', name='logistics_quote_valid_range'),
        CheckConstraint("status IN ('DRAFT','SUBMITTED','EFFECTIVE','EXPIRED','CANCELLED')", name='logistics_quote_status'),
        CheckConstraint("pricing_method IN ('LEGACY','FIXED_ROUTE','COMPETITIVE','NEGOTIATED','SINGLE_SOURCE')", name='logistics_quote_pricing_method'),
        CheckConstraint('comparison_count >= 0', name='logistics_quote_comparison_count_nonnegative'),
        UniqueConstraint('route_id','source_ref', name='logistics_quote_unique_source'),
    )


class CustomerDeliverySignature(IdentityMixin, Base):
    """Customer receipt/signature evidence after shipment; not quality acceptance."""
    __tablename__ = 'customer_delivery_signature'
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), index=True)
    logistics_route_id: Mapped[str | None] = mapped_column(ForeignKey('logistics_route.id'))
    shipment_reference: Mapped[str] = mapped_column(String(120))
    signed_date: Mapped[date] = mapped_column(Date)
    signer_name: Mapped[str] = mapped_column(String(120))
    sign_status: Mapped[str] = mapped_column(String(30), default='SIGNED')
    move_type: Mapped[str] = mapped_column(String(40), default='DELIVERY')
    evidence: Mapped[str] = mapped_column(Text)
    recorded_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    __table_args__ = (
        UniqueConstraint('project_id','shipment_reference','signed_date', name='customer_delivery_signature_unique_ref_date'),
        CheckConstraint("sign_status IN ('SIGNED','REJECTED','PENDING')", name='customer_delivery_signature_status'),
        CheckConstraint("move_type IN ('DELIVERY','MOLD_TRANSFER','RETURN','OTHER')", name='customer_delivery_signature_move_type'),
    )


class CustomerAcceptanceRecord(IdentityMixin, Base):
    """Customer acceptance, rejection and recheck evidence; failures may carry deductions."""
    __tablename__ = 'customer_acceptance_record'
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), index=True)
    signature_id: Mapped[str | None] = mapped_column(ForeignKey('customer_delivery_signature.id'), index=True)
    acceptance_type: Mapped[str] = mapped_column(String(30), default='INITIAL')
    result: Mapped[str] = mapped_column(String(30))
    accepted_date: Mapped[date] = mapped_column(Date)
    issue_description: Mapped[str] = mapped_column(Text, default='')
    responsibility: Mapped[str] = mapped_column(String(40), default='UNKNOWN')
    corrective_due_date: Mapped[date | None] = mapped_column(Date)
    contact_case_id: Mapped[str | None] = mapped_column(ForeignKey('contact_case.id'), index=True)
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey('supplier.id'))
    deduction_amount: Mapped[Decimal | None] = mapped_column(Numeric(18,2))
    currency: Mapped[str | None] = mapped_column(String(3))
    schedule_impact_days: Mapped[int] = mapped_column(Integer, default=0)
    contract_change_required: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[str] = mapped_column(Text)
    confirmed_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    __table_args__ = (
        CheckConstraint("acceptance_type IN ('INITIAL','RECHECK')", name='customer_acceptance_type'),
        CheckConstraint("result IN ('PASSED','FAILED','CONDITIONALLY_PASSED')", name='customer_acceptance_result'),
        CheckConstraint("responsibility IN ('CUSTOMER','SUPPLIER','INTERNAL','SHARED','UNKNOWN')", name='customer_acceptance_responsibility'),
        CheckConstraint('deduction_amount IS NULL OR deduction_amount >= 0', name='customer_acceptance_deduction_nonnegative'),
        CheckConstraint('schedule_impact_days >= 0', name='customer_acceptance_schedule_impact_nonnegative'),
    )


class SupplierProgressReport(IdentityMixin, Base):
    """Authorized supplier node progress report/import for full-outsource collaboration."""
    __tablename__ = 'supplier_progress_report'
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey('supplier.id'), index=True)
    contract_subject_id: Mapped[str | None] = mapped_column(ForeignKey('business_subject.id'), index=True)
    plan_task_id: Mapped[str | None] = mapped_column(ForeignKey('plan_task.id'), index=True)
    stage_key: Mapped[str] = mapped_column(String(80))
    stage_name: Mapped[str] = mapped_column(String(150))
    report_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30))
    progress_percent: Mapped[int | None] = mapped_column(Integer)
    next_due_date: Mapped[date | None] = mapped_column(Date)
    issue_summary: Mapped[str] = mapped_column(Text, default='')
    evidence: Mapped[str] = mapped_column(Text)
    evidence_items: Mapped[list] = mapped_column(J, default=list)
    source_system: Mapped[str] = mapped_column(String(20), default='MANUAL')
    source_ref: Mapped[str | None] = mapped_column(String(120))
    reported_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    followed_by: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    __table_args__ = (
        UniqueConstraint('project_id','supplier_id','stage_key','report_date','source_ref', name='supplier_progress_report_unique_source'),
        CheckConstraint("status IN ('ON_TRACK','AT_RISK','BLOCKED','DONE','REWORK')", name='supplier_progress_report_status'),
        CheckConstraint('progress_percent IS NULL OR progress_percent BETWEEN 0 AND 100', name='supplier_progress_report_progress_range'),
        CheckConstraint("source_system IN ('MANUAL','IMPORT','ERP')", name='supplier_progress_report_source_system'),
    )


class SupplierProgressPolicy(IdentityMixin, Base):
    """Versioned reporting cadence and evidence contract for one supplier stage."""
    __tablename__ = 'supplier_progress_policy'
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey('supplier.id'), index=True)
    contract_subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), index=True)
    plan_task_id: Mapped[str | None] = mapped_column(ForeignKey('plan_task.id'), index=True)
    stage_key: Mapped[str] = mapped_column(String(80))
    stage_name: Mapped[str] = mapped_column(String(150))
    frequency_days: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[date] = mapped_column(Date)
    first_due_date: Mapped[date] = mapped_column(Date)
    evidence_requirements: Mapped[list] = mapped_column(J, default=list)
    basis: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey('supplier_progress_policy.id'))
    created_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    __table_args__ = (
        UniqueConstraint('project_id','supplier_id','contract_subject_id','stage_key','version', name='supplier_progress_policy_version'),
        UniqueConstraint('project_id','supplier_id','contract_subject_id','stage_key','source_ref', name='supplier_progress_policy_unique_source'),
        CheckConstraint('frequency_days BETWEEN 1 AND 90', name='supplier_progress_policy_frequency'),
        CheckConstraint('version >= 1', name='supplier_progress_policy_version_positive'),
    )


class SupplierMaterialHandoff(IdentityMixin, Base):
    """Customer/design material handoff evidence from project/design/purchase to supplier."""
    __tablename__ = 'supplier_material_handoff'
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey('supplier.id'), index=True)
    contract_subject_id: Mapped[str | None] = mapped_column(ForeignKey('business_subject.id'), index=True)
    file_id: Mapped[str | None] = mapped_column(ForeignKey('file_object.id'), index=True)
    document_title: Mapped[str] = mapped_column(String(200))
    document_type: Mapped[str] = mapped_column(String(40), default='CUSTOMER_MATERIAL')
    approval_status: Mapped[str] = mapped_column(String(30), default='APPROVED')
    provided_date: Mapped[date] = mapped_column(Date)
    provided_to: Mapped[str] = mapped_column(String(150))
    handoff_channel: Mapped[str] = mapped_column(String(40), default='MANUAL')
    evidence: Mapped[str] = mapped_column(Text)
    source_system: Mapped[str] = mapped_column(String(20), default='MANUAL')
    source_ref: Mapped[str | None] = mapped_column(String(120))
    provided_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    verified_by: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    __table_args__ = (
        UniqueConstraint('project_id','supplier_id','document_title','provided_date','source_ref', name='supplier_material_handoff_unique_source'),
        CheckConstraint("document_type IN ('CUSTOMER_MATERIAL','DESIGN_DRAWING','TECHNICAL_SPEC','QUALITY_STANDARD','OTHER')", name='supplier_material_handoff_document_type'),
        CheckConstraint("approval_status IN ('DRAFT','APPROVED','REVOKED')", name='supplier_material_handoff_approval_status'),
        CheckConstraint("handoff_channel IN ('MANUAL','EMAIL','IMPORT','ERP','OTHER')", name='supplier_material_handoff_channel'),
        CheckConstraint("source_system IN ('MANUAL','IMPORT','ERP')", name='supplier_material_handoff_source_system'),
    )


class SupplierMaterialVerification(IdentityMixin, Base):
    """Append-only supplier response to one approved material handoff."""
    __tablename__ = 'supplier_material_verification'
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey('supplier.id'), index=True)
    contract_subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), index=True)
    handoff_id: Mapped[str] = mapped_column(ForeignKey('supplier_material_handoff.id'), index=True)
    response_file_id: Mapped[str | None] = mapped_column(ForeignKey('file_object.id'), index=True)
    response_date: Mapped[date] = mapped_column(Date)
    result: Mapped[str] = mapped_column(String(30))
    supplier_contact: Mapped[str] = mapped_column(String(150))
    response_channel: Mapped[str] = mapped_column(String(40), default='MANUAL')
    response_summary: Mapped[str] = mapped_column(Text, default='')
    follow_up_due_date: Mapped[date | None] = mapped_column(Date)
    evidence: Mapped[str] = mapped_column(Text)
    source_system: Mapped[str] = mapped_column(String(20), default='MANUAL')
    source_ref: Mapped[str] = mapped_column(String(120))
    recorded_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    __table_args__ = (
        UniqueConstraint('handoff_id','source_ref', name='supplier_material_verification_unique_source'),
        CheckConstraint("result IN ('RECEIVED','ACCEPTED','NEEDS_CLARIFICATION','REJECTED')", name='supplier_material_verification_result'),
        CheckConstraint("response_channel IN ('MANUAL','EMAIL','IMPORT','ERP','OTHER')", name='supplier_material_verification_channel'),
        CheckConstraint("source_system IN ('MANUAL','IMPORT','ERP')", name='supplier_material_verification_source_system'),
    )


class ReceiptInspection(IdentityMixin, Base):
    __tablename__ = 'receipt_inspection'
    receipt_id: Mapped[str] = mapped_column(ForeignKey('goods_receipt.id'), unique=True)
    accepted_quantity: Mapped[Decimal] = mapped_column(Numeric(18,6))
    rejected_quantity: Mapped[Decimal] = mapped_column(Numeric(18,6))
    inspector_id: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    evidence: Mapped[str] = mapped_column(Text)
    __table_args__ = (CheckConstraint('accepted_quantity >= 0 AND rejected_quantity >= 0'),)


class StockBalance(IdentityMixin, Base):
    __tablename__ = 'stock_balance'
    warehouse_id: Mapped[str] = mapped_column(ForeignKey('warehouse.id'))
    material_id: Mapped[str] = mapped_column(ForeignKey('material.id'))
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18,6), default=0)
    __table_args__ = (UniqueConstraint('warehouse_id','material_id','project_id'), CheckConstraint('quantity >= 0'))


class StockMovement(IdentityMixin, Base):
    __tablename__ = 'stock_movement'
    balance_id: Mapped[str] = mapped_column(ForeignKey('stock_balance.id'), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18,6))
    kind: Mapped[str] = mapped_column(String(30))
    source_key: Mapped[str] = mapped_column(String(150), unique=True)
    confirmed_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    evidence: Mapped[str] = mapped_column(Text)
    __table_args__ = (CheckConstraint('quantity <> 0'),)


class ContractDetail(Base):
    __tablename__ = 'contract_detail'
    subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), primary_key=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey('customer.id'))
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey('supplier.id'))
    amount: Mapped[Decimal] = mapped_column(Numeric(18,2))
    currency: Mapped[str] = mapped_column(String(3))
    contract_number: Mapped[str] = mapped_column(String(100))
    expected_date: Mapped[date | None] = mapped_column(Date)
    replaces_id: Mapped[str | None] = mapped_column(ForeignKey('business_subject.id'))
    __table_args__ = (CheckConstraint('amount > 0'),)


class ContractSigningRecord(IdentityMixin, Base):
    """Manual/template-based contract signing evidence; does not imply e-sign integration."""
    __tablename__ = 'contract_signing_record'
    contract_subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), index=True)
    template_name: Mapped[str] = mapped_column(String(150), default='')
    signing_method: Mapped[str] = mapped_column(String(40), default='MANUAL')
    status: Mapped[str] = mapped_column(String(30), default='SIGNED')
    signed_date: Mapped[date | None] = mapped_column(Date)
    signed_file_id: Mapped[str | None] = mapped_column(ForeignKey('file_object.id'), index=True)
    signed_file_title: Mapped[str] = mapped_column(String(200), default='')
    supplier_signer: Mapped[str] = mapped_column(String(120), default='')
    buyer_reviewer_id: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    approved_by: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    evidence: Mapped[str] = mapped_column(Text)
    source_system: Mapped[str] = mapped_column(String(20), default='MANUAL')
    source_ref: Mapped[str | None] = mapped_column(String(120))
    recorded_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    __table_args__ = (
        UniqueConstraint('contract_subject_id','status','source_ref', name='contract_signing_record_unique_source'),
        CheckConstraint("signing_method IN ('MANUAL','OFFLINE_FILE','IMPORT','ERP','OTHER')", name='contract_signing_method'),
        CheckConstraint("status IN ('DRAFT','UNDER_REVIEW','SIGNED','REJECTED','CANCELLED')", name='contract_signing_status'),
        CheckConstraint("source_system IN ('MANUAL','IMPORT','ERP')", name='contract_signing_source_system'),
    )


class PaymentStage(IdentityMixin, Base):
    __tablename__ = 'payment_stage'
    contract_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'))
    name: Mapped[str] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(Numeric(18,2))
    currency: Mapped[str] = mapped_column(String(3))
    condition: Mapped[str] = mapped_column(Text)
    condition_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    condition_evidence: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint('amount > 0'),)


class PaymentRequestDetail(Base):
    __tablename__ = 'payment_request_detail'
    subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), primary_key=True)
    stage_id: Mapped[str] = mapped_column(ForeignKey('payment_stage.id'))
    amount: Mapped[Decimal] = mapped_column(Numeric(18,2))
    currency: Mapped[str] = mapped_column(String(3))
    reservation: Mapped[Decimal] = mapped_column(Numeric(18,2), default=0)
    __table_args__ = (CheckConstraint('amount > 0 AND reservation >= 0 AND reservation <= amount'),)


class PaymentConfirmation(IdentityMixin, Base):
    __tablename__ = 'payment_confirmation'
    request_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18,2))
    currency: Mapped[str] = mapped_column(String(3))
    paid_date: Mapped[date] = mapped_column(Date)
    reference: Mapped[str] = mapped_column(String(100), unique=True)
    evidence: Mapped[str] = mapped_column(Text)
    confirmed_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    reversal_of_id: Mapped[str | None] = mapped_column(ForeignKey('payment_confirmation.id'), unique=True)
    __table_args__ = (CheckConstraint('amount <> 0'),)


class CustomerReceiptConfirmation(IdentityMixin, Base):
    """Finance-confirmed customer receipt evidence.

    Contract payment stages are receivable conditions. This table is the
    separate actual cash receipt ledger confirmed by finance, so reminders or
    closure checklist text cannot be mistaken for money received.
    """
    __tablename__ = 'customer_receipt_confirmation'
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), index=True)
    contract_subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), index=True)
    stage_id: Mapped[str | None] = mapped_column(ForeignKey('payment_stage.id'), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18,2))
    currency: Mapped[str] = mapped_column(String(3))
    received_date: Mapped[date] = mapped_column(Date)
    reference: Mapped[str] = mapped_column(String(100), unique=True)
    evidence: Mapped[str] = mapped_column(Text)
    confirmed_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    source_system: Mapped[str] = mapped_column(String(20), default='MANUAL')
    source_ref: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint('amount > 0', name='customer_receipt_amount_positive'),
        CheckConstraint("source_system IN ('MANUAL','IMPORT','ERP')", name='customer_receipt_source_system'),
        UniqueConstraint('project_id','source_system','source_ref', name='customer_receipt_unique_source'),
    )


class SupplierDeductionSettlement(IdentityMixin, Base):
    """Responsibility-confirmed supplier deduction/settlement evidence for quality or delay."""
    __tablename__ = 'supplier_deduction_settlement'
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey('supplier.id'), index=True)
    contract_subject_id: Mapped[str | None] = mapped_column(ForeignKey('business_subject.id'), index=True)
    contact_case_id: Mapped[str | None] = mapped_column(ForeignKey('contact_case.id'), index=True)
    contact_task_id: Mapped[str | None] = mapped_column(ForeignKey('contact_task.id'), index=True)
    reason: Mapped[str] = mapped_column(Text)
    responsibility: Mapped[str] = mapped_column(String(40), default='UNKNOWN')
    deduction_amount: Mapped[Decimal] = mapped_column(Numeric(18,2))
    currency: Mapped[str] = mapped_column(String(3), default='CNY')
    status: Mapped[str] = mapped_column(String(30), default='PROPOSED')
    settlement_reference: Mapped[str | None] = mapped_column(String(120))
    responsibility_evidence: Mapped[str] = mapped_column(Text)
    settlement_evidence: Mapped[str] = mapped_column(Text, default='')
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    settled_by: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_system: Mapped[str] = mapped_column(String(20), default='MANUAL')
    source_ref: Mapped[str | None] = mapped_column(String(120))
    __table_args__ = (
        UniqueConstraint('project_id','supplier_id','reason','source_ref', name='supplier_deduction_settlement_unique_source'),
        CheckConstraint("responsibility IN ('CUSTOMER','SUPPLIER','INTERNAL','SHARED','UNKNOWN')", name='supplier_deduction_responsibility'),
        CheckConstraint("status IN ('PROPOSED','RESPONSIBILITY_CONFIRMED','SETTLED','CANCELLED')", name='supplier_deduction_status'),
        CheckConstraint('deduction_amount >= 0', name='supplier_deduction_nonnegative'),
        CheckConstraint("source_system IN ('MANUAL','IMPORT','ERP')", name='supplier_deduction_source_system'),
    )


class OutsourceChangeNegotiation(IdentityMixin, Base):
    """Negotiation evidence for outsource changes: cost, schedule and task impact."""
    __tablename__ = 'outsource_change_negotiation'
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey('supplier.id'), index=True)
    contract_subject_id: Mapped[str | None] = mapped_column(ForeignKey('business_subject.id'), index=True)
    contact_case_id: Mapped[str | None] = mapped_column(ForeignKey('contact_case.id'), index=True)
    contact_task_id: Mapped[str | None] = mapped_column(ForeignKey('contact_task.id'), index=True)
    customer_quote_amount: Mapped[Decimal | None] = mapped_column(Numeric(18,2))
    supplier_quote_amount: Mapped[Decimal | None] = mapped_column(Numeric(18,2))
    negotiated_amount: Mapped[Decimal | None] = mapped_column(Numeric(18,2))
    currency: Mapped[str] = mapped_column(String(3), default='CNY')
    schedule_impact_days: Mapped[int] = mapped_column(Integer, default=0)
    task_impact_summary: Mapped[str] = mapped_column(Text, default='')
    requires_contract_change: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default='DRAFT')
    customer_evidence: Mapped[str] = mapped_column(Text, default='')
    supplier_evidence: Mapped[str] = mapped_column(Text, default='')
    negotiation_evidence: Mapped[str] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    source_system: Mapped[str] = mapped_column(String(20), default='MANUAL')
    source_ref: Mapped[str | None] = mapped_column(String(120))
    __table_args__ = (
        UniqueConstraint('project_id','supplier_id','source_ref', name='outsource_change_negotiation_unique_source'),
        CheckConstraint('customer_quote_amount IS NULL OR customer_quote_amount >= 0', name='outsource_change_customer_amount_nonnegative'),
        CheckConstraint('supplier_quote_amount IS NULL OR supplier_quote_amount >= 0', name='outsource_change_supplier_amount_nonnegative'),
        CheckConstraint('negotiated_amount IS NULL OR negotiated_amount >= 0', name='outsource_change_negotiated_amount_nonnegative'),
        CheckConstraint('schedule_impact_days >= 0', name='outsource_change_schedule_impact_nonnegative'),
        CheckConstraint("status IN ('DRAFT','NEGOTIATING','AGREED','APPROVED','CANCELLED')", name='outsource_change_negotiation_status'),
        CheckConstraint("source_system IN ('MANUAL','IMPORT','ERP')", name='outsource_change_negotiation_source_system'),
    )


class PlanDetail(Base):
    __tablename__ = 'plan_detail'
    subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), primary_key=True)
    previous_id: Mapped[str | None] = mapped_column(ForeignKey('business_subject.id'))
    reason: Mapped[str] = mapped_column(Text)


class PlanTask(IdentityMixin, Base):
    __tablename__ = 'plan_task'
    plan_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), index=True)
    key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(150))
    owner_user_id: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    planned_start: Mapped[date] = mapped_column(Date)
    planned_end: Mapped[date] = mapped_column(Date)
    actual_start: Mapped[date | None] = mapped_column(Date)
    actual_end: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30), default='PLANNED')
    __table_args__ = (UniqueConstraint('plan_id','key'), CheckConstraint('planned_end >= planned_start'))


class TaskDependency(Base):
    __tablename__ = 'task_dependency'
    task_id: Mapped[str] = mapped_column(ForeignKey('plan_task.id'), primary_key=True)
    prerequisite_id: Mapped[str] = mapped_column(ForeignKey('plan_task.id'), primary_key=True)
    __table_args__ = (CheckConstraint('task_id <> prerequisite_id'),)


class PlanDepartmentConfirmation(IdentityMixin, Base):
    __tablename__ = 'plan_department_confirmation'
    plan_change_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), index=True)
    department: Mapped[str] = mapped_column(String(100))
    assigned_user_ids: Mapped[list] = mapped_column(J, default=list)
    task_keys: Mapped[list] = mapped_column(J, default=list)
    change_types: Mapped[list] = mapped_column(J, default=list)
    status: Mapped[str] = mapped_column(String(30), default='PENDING')
    version: Mapped[int] = mapped_column(Integer, default=1)
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint('plan_change_id','department'),
                      CheckConstraint("status IN ('PENDING','CONFIRMED')", name='plan_department_confirmation_status'))


class BusinessDecisionDetail(Base):
    """Typed decision/notice fields shared by simple human control documents."""
    __tablename__ = 'business_decision_detail'
    subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), primary_key=True)
    source_subject_id: Mapped[str | None] = mapped_column(ForeignKey('business_subject.id'))
    decision: Mapped[str] = mapped_column(String(40))
    execution_mode: Mapped[str | None] = mapped_column(String(30))
    effective_date: Mapped[date] = mapped_column(Date)
    evidence: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18,2))
    currency: Mapped[str | None] = mapped_column(String(3))


class PauseRecord(IdentityMixin, Base):
    __tablename__ = 'pause_record'
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), unique=True)
    resume_subject_id: Mapped[str | None] = mapped_column(ForeignKey('business_subject.id'), unique=True)
    shifted_days: Mapped[int] = mapped_column(Integer, default=0)
    shift_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    customer_due_date_snapshot: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (
        Index(
            'uq_pause_record_open_project', 'project_id', unique=True,
            postgresql_where=text('end_date IS NULL'),
        ),
    )


class ProjectPauseDetail(Base):
    """Approval material for a whole-project pause or resume.

    The task snapshot is frozen when the draft is created so approval cannot
    silently apply to a different plan scope later.
    """
    __tablename__ = 'project_pause_detail'
    subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), primary_key=True)
    decision: Mapped[str] = mapped_column(String(20))
    effective_date: Mapped[date] = mapped_column(Date)
    expected_resume_date: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text)
    source_pause_subject_id: Mapped[str | None] = mapped_column(ForeignKey('business_subject.id'))
    plan_subject_id: Mapped[str | None] = mapped_column(ForeignKey('business_subject.id'))
    task_snapshot: Mapped[list] = mapped_column(J, default=list)
    customer_due_date_snapshot: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (CheckConstraint("decision IN ('PAUSE','RESUME')", name='project_pause_decision'),)


class PauseTaskShift(IdentityMixin, Base):
    """Immutable before/after evidence for one resume date adjustment."""
    __tablename__ = 'pause_task_shift'
    pause_id: Mapped[str] = mapped_column(ForeignKey('pause_record.id'), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey('plan_task.id'), index=True)
    previous_start: Mapped[date] = mapped_column(Date)
    previous_end: Mapped[date] = mapped_column(Date)
    shifted_start: Mapped[date] = mapped_column(Date)
    shifted_end: Mapped[date] = mapped_column(Date)
    shifted_days: Mapped[int] = mapped_column(Integer)
    task_status: Mapped[str] = mapped_column(String(30))
    __table_args__ = (UniqueConstraint('pause_id','task_id'), CheckConstraint('shifted_days >= 0'))


class ProjectClosureCase(IdentityMixin, Base):
    """Durable normal-close or termination-settlement checklist."""
    __tablename__ = 'project_closure_case'
    project_id: Mapped[str] = mapped_column(ForeignKey('project.id'), index=True)
    mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default='OPEN')
    current_stage: Mapped[str] = mapped_column(String(200))
    opened_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    source_termination_subject_id: Mapped[str | None] = mapped_column(ForeignKey('business_subject.id'), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    closed_by: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("mode IN ('NORMAL','TERMINATION')", name='project_closure_mode'),
        CheckConstraint("status IN ('OPEN','CLOSED','CANCELLED')", name='project_closure_status'),
        Index(
            'uq_project_closure_open_project', 'project_id', unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
    )


class ProjectClosureItem(IdentityMixin, Base):
    __tablename__ = 'project_closure_item'
    case_id: Mapped[str] = mapped_column(ForeignKey('project_closure_case.id'), index=True)
    item_key: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default='PENDING')
    allow_not_applicable: Mapped[bool] = mapped_column(Boolean, default=False)
    system_managed: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[str] = mapped_column(Text, default='')
    evidence: Mapped[str] = mapped_column(Text, default='')
    source_system: Mapped[str] = mapped_column(String(20), default='MANUAL')
    source_ref: Mapped[str | None] = mapped_column(String(300))
    source_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        UniqueConstraint('case_id','item_key'),
        CheckConstraint("status IN ('PENDING','DONE','NOT_APPLICABLE')", name='project_closure_item_status'),
        CheckConstraint("source_system IN ('AGENT','ERP','MANUAL')", name='project_closure_item_source'),
    )


class ProjectClosureItemRevision(IdentityMixin, Base):
    """Append-only evidence for checklist changes; corrections never erase history."""
    __tablename__ = 'project_closure_item_revision'
    item_id: Mapped[str] = mapped_column(ForeignKey('project_closure_item.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30))
    result: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text)
    source_system: Mapped[str] = mapped_column(String(20))
    source_ref: Mapped[str | None] = mapped_column(String(300))
    source_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    changed_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    __table_args__ = (UniqueConstraint('item_id','revision'),)


class ProjectClosureDetail(Base):
    __tablename__ = 'project_closure_detail'
    subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), primary_key=True)
    decision: Mapped[str] = mapped_column(String(30))
    effective_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text)
    project_version: Mapped[int] = mapped_column(Integer)
    closure_case_id: Mapped[str | None] = mapped_column(ForeignKey('project_closure_case.id'))
    closure_case_version: Mapped[int | None] = mapped_column(Integer)
    current_stage: Mapped[str | None] = mapped_column(String(200))
    completed_work_summary: Mapped[str | None] = mapped_column(Text)
    incurred_cost_summary: Mapped[str | None] = mapped_column(Text)
    incurred_cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(18,2))
    currency: Mapped[str | None] = mapped_column(String(3))
    __table_args__ = (
        CheckConstraint("decision IN ('TERMINATE','NORMAL_CLOSE','SETTLEMENT_CLOSE')", name='project_closure_decision'),
        CheckConstraint('incurred_cost_amount IS NULL OR incurred_cost_amount >= 0', name='project_closure_cost'),
    )


class EngineeringChangeDetail(Base):
    __tablename__ = 'engineering_change_detail'
    subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), primary_key=True)
    problem: Mapped[str] = mapped_column(Text)
    solution: Mapped[str] = mapped_column(Text)
    customer_due_affected: Mapped[bool] = mapped_column(Boolean, default=False)
    customer_evidence: Mapped[str | None] = mapped_column(Text)


class ChangeImpact(IdentityMixin, Base):
    __tablename__ = 'change_impact'
    change_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey('plan_task.id'))
    action: Mapped[str] = mapped_column(String(30))
    implemented_by: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    implementation_evidence: Mapped[str | None] = mapped_column(Text)
    rechecked_by: Mapped[str | None] = mapped_column(ForeignKey('app_user.id'))
    recheck_passed: Mapped[bool | None] = mapped_column(Boolean)
    __table_args__ = (UniqueConstraint('change_id','task_id'), CheckConstraint("action IN ('KEEP','PAUSE','CANCEL','REWORK')"))


class RiskPolicy(IdentityMixin, Base):
    __tablename__ = 'risk_policy'
    version: Mapped[int] = mapped_column(Integer, unique=True)
    near_due_days: Mapped[int] = mapped_column(Integer)
    configured_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    __table_args__ = (CheckConstraint('near_due_days BETWEEN 0 AND 90'),)


class RiskAnalysis(IdentityMixin, Base):
    __tablename__ = 'risk_analysis_result'
    user_id: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    authorization_hash: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[int] = mapped_column(Integer)
    trigger_type: Mapped[str] = mapped_column(String(20), default='USER_REQUEST')
    findings: Mapped[list] = mapped_column(J)
    limitations: Mapped[list] = mapped_column(J)


class DesignDetail(Base):
    __tablename__='design_detail'
    subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), primary_key=True)
    design_type: Mapped[str | None] = mapped_column(String(30))
    drawing_revision: Mapped[str] = mapped_column(String(100))
    drawing_evidence: Mapped[str] = mapped_column(Text)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey('app_user.id'))


class DesignItem(IdentityMixin, Base):
    __tablename__='design_item'
    design_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey('material.id'))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18,6))
    route: Mapped[str] = mapped_column(String(30))
    task_id: Mapped[str | None] = mapped_column(ForeignKey('plan_task.id'))
    __table_args__=(UniqueConstraint('design_id','material_id'), CheckConstraint('quantity > 0'),
                   CheckConstraint("route IN ('INTERNAL','PURCHASE','OUTSOURCE')"))


class PriceDetail(Base):
    __tablename__='price_detail'
    subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey('supplier.id'))
    material_id: Mapped[str] = mapped_column(ForeignKey('material.id'))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18,6))
    currency: Mapped[str] = mapped_column(String(3))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    quote_evidence: Mapped[str] = mapped_column(Text)
    __table_args__=(CheckConstraint('unit_price >= 0'),CheckConstraint('valid_to >= valid_from'))


class OrderPriceSnapshot(Base):
    __tablename__='order_price_snapshot'
    line_id: Mapped[str] = mapped_column(ForeignKey('purchase_order_line.id'), primary_key=True)
    price_subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18,6))
    currency: Mapped[str] = mapped_column(String(3))
    selected_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))


class AssemblyDetail(Base):
    __tablename__='assembly_detail'
    subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), primary_key=True)
    design_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'))
    supervisor_id: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    prerequisites_evidence: Mapped[str] = mapped_column(Text)
    planned_date: Mapped[date] = mapped_column(Date)
    execution_status: Mapped[str] = mapped_column(String(30), default='NOT_STARTED')
    __table_args__=(CheckConstraint("execution_status IN ('NOT_STARTED','RUNNING','DONE')"),)


class AssemblyExecution(IdentityMixin, Base):
    __tablename__='assembly_execution'
    assembly_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'))
    action: Mapped[str] = mapped_column(String(20))
    actual_date: Mapped[date] = mapped_column(Date)
    evidence: Mapped[str] = mapped_column(Text)
    confirmed_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    __table_args__=(UniqueConstraint('assembly_id','action'),CheckConstraint("action IN ('START','DONE')"))


class TrialDetail(Base):
    __tablename__='trial_detail'
    subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), primary_key=True)
    assembly_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'))
    planned_date: Mapped[date] = mapped_column(Date)
    location: Mapped[str] = mapped_column(String(200))
    acceptance_criteria: Mapped[str] = mapped_column(Text)
    responsible_id: Mapped[str] = mapped_column(ForeignKey('app_user.id'))


class TrialResult(IdentityMixin, Base):
    __tablename__='trial_result'
    trial_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), unique=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    actual_date: Mapped[date] = mapped_column(Date)
    evidence: Mapped[str] = mapped_column(Text)
    findings: Mapped[str] = mapped_column(Text)
    change_id: Mapped[str | None] = mapped_column(ForeignKey('business_subject.id'))
    confirmed_by: Mapped[str] = mapped_column(ForeignKey('app_user.id'))


class FinanceCorrectionDetail(Base):
    __tablename__='finance_correction_detail'
    subject_id: Mapped[str] = mapped_column(ForeignKey('business_subject.id'), primary_key=True)
    original_payment_id: Mapped[str] = mapped_column(ForeignKey('payment_confirmation.id'))
    reason: Mapped[str] = mapped_column(Text)
    reversal_evidence: Mapped[str] = mapped_column(Text)
    reversal_date: Mapped[date] = mapped_column(Date)
    reversal_id: Mapped[str | None] = mapped_column(ForeignKey('payment_confirmation.id'), unique=True)


class ERPIdentity(Base):
    __tablename__='erp_identity'
    user_id: Mapped[str] = mapped_column(ForeignKey('app_user.id'), primary_key=True)
    erp_user_id: Mapped[str] = mapped_column(String(40), unique=True)
    token_ciphertext: Mapped[str | None] = mapped_column(Text)
    authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)


class ERPOperation(IdentityMixin, Base):
    __tablename__='erp_operation'
    user_id: Mapped[str] = mapped_column(ForeignKey('app_user.id'))
    intent_id: Mapped[str] = mapped_column(ForeignKey('human_action_intent.id'), unique=True)
    action: Mapped[str] = mapped_column(String(80))
    native_id: Mapped[str] = mapped_column(String(80), index=True)
    state: Mapped[str] = mapped_column(String(30), default='DISPATCHING')
    request_hash: Mapped[str] = mapped_column(String(64))
    erp_user_id: Mapped[str] = mapped_column(String(40))
    response: Mapped[dict | None] = mapped_column(J)
    error_code: Mapped[str | None] = mapped_column(String(80))
    __table_args__=(CheckConstraint("state IN ('DISPATCHING','SUCCEEDED','REJECTED','UNKNOWN','OBSERVED_APPLIED')"),)
