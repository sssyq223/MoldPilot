"""Transport-neutral schema primitives exposed to business packs."""
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SubmitInput(StrictModel):
    revision: int = Field(ge=1)
    definition_id: str
    material_review_id: str | None = Field(default=None, min_length=1, max_length=36)
