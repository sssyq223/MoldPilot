"""Keep Agent project identities distinct from external business identifiers."""
import re
from typing import Annotated

from pydantic import BeforeValidator, Field


PROJECT_ID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
RESOLVED_PROJECT_ID_GUIDANCE = "project_id 必须是查询结果返回的项目 UUID；请先通过 query_projects 查询真实项目 ID，不能填写项目编号或名称。"


def _project_id(value):
    if not isinstance(value, str) or not re.fullmatch(PROJECT_ID_PATTERN, value):
        raise ValueError("project_id 必须是查询结果返回的项目 UUID；项目编号、模具号或名称请填写 identifier，不能当作项目 ID。")
    return value.lower()


def _resolved_project_id(value):
    if not isinstance(value, str) or not re.fullmatch(PROJECT_ID_PATTERN, value):
        raise ValueError(RESOLVED_PROJECT_ID_GUIDANCE)
    return value.lower()


ProjectId = Annotated[str, Field(
    pattern=PROJECT_ID_PATTERN,
    description="查询结果返回的 Agent 项目 UUID。业务项目编号、模具号或名称使用 identifier；不要猜测 ID。",
), BeforeValidator(_project_id)]


# ID-only actions must not instruct callers to use a nonexistent identifier field.
ResolvedProjectId = Annotated[str, Field(
    pattern=PROJECT_ID_PATTERN, description=RESOLVED_PROJECT_ID_GUIDANCE,
), BeforeValidator(_resolved_project_id)]
