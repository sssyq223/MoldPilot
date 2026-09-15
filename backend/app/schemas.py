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


class ConfirmationInput(StrictModel):
    challenge: str = Field(min_length=32, max_length=200)


class RunInput(StrictModel):
    prompt: str = Field(min_length=1, max_length=6000)
    conversation_id: str | None = None
    file_ids:list[UUID]=Field(default_factory=list,max_length=10)


class CapabilityInput(StrictModel):
    kind: Literal["TOOL", "SKILL"]
    key: str = Field(min_length=1, max_length=100)
    enabled: bool
    reason: str = Field(min_length=1, max_length=500)
    expected_security_version: int
