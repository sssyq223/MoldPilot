"""Schemas and matching rules shared by mold-domain application services."""
from pydantic import Field, model_validator

from agent_core.schemas import StrictModel


class ProjectPlanContextInput(StrictModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    identifier: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="项目编号/名称、计划单号、计划任务关键字、模具号或其他可见业务线索。",
    )

    @model_validator(mode="after")
    def one_locator(self):
        if bool(self.project_id) == bool(self.identifier):
            raise ValueError("project_id 和 identifier 须且只能填写一项")
        if self.identifier:
            self.identifier = self.identifier.strip()
            if not self.identifier:
                raise ValueError("线索不能为空")
        return self


def match_strength(value, needle):
    if value is None:
        return 0
    value = str(value).casefold()
    needle = str(needle).casefold()
    return 100 if value == needle else 50 if needle in value else 0
