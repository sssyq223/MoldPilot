from datetime import date, datetime
from uuid import UUID
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(StrictModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class UserInput(StrictModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=100)
    department: str = Field(default="", max_length=100)
    password: str = Field(min_length=12, max_length=128)


class AvatarInput(StrictModel):
    avatar_url: str = Field(default="", max_length=150000)


class GrantInput(StrictModel):
    permission: str
    effect: Literal["ALLOW", "DENY"] = "ALLOW"
    scope: dict
    fields: list[str]
    reason: str = Field(min_length=1, max_length=500)
    expected_security_version: int
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @field_validator("valid_from", "valid_to")
    @classmethod
    def aware(cls, value):
        if value is not None and value.tzinfo is None: raise ValueError("时间必须携带时区偏移")
        return value


class LineInput(StrictModel):
    material_id: str
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    due_date: date


class PurchaseInput(StrictModel):
    project_id: str
    remark: str = Field(default="", max_length=4000)
    lines: list[LineInput] = Field(min_length=1, max_length=100)


class DefinitionInput(StrictModel):
    process_key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    name: str = Field(min_length=1, max_length=150)
    config: dict
    category_id: str | None = Field(default=None,max_length=36)
    material_template_id: str | None = Field(default=None,max_length=36)


class SubmitInput(StrictModel):
    revision: int = Field(ge=1)
    definition_id: str
    material_review_id: str | None = Field(default=None, min_length=1, max_length=36)


class DefinitionEditInput(StrictModel):
    name: str = Field(min_length=1, max_length=150)
    config: dict
    expected_hash: str = Field(min_length=64, max_length=64)
    category_id: str | None = Field(default=None,max_length=36)
    material_template_id: str | None = Field(default=None,max_length=36)


class WorkflowSimulationInput(StrictModel):
    config: dict
    snapshot: dict
    material_template_id: str | None = Field(default=None,max_length=36)


class DecisionInput(StrictModel):
    instance_id: str
    seat_id: str
    seat_version: int
    version: int
    snapshot_hash: str
    decision: Literal["APPROVE", "REJECT", "RETURN"]
    comment: str = Field(min_length=1, max_length=2000)


class AgentApprovalDelegationInput(StrictModel):
    process_key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    node_key: str = Field(min_length=1, max_length=80)
    decision: Literal["APPROVE"] = "APPROVE"
    reason: str = Field(min_length=1, max_length=500)
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @field_validator("valid_from", "valid_to")
    @classmethod
    def aware(cls, value):
        if value is not None and value.tzinfo is None: raise ValueError("时间必须携带时区偏移")
        return value


class AgentApprovalDelegationRevokeInput(StrictModel):
    reason: str = Field(min_length=1, max_length=500)


class ConfirmationInput(StrictModel):
    challenge: str = Field(min_length=32, max_length=200)


class RunInput(StrictModel):
    prompt: str = Field(min_length=1, max_length=6000)
    conversation_id: str | None = None
    file_ids:list[UUID]=Field(default_factory=list,max_length=10)
    agent_permission_mode: Literal["ask", "delegated_auto"] = "ask"


class CapabilityInput(StrictModel):
    kind: Literal["TOOL", "SKILL"]
    key: str = Field(min_length=1, max_length=100)
    enabled: bool
    reason: str = Field(min_length=1, max_length=500)
    expected_security_version: int


class CompanyModelConfigInput(StrictModel):
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=160)
    trusted_http_origin: str = Field(default="", max_length=500)
    api_key: str = Field(default="", max_length=4000)
    clear_api_key: bool = False
    proxy_url: str = Field(default="", max_length=500)


class OllamaModelConfigInput(StrictModel):
    base_url: str = Field(default="http://127.0.0.1:11434", max_length=500)
    model: str = Field(default="", max_length=160)


class ModelConfigInput(StrictModel):
    enabled: bool = True
    provider: Literal["company", "ollama"] = "company"
    company: CompanyModelConfigInput = Field(default_factory=CompanyModelConfigInput)
    ollama: OllamaModelConfigInput = Field(default_factory=OllamaModelConfigInput)
    max_output_tokens: int = Field(default=2048, ge=256, le=8192)
    context_window: int = Field(default=8192, ge=4096, le=2_000_000)
    max_turns: int = Field(default=12, ge=1, le=30)
    connect_timeout: float = Field(default=10, gt=0, le=20)
    read_timeout: float = Field(default=60, gt=0, le=120)
