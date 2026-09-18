"""Narrow bridge from MoldPilot's tool catalog to the ERP design-upload MCP.

The package owns ERP requests and returns ERP results.  This module never
copies design, drawing, BOM, purchase, or approval records into agent_db.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from base64 import b64decode
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, ValidationError
from sqlalchemy import select

from app import object_storage
from domain_packs.mold import files, models as m
from app.db import now
from app.errors import DomainError
from app.events import record
from app.schemas import StrictModel


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_RUNTIME_ROOT = _PROJECT_ROOT / "mcp" / "erp-design-upload"
_ENV_FILE = _RUNTIME_ROOT / ".env"
_SERVER_FILE = _RUNTIME_ROOT / "node_modules" / "erp-design-upload-mcp" / "scripts" / "erp-design-upload-mcp.mjs"
_CONTROL_SERVER_FILE = _RUNTIME_ROOT / "scripts" / "erp-design-control-mcp.mjs"
_SESSION_ACTION = "erp_design_mcp.parsed"
_SHEET_TYPES = Literal["steel", "hardware"]


TOOL_SPECS = {
    "erp_design_parse_new_mold_upload": {
        "description": "通过 ERP MCP 上传并解析新模钢料或五金设计清单，创建 ERP 上传会话；图纸处理请随后查询状态。",
        "permission": "design_route.create",
    },
    "erp_design_get_drawing_status": {
        "description": "查询 ERP 新模设计上传会话的图纸匹配与归档处理状态；仅可查询本人发起的会话。",
        "permission": "design_route.read",
    },
    "erp_design_get_upload_result": {
        "description": "读取已完成 ERP 设计上传会话的完整解析结果与待核对明细；只读。",
        "permission": "design_route.read",
    },
    "erp_design_validate_rows": {
        "description": "使用 ERP 规则校验新模设计上传明细，不创建请购或审批数据。",
        "permission": "design_route.create",
    },
    "erp_design_reprice_rows": {
        "description": "使用 ERP 规则重新核算新模设计清单价格；只返回重新核价结果。",
        "permission": "design_route.create",
    },
    "erp_design_get_approval_config": {
        "description": "读取 ERP 为新模设计上传会话返回的审批发起配置；只读。",
        "permission": "design_route.read",
    },
    "erp_design_import_new_mold": {
        "description": "在用户明确确认后，将已校验的新模设计明细导入 ERP 并创建 ERP 请购/审批数据。",
        "permission": "design_route.execute",
        "write": True,
    },
}

READ_TOOL_MAP = {
    "erp_design_query_orders": "query_erp_design_orders",
    "erp_design_query_drawing_versions": "query_erp_drawing_versions",
    "erp_design_query_bom": "query_erp_bom",
    "erp_design_query_bom_report": "query_erp_bom_report",
    "erp_design_query_changes": "query_erp_design_changes",
    "erp_design_query_standard_hardware": "query_erp_standard_hardware_drawings",
    "erp_design_query_densities": "query_erp_design_densities",
    "erp_design_query_group_rules": "query_erp_design_group_rules",
    "erp_design_query_group_keywords": "query_erp_design_group_keywords",
    "erp_design_get_record": "get_erp_design_record",
    "erp_design_compare_drawing_versions": "compare_erp_drawing_versions",
    "erp_design_analyze_change": "analyze_erp_design_change",
    "erp_design_get_mold_repair_approval": "get_erp_mold_repair_approval",
    "erp_design_get_mold_repair_outsource_approval": "get_erp_mold_repair_outsource_approval",
    "erp_design_get_mold_repair_processor_response": "get_erp_mold_repair_processor_response",
}
TOOL_SPECS.update({
    "erp_design_query_orders": {"description": "查询 ERP 设计订单及其当前状态。", "permission": "design_route.read"},
    "erp_design_query_drawing_versions": {"description": "查询 ERP 图纸版本主线和发布状态。", "permission": "design_route.read"},
    "erp_design_query_bom": {"description": "查询 ERP BOM 明细。", "permission": "design_route.read"},
    "erp_design_query_bom_report": {"description": "查询 ERP BOM 汇总、物料、采购进度或待采购报表。", "permission": "design_route.read"},
    "erp_design_query_changes": {"description": "查询 ERP 设变申请及其流程状态。", "permission": "design_route.read"},
    "erp_design_query_standard_hardware": {"description": "查询 ERP 厂内标准件图纸目录。", "permission": "design_route.read"},
    "erp_design_query_densities": {"description": "查询 ERP 设计材质密度配置。", "permission": "design_route.read"},
    "erp_design_query_group_rules": {"description": "查询 ERP 设计分组与采购拆分规则。", "permission": "design_route.read"},
    "erp_design_query_group_keywords": {"description": "查询 ERP 设计分组关键词。", "permission": "design_route.read"},
    "erp_design_get_record": {"description": "读取一条 ERP 设计订单、图纸版本、BOM、设变或设计配置详情。", "permission": "design_route.read"},
    "erp_design_compare_drawing_versions": {"description": "对比 ERP 同一图纸的两个版本。", "permission": "design_route.read"},
    "erp_design_analyze_change": {"description": "读取 ERP 设变影响分析，不提交、评审、确认或执行设变。", "permission": "design_route.read"},
    "erp_design_get_mold_repair_approval": {"description": "读取 ERP 修模改模图纸异常审批批次详情。", "permission": "design_route.read"},
    "erp_design_get_mold_repair_outsource_approval": {"description": "读取 ERP 委外定标触发的修模改模图纸异常审批详情。", "permission": "design_route.read"},
    "erp_design_get_mold_repair_processor_response": {"description": "读取 ERP 加工商对修模改模图纸异常的响应状态。", "permission": "design_route.read"},
})

CONTROL_TOOL_MAP = {
    "erp_design_rematch_no_drawing": "rematch_new_mold_upload_drawings",
    "erp_design_update_order_item": "update_design_order_item",
    "erp_design_save_scrap_decision": "save_design_order_scrap_decision",
    "erp_design_release_scrap_decision": "release_design_order_scrap_decision",
    "erp_design_create_density": "create_design_density",
    "erp_design_update_density": "update_design_density",
    "erp_design_delete_density": "delete_design_density",
    "erp_design_create_group_rule": "create_design_group_rule",
    "erp_design_update_group_rule": "update_design_group_rule",
    "erp_design_toggle_group_rule": "toggle_design_group_rule",
    "erp_design_delete_group_rule": "delete_design_group_rule",
    "erp_design_create_group_keyword": "create_design_group_keyword",
    "erp_design_update_group_keyword": "update_design_group_keyword",
    "erp_design_delete_group_keyword": "delete_design_group_keyword",
    "erp_design_upload_standard_hardware": "upload_standard_hardware_drawings",
    "erp_design_rename_standard_hardware": "rename_standard_hardware_drawing",
    "erp_design_delete_standard_hardware": "delete_standard_hardware_folder",
    "erp_design_manage_change": "manage_design_change",
    "erp_design_query_change_items": "manage_design_change_items",
    "erp_design_manage_change_items": "manage_design_change_items",
    "erp_design_manage_order": "manage_design_order",
    "erp_design_manage_order_draft_scrap": "manage_design_order_draft_scrap",
    "erp_design_submit_upload_change": "submit_design_upload_change",
    "erp_design_manage_mold_repair": "manage_mold_repair",
    "erp_design_manage_bom": "manage_erp_bom",
    "erp_design_query_bom_shortage": "get_erp_bom_shortage",
    "erp_design_upload_mold_repair_drawing": "upload_mold_repair_drawing",
    "erp_design_import_bom": "import_erp_bom",
    "erp_design_download_file": "download_erp_design_file",
}
TOOL_SPECS.update({
    "erp_design_rematch_no_drawing": {"description": "在用户明确确认后，请 ERP 对本人新模上传会话中的历史无图明细重新匹配图纸。", "permission": "design_route.execute", "write": True},
    "erp_design_update_order_item": {"description": "在用户明确确认后，修改 ERP 设计订单的一条可编辑明细，并保留修改原因。", "permission": "design_route.execute", "write": True},
    "erp_design_save_scrap_decision": {"description": "在用户明确确认后，为 ERP 设计订单明细保存闲置料使用决策。", "permission": "design_route.execute", "write": True},
    "erp_design_release_scrap_decision": {"description": "在用户明确确认后，释放 ERP 设计订单明细已占用的闲置料决策。", "permission": "design_route.execute", "write": True},
    "erp_design_create_density": {"description": "在用户明确确认后，在 ERP 新增设计材质密度。", "permission": "design_route.execute", "write": True},
    "erp_design_update_density": {"description": "在用户明确确认后，修改 ERP 设计材质密度。", "permission": "design_route.execute", "write": True},
    "erp_design_delete_density": {"description": "在用户明确确认后，删除 ERP 设计材质密度。", "permission": "design_route.execute", "write": True, "destructive": True},
    "erp_design_create_group_rule": {"description": "在用户明确确认后，在 ERP 新增设计分组与采购拆分规则。", "permission": "design_route.execute", "write": True},
    "erp_design_update_group_rule": {"description": "在用户明确确认后，修改 ERP 设计分组与采购拆分规则。", "permission": "design_route.execute", "write": True},
    "erp_design_toggle_group_rule": {"description": "在用户明确确认后，启用或停用 ERP 设计分组与采购拆分规则。", "permission": "design_route.execute", "write": True},
    "erp_design_delete_group_rule": {"description": "在用户明确确认后，删除 ERP 设计分组与采购拆分规则。", "permission": "design_route.execute", "write": True, "destructive": True},
    "erp_design_create_group_keyword": {"description": "在用户明确确认后，在 ERP 新增设计分组关键词。", "permission": "design_route.execute", "write": True},
    "erp_design_update_group_keyword": {"description": "在用户明确确认后，修改 ERP 设计分组关键词。", "permission": "design_route.execute", "write": True},
    "erp_design_delete_group_keyword": {"description": "在用户明确确认后，删除 ERP 设计分组关键词。", "permission": "design_route.execute", "write": True, "destructive": True},
    "erp_design_upload_standard_hardware": {"description": "在用户明确确认后，将当前会话选定的图纸附件上传到 ERP 厂内标准件目录。", "permission": "design_route.execute", "write": True},
    "erp_design_rename_standard_hardware": {"description": "在用户明确确认后，重命名 ERP 厂内标准件图纸。", "permission": "design_route.execute", "write": True},
    "erp_design_delete_standard_hardware": {"description": "在用户明确确认后，删除 ERP 厂内标准件图纸文件夹。", "permission": "design_route.execute", "write": True, "destructive": True},
    "erp_design_manage_change": {"description": "在用户明确确认后，创建、修改、删除、提交、评审或确认 ERP 设变申请。", "permission": "design_route.execute", "write": True},
    "erp_design_query_change_items": {"description": "查询指定 ERP 设变申请的明细。", "permission": "design_route.read"},
    "erp_design_manage_change_items": {"description": "在用户明确确认后维护或执行 ERP 设变明细。", "permission": "design_route.execute", "write": True},
    "erp_design_manage_order": {"description": "在用户明确确认后删除、审批或重新提交 ERP 设计订单。", "permission": "design_route.execute", "write": True},
    "erp_design_manage_order_draft_scrap": {"description": "在用户明确确认后保存或释放 ERP 设计草稿明细的闲置料决策。", "permission": "design_route.execute", "write": True},
    "erp_design_submit_upload_change": {"description": "在用户明确确认后，将已解析的设计上传清单提交为 ERP 变更请购。", "permission": "design_route.execute", "write": True},
    "erp_design_manage_mold_repair": {"description": "在用户明确确认后处理 ERP 修模改模图纸异常的数量、审批、订单关联或加工商响应。", "permission": "design_route.execute", "write": True},
    "erp_design_manage_bom": {"description": "在用户明确确认后新增、修改或删除 ERP BOM。", "permission": "design_route.execute", "write": True},
    "erp_design_query_bom_shortage": {"description": "查询 ERP 指定模具及可选零件的 BOM 缺料情况。", "permission": "design_route.read"},
    "erp_design_upload_mold_repair_drawing": {"description": "在用户明确确认后，将当前会话的修模改模图纸上传至 ERP 并生成异常分析。", "permission": "design_route.execute", "write": True},
    "erp_design_import_bom": {"description": "在用户明确确认后，将当前会话的 XLSX BOM 工艺清单导入指定 ERP 模具。", "permission": "design_route.execute", "write": True},
    "erp_design_download_file": {"description": "下载 ERP 图纸、标准件目录、修模图纸包或 BOM 导出文件，并保存为当前会话的私有附件。", "permission": "design_route.read"},
})

# These names are returned by the capability API and are the user-facing labels
# in the settings page.  Keep them next to the ERP tool catalogue so a newly
# registered tool cannot fall back to the generic "业务查询能力" label.
TOOL_NAMES = {
    "erp_design_parse_new_mold_upload": "解析新模设计上传清单",
    "erp_design_get_drawing_status": "查询新模图纸匹配状态",
    "erp_design_get_upload_result": "读取新模设计上传结果",
    "erp_design_validate_rows": "校验新模设计上传明细",
    "erp_design_reprice_rows": "重新核算新模设计价格",
    "erp_design_get_approval_config": "读取新模设计审批配置",
    "erp_design_import_new_mold": "导入新模设计清单",
    "erp_design_query_orders": "查询 ERP 设计订单",
    "erp_design_query_drawing_versions": "查询 ERP 图纸版本",
    "erp_design_query_bom": "查询 ERP BOM",
    "erp_design_query_bom_report": "查询 ERP BOM 报表",
    "erp_design_query_changes": "查询 ERP 设计变更",
    "erp_design_query_standard_hardware": "查询 ERP 厂内标准件",
    "erp_design_query_densities": "查询 ERP 材质密度",
    "erp_design_query_group_rules": "查询 ERP 设计分组规则",
    "erp_design_query_group_keywords": "查询 ERP 分组关键词",
    "erp_design_get_record": "读取 ERP 设计记录",
    "erp_design_compare_drawing_versions": "对比 ERP 图纸版本",
    "erp_design_analyze_change": "分析 ERP 设计变更影响",
    "erp_design_get_mold_repair_approval": "读取修模改模审批",
    "erp_design_get_mold_repair_outsource_approval": "读取修模改模委外审批",
    "erp_design_get_mold_repair_processor_response": "读取加工商处理反馈",
    "erp_design_rematch_no_drawing": "重新匹配无图设计明细",
    "erp_design_update_order_item": "修改 ERP 设计订单明细",
    "erp_design_save_scrap_decision": "保存闲置料使用决策",
    "erp_design_release_scrap_decision": "释放闲置料使用决策",
    "erp_design_create_density": "新增 ERP 材质密度",
    "erp_design_update_density": "修改 ERP 材质密度",
    "erp_design_delete_density": "删除 ERP 材质密度",
    "erp_design_create_group_rule": "新增 ERP 设计分组规则",
    "erp_design_update_group_rule": "修改 ERP 设计分组规则",
    "erp_design_toggle_group_rule": "启停 ERP 设计分组规则",
    "erp_design_delete_group_rule": "删除 ERP 设计分组规则",
    "erp_design_create_group_keyword": "新增 ERP 分组关键词",
    "erp_design_update_group_keyword": "修改 ERP 分组关键词",
    "erp_design_delete_group_keyword": "删除 ERP 分组关键词",
    "erp_design_upload_standard_hardware": "上传 ERP 厂内标准件图纸",
    "erp_design_rename_standard_hardware": "重命名 ERP 厂内标准件图纸",
    "erp_design_delete_standard_hardware": "删除 ERP 厂内标准件图纸目录",
    "erp_design_manage_change": "办理 ERP 设计变更",
    "erp_design_query_change_items": "查询 ERP 设计变更明细",
    "erp_design_manage_change_items": "维护 ERP 设计变更明细",
    "erp_design_manage_order": "办理 ERP 设计订单",
    "erp_design_manage_order_draft_scrap": "维护设计订单草稿闲置料",
    "erp_design_submit_upload_change": "提交设计上传变更请购",
    "erp_design_manage_mold_repair": "办理修模改模图纸异常",
    "erp_design_manage_bom": "维护 ERP BOM",
    "erp_design_query_bom_shortage": "查询 ERP BOM 缺料",
    "erp_design_upload_mold_repair_drawing": "上传修模改模图纸",
    "erp_design_import_bom": "导入 ERP BOM 工艺清单",
    "erp_design_download_file": "下载 ERP 设计资料",
}
for _tool_key, _tool_name in TOOL_NAMES.items():
    TOOL_SPECS[_tool_key]["name"] = _tool_name


class ParseInput(StrictModel):
    sheet_type: _SHEET_TYPES
    file_id: UUID | None = Field(default=None, description="当前会话中的 XLSX 附件标识；未填写时自动使用唯一的 XLSX 附件。")
    design_order_sub_type: str | None = Field(default=None, max_length=100)


class SessionInput(StrictModel):
    session_id: int = Field(ge=1)


class StatusInput(SessionInput):
    include_result: bool = False


class RowsInput(SessionInput):
    sheet_type: _SHEET_TYPES
    preview_rows: list[dict] = Field(min_length=1, max_length=1000)
    mold_code: str | None = Field(default=None, max_length=120)


class ImportInput(RowsInput):
    confirm_import: Literal[True] = Field(description="仅在用户已经明确确认导入后设为 true。")
    urgency_level: Literal["normal", "important", "urgent"] = "normal"
    expected_date: str = Field(min_length=10, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$")
    purchase_reason: str | None = Field(default=None, max_length=200)
    remark: str | None = Field(default=None, max_length=1000)
    allow_duplicate: bool = False


class QueryInput(StrictModel):
    query: dict = Field(default_factory=dict, max_length=30)


class BomReportInput(QueryInput):
    report: Literal["mold_summary", "material_summary", "purchase_progress", "pending_purchase"]


class RecordInput(StrictModel):
    resource: Literal["design_order", "drawing_version", "bom", "design_change", "density", "group_rule", "group_keyword"]
    id: int = Field(ge=1)


class DrawingCompareInput(StrictModel):
    from_version_id: int = Field(ge=1, serialization_alias="fromVersionId")
    to_version_id: int = Field(ge=1, serialization_alias="toVersionId")


class MoldRepairApprovalInput(StrictModel):
    batch_id: int = Field(ge=1)


class MoldRepairOutsourceApprovalInput(StrictModel):
    approval_order_id: int = Field(ge=1)


class MoldRepairProcessorResponseInput(StrictModel):
    group_token: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    order_id: int = Field(ge=1)


class ConfirmedSessionInput(SessionInput):
    confirm: Literal[True] = Field(description="仅在用户已经明确确认该 ERP 操作后设为 true。")


class OrderItemUpdateInput(StrictModel):
    detail_id: int = Field(ge=1)
    material_mark: str | None = Field(default=None, max_length=120)
    specification: str | None = Field(default=None, max_length=500)
    modify_reason: str = Field(min_length=1, max_length=500)
    confirm: Literal[True] = Field(description="仅在用户已经明确确认修改后设为 true。")


class ScrapDecisionInput(StrictModel):
    detail_id: int = Field(ge=1)
    decision: Literal["use", "partial", "skip"]
    detail_version: str = Field(min_length=1, max_length=200)
    scrap_inventory_id: int | None = Field(default=None, ge=1)
    used_quantity: Decimal | None = Field(default=None, gt=0)
    match_note: str | None = Field(default=None, max_length=500)
    confirm: Literal[True] = Field(description="仅在用户已经明确确认闲置料决策后设为 true。")


class ScrapReleaseInput(StrictModel):
    detail_id: int = Field(ge=1)
    detail_version: str = Field(min_length=1, max_length=200)
    confirm: Literal[True] = Field(description="仅在用户已经明确确认释放闲置料后设为 true。")


class DensityInput(StrictModel):
    material_mark: str = Field(min_length=1, max_length=120)
    density: Decimal = Field(gt=0, le=100)
    confirm: Literal[True] = Field(description="仅在用户已经明确确认维护密度后设为 true。")


class DensityUpdateInput(DensityInput):
    density_id: int = Field(ge=1)


class ConfirmedIdInput(StrictModel):
    id: int = Field(ge=1)
    confirm: Literal[True] = Field(description="仅在用户已经明确确认删除后设为 true。")


class GroupRuleInput(StrictModel):
    keyword_text: str = Field(min_length=1, max_length=120)
    category_name: str | None = Field(default=None, max_length=120)
    material_category: str | None = Field(default=None, max_length=120)
    match_scope: str = Field(default="material_name", min_length=1, max_length=80)
    group_prefix: str | None = Field(default=None, max_length=80)
    is_exact: int = Field(default=0, ge=0, le=1)
    force_split: int = Field(default=0, ge=0, le=1)
    priority: int = Field(default=100, ge=0, le=10000)
    status: Literal["active", "inactive"] = "active"
    remark: str | None = Field(default=None, max_length=500)
    confirm: Literal[True] = Field(description="仅在用户已经明确确认维护分组规则后设为 true。")


class GroupRuleUpdateInput(GroupRuleInput):
    id: int = Field(ge=1)


class GroupRuleToggleInput(StrictModel):
    id: int = Field(ge=1)
    status: Literal["active", "inactive"]
    confirm: Literal[True] = Field(description="仅在用户已经明确确认切换规则状态后设为 true。")


class GroupKeywordInput(StrictModel):
    keyword_text: str = Field(min_length=1, max_length=120)
    remark: str | None = Field(default=None, max_length=500)
    confirm: Literal[True] = Field(description="仅在用户已经明确确认维护分组关键词后设为 true。")


class GroupKeywordUpdateInput(GroupKeywordInput):
    id: int = Field(ge=1)


class StandardHardwareUploadInput(StrictModel):
    file_ids: list[UUID] = Field(min_length=1, max_length=50)
    folder_name: str = Field(min_length=1, max_length=120)
    confirm: Literal[True] = Field(description="仅在用户已经明确确认上传后设为 true。")


class StandardHardwareRenameInput(StrictModel):
    relative_path: str = Field(min_length=1, max_length=500)
    new_file_name: str = Field(min_length=1, max_length=240)
    confirm: Literal[True] = Field(description="仅在用户已经明确确认重命名后设为 true。")


class StandardHardwareDeleteInput(StrictModel):
    relative_path: str = Field(min_length=1, max_length=500)
    confirm: Literal[True] = Field(description="仅在用户已经明确确认删除后设为 true。")


class ChangeManageInput(StrictModel):
    operation: Literal["create", "update", "delete", "submit", "review", "confirm"]
    change_id: int | None = Field(default=None, ge=1)
    change_ids: list[int] = Field(default_factory=list, max_length=100)
    payload: dict = Field(default_factory=dict, max_length=80, description="按 ERP 设变接口填写的字段。")
    confirm: Literal[True] = Field(description="仅在用户已经明确确认该 ERP 设变操作后设为 true。")


class ChangeItemsManageInput(StrictModel):
    operation: Literal["create", "batch_create", "update", "delete", "execute"]
    change_id: int | None = Field(default=None, ge=1)
    item_ids: list[int] = Field(default_factory=list, max_length=100)
    payload: dict = Field(default_factory=dict, max_length=100, description="按 ERP 设变明细接口填写的字段。")
    confirm: Literal[True] = Field(description="仅在用户已经明确确认维护或执行 ERP 设变明细后设为 true。")


class ChangeItemsQueryInput(StrictModel):
    change_id: int = Field(ge=1)


class OrderManageInput(StrictModel):
    operation: Literal["delete", "approve", "resubmit"]
    request_id: int = Field(ge=1)
    approval_version: str | None = Field(default=None, min_length=1, max_length=200)
    confirm: Literal[True] = Field(description="仅在用户已经明确确认该 ERP 设计订单操作后设为 true。")


class DraftScrapManageInput(StrictModel):
    operation: Literal["save", "release"]
    draft_id: int = Field(ge=1)
    seq: int = Field(ge=1)
    payload: dict = Field(default_factory=dict, max_length=30, description="保存时填写 ERP 所需的 decision、detailVersion 等字段。")
    confirm: Literal[True] = Field(description="仅在用户已经明确确认草稿闲置料操作后设为 true。")


class PayloadMutationInput(StrictModel):
    payload: dict = Field(min_length=1, max_length=100, description="按对应 ERP 接口填写的请求字段。")
    confirm: Literal[True] = Field(description="仅在用户已经明确确认该 ERP 写入操作后设为 true。")


class MoldRepairManageInput(StrictModel):
    operation: Literal["confirm_quantity", "submit_approval", "confirm_order_link", "respond", "submit_approval_batches"]
    exception_id: int | None = Field(default=None, ge=1)
    batch_id: int | None = Field(default=None, ge=1)
    group_token: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    order_id: int | None = Field(default=None, ge=1)
    payload: dict = Field(default_factory=dict, max_length=100, description="按对应 ERP 修模改模接口填写的请求字段。")
    confirm: Literal[True] = Field(description="仅在用户已经明确确认该 ERP 修模改模操作后设为 true。")


class BomManageInput(StrictModel):
    operation: Literal["create", "update", "delete"]
    bom_ids: list[int] = Field(default_factory=list, max_length=100)
    payload: dict = Field(default_factory=dict, max_length=100, description="新增或修改时按 ERP BOM 接口填写的字段。")
    confirm: Literal[True] = Field(description="仅在用户已经明确确认该 ERP BOM 写入操作后设为 true。")


class BomShortageInput(StrictModel):
    mold_id: int = Field(ge=1)
    part_id: int | None = Field(default=None, ge=1)


class SessionFileMutationInput(StrictModel):
    file_id: UUID
    confirm: Literal[True] = Field(description="仅在用户已经明确确认上传或导入后设为 true。")


class MoldRepairUploadInput(SessionFileMutationInput):
    skip_vision: bool = False


class BomImportInput(SessionFileMutationInput):
    mold_id: int = Field(ge=1)


class DownloadInput(StrictModel):
    artifact: Literal["drawing_preview", "drawing_download", "standard_hardware_preview", "standard_hardware_folder",
                      "mold_repair_outsource_approval_drawing", "mold_repair_approval_drawing",
                      "mold_repair_entrust_order_drawing", "mold_repair_exception_drawing", "mold_repair_group_drawing",
                      "mold_repair_authorized_package", "bom_export"]
    drawing_id: int | None = Field(default=None, ge=1)
    relative_path: str | None = Field(default=None, min_length=1, max_length=500)
    approval_order_id: int | None = Field(default=None, ge=1)
    exception_id: int | None = Field(default=None, ge=1)
    batch_id: int | None = Field(default=None, ge=1)
    order_id: int | None = Field(default=None, ge=1)
    group_token: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    kind: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    route_type: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    order_no: str | None = Field(default=None, max_length=160)
    partner_id: int | None = Field(default=None, ge=1)
    mold_id: int | None = Field(default=None, ge=1)


_INPUTS = {
    "erp_design_parse_new_mold_upload": ParseInput,
    "erp_design_get_drawing_status": StatusInput,
    "erp_design_get_upload_result": SessionInput,
    "erp_design_validate_rows": RowsInput,
    "erp_design_reprice_rows": RowsInput,
    "erp_design_get_approval_config": SessionInput,
    "erp_design_import_new_mold": ImportInput,
    "erp_design_query_orders": QueryInput,
    "erp_design_query_drawing_versions": QueryInput,
    "erp_design_query_bom": QueryInput,
    "erp_design_query_bom_report": BomReportInput,
    "erp_design_query_changes": QueryInput,
    "erp_design_query_standard_hardware": QueryInput,
    "erp_design_query_densities": QueryInput,
    "erp_design_query_group_rules": QueryInput,
    "erp_design_query_group_keywords": QueryInput,
    "erp_design_get_record": RecordInput,
    "erp_design_compare_drawing_versions": DrawingCompareInput,
    "erp_design_analyze_change": QueryInput,
    "erp_design_get_mold_repair_approval": MoldRepairApprovalInput,
    "erp_design_get_mold_repair_outsource_approval": MoldRepairOutsourceApprovalInput,
    "erp_design_get_mold_repair_processor_response": MoldRepairProcessorResponseInput,
    "erp_design_rematch_no_drawing": ConfirmedSessionInput,
    "erp_design_update_order_item": OrderItemUpdateInput,
    "erp_design_save_scrap_decision": ScrapDecisionInput,
    "erp_design_release_scrap_decision": ScrapReleaseInput,
    "erp_design_create_density": DensityInput,
    "erp_design_update_density": DensityUpdateInput,
    "erp_design_delete_density": ConfirmedIdInput,
    "erp_design_create_group_rule": GroupRuleInput,
    "erp_design_update_group_rule": GroupRuleUpdateInput,
    "erp_design_toggle_group_rule": GroupRuleToggleInput,
    "erp_design_delete_group_rule": ConfirmedIdInput,
    "erp_design_create_group_keyword": GroupKeywordInput,
    "erp_design_update_group_keyword": GroupKeywordUpdateInput,
    "erp_design_delete_group_keyword": ConfirmedIdInput,
    "erp_design_upload_standard_hardware": StandardHardwareUploadInput,
    "erp_design_rename_standard_hardware": StandardHardwareRenameInput,
    "erp_design_delete_standard_hardware": StandardHardwareDeleteInput,
    "erp_design_manage_change": ChangeManageInput,
    "erp_design_query_change_items": ChangeItemsQueryInput,
    "erp_design_manage_change_items": ChangeItemsManageInput,
    "erp_design_manage_order": OrderManageInput,
    "erp_design_manage_order_draft_scrap": DraftScrapManageInput,
    "erp_design_submit_upload_change": PayloadMutationInput,
    "erp_design_manage_mold_repair": MoldRepairManageInput,
    "erp_design_manage_bom": BomManageInput,
    "erp_design_query_bom_shortage": BomShortageInput,
    "erp_design_upload_mold_repair_drawing": MoldRepairUploadInput,
    "erp_design_import_bom": BomImportInput,
    "erp_design_download_file": DownloadInput,
}


def tool_schema(key: str) -> dict:
    return {"type": "function", "function": {
        "name": key, "description": TOOL_SPECS[key]["description"],
        "parameters": _INPUTS[key].model_json_schema(),
    }}


def _failure(message: str, status: int = 502):
    message = " ".join(str(message).split())[:1000]
    if "fetch failed" in message.lower() or "econnrefused" in message.lower():
        raise DomainError("ERP_DESIGN_MCP_UNAVAILABLE", "ERP 设计服务不可达，请确认 management-system ERP 后端已启动且 MCP 地址可访问", 503)
    if "重复上传" in message:
        raise DomainError("ERP_DUPLICATE_CONFIRMATION_REQUIRED", message, 409)
    raise DomainError("ERP_DESIGN_MCP_FAILED", message or "ERP 设计 MCP 调用失败", status)


def _read_line(stream, output: Queue):
    try:
        for line in iter(stream.readline, ""):
            output.put(line)
    finally:
        output.put(None)


def _call_server(server_file: Path, name: str, arguments: dict) -> dict | list:
    """Call exactly one code-registered MCP tool over stdio."""
    if not _ENV_FILE.is_file() or not server_file.is_file():
        _failure("ERP 设计 MCP 尚未安装或本地配置不完整", 503)
    if not shutil.which("node"):
        _failure("未找到 Node.js，无法启动 ERP 设计 MCP", 503)
    try:
        process = subprocess.Popen(
            ["node", f"--env-file-if-exists={_ENV_FILE}", str(server_file)],
            cwd=_RUNTIME_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
        )
    except OSError:
        _failure("无法启动 ERP 设计 MCP", 503)
    if process.stdin is None or process.stdout is None:
        _failure("无法启动 ERP 设计 MCP", 503)
    output: Queue[str | None] = Queue()
    Thread(target=_read_line, args=(process.stdout, output), daemon=True).start()
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "MoldPilot", "version": "0.1.0"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
    ]
    try:
        for request in requests:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()
        while True:
            try:
                line = output.get(timeout=120)
            except Empty:
                _failure("ERP 设计 MCP 未在两分钟内返回结果", 504)
            if line is None:
                _failure("ERP 设计 MCP 在返回结果前停止")
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if response.get("id") != 2:
                continue
            if response.get("error"):
                _failure(response["error"].get("message", "MCP 请求失败"))
            result = response.get("result")
            if not isinstance(result, dict):
                _failure("ERP 设计 MCP 返回结构无效")
            if result.get("isError"):
                content = result.get("content") or []
                text = content[0].get("text", "ERP 设计处理失败") if content and isinstance(content[0], dict) else "ERP 设计处理失败"
                _failure(text)
            value = result.get("structuredContent")
            if isinstance(value, (dict, list)):
                return value
            _failure("ERP 设计 MCP 未返回可读取结果")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def call_mcp(name: str, arguments: dict) -> dict | list:
    return _call_server(_SERVER_FILE, name, arguments)


def call_design_control_mcp(name: str, arguments: dict) -> dict | list:
    return _call_server(_CONTROL_SERVER_FILE, name, arguments)


def _owned_session(db, user, session_id: int):
    event = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.user_id == user.id, m.AuditEvent.action == _SESSION_ACTION,
        m.AuditEvent.resource_id == str(session_id),
    ).order_by(m.AuditEvent.created_at.desc()))
    if not event:
        raise DomainError("ERP_UPLOAD_NOT_FOUND", "上传会话不存在，或不属于当前账号", 404)


def _temporary_xlsx(db, user, run, requested_id: UUID | None):
    if not run or run.user_id != user.id:
        raise DomainError("FILE_CONTEXT_INVALID", "设计上传须从当前会话的附件发起", 403)
    allowed_ids = {str(value) for value in db.scalars(select(m.RunFile.file_id).where(m.RunFile.run_id == run.id))}
    if requested_id:
        if str(requested_id) not in allowed_ids:
            raise DomainError("FILE_CONTEXT_INVALID", "所选文件不属于当前会话", 403)
        source = files.uploaded_file(db, user, str(requested_id))
    else:
        candidates = [files.uploaded_file(db, user, file_id) for file_id in allowed_ids]
        candidates = [item for item in candidates if Path(item.filename).suffix.lower() == ".xlsx"]
        if len(candidates) != 1:
            raise DomainError("ERP_DESIGN_FILE_REQUIRED", "请在当前会话附加且仅附加一份 XLSX 设计清单，或明确指定 file_id", 409)
        source = candidates[0]
    if Path(source.filename).suffix.lower() != ".xlsx":
        raise DomainError("ERP_DESIGN_FILE_TYPE", "新模设计上传仅支持 XLSX 文件")
    filename, _ = files.validate_file(source.filename, object_storage.read(source))
    directory = Path(tempfile.mkdtemp(prefix="moldpilot-erp-design-"))
    path = directory / filename
    path.write_bytes(object_storage.read(source))
    return directory, path, source


def _temporary_files(db, user, run, file_ids: list[UUID]):
    if not run or run.user_id != user.id:
        raise DomainError("FILE_CONTEXT_INVALID", "标准件图纸上传须从当前会话的附件发起", 403)
    allowed_ids = {str(value) for value in db.scalars(select(m.RunFile.file_id).where(m.RunFile.run_id == run.id))}
    requested_ids = [str(value) for value in file_ids]
    if len(set(requested_ids)) != len(requested_ids) or any(value not in allowed_ids for value in requested_ids):
        raise DomainError("FILE_CONTEXT_INVALID", "所选标准件图纸不属于当前会话", 403)
    directory = Path(tempfile.mkdtemp(prefix="moldpilot-erp-standard-hardware-"))
    paths = []
    try:
        for index, file_id in enumerate(requested_ids, start=1):
            source = files.uploaded_file(db, user, file_id)
            filename, _ = files.validate_file(source.filename, object_storage.read(source))
            path = directory / f"{index}-{filename}"
            path.write_bytes(object_storage.read(source))
            paths.append(path)
        return directory, paths
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def _store_erp_download(db, user, run, value: dict):
    if not run or run.user_id != user.id:
        raise DomainError("FILE_CONTEXT_INVALID", "ERP 文件下载须绑定当前会话", 403)
    if not isinstance(value, dict) or not isinstance(value.get("base64"), str):
        _failure("ERP 文件下载未返回有效文件内容")
    try:
        raw = b64decode(value["base64"], validate=True)
    except ValueError:
        _failure("ERP 文件下载内容无效")
    if not raw or len(raw) > 20 * 1024 * 1024:
        _failure("ERP 文件为空或超过 20 MB 限制", 413)
    filename, media_type = files.validate_file(str(value.get("fileName") or "erp-design-file"), raw)
    digest = sha256(raw).hexdigest()
    key = uuid4().hex + "/" + digest
    storage = object_storage.put(key, raw, media_type)
    blob = m.FileObject(owner_id=user.id, conversation_id=run.conversation_id, request_key=str(uuid4()), filename=filename,
                        media_type=media_type, size=len(raw), sha256=digest, object_key=key, **storage)
    db.add(blob)
    db.flush()
    record(db, user, "erp_design_mcp.file_downloaded", blob.id, {"filename": filename, "size": blob.size})
    return {"file": files.metadata(blob), "download_path": f"/api/files/{blob.id}/content"}


def _result(value):
    return {
        "data": value, "source": "management-system ERP via erp-design-upload MCP",
        "as_of": now().isoformat(),
        "limitations": ["设计、图纸处理和导入结果以 ERP 原始回执为准；MoldPilot 不保存 ERP 业务副本。"],
    }


def _control_arguments(key: str, data: StrictModel) -> dict:
    if key == "erp_design_rematch_no_drawing":
        return {"sessionId": data.session_id}
    if key == "erp_design_update_order_item":
        return {"detailId": data.detail_id, "materialMark": data.material_mark,
                "specification": data.specification, "modifyReason": data.modify_reason}
    if key == "erp_design_save_scrap_decision":
        return {"detailId": data.detail_id, "decision": data.decision,
                "scrapInventoryId": data.scrap_inventory_id, "usedQuantity": str(data.used_quantity) if data.used_quantity is not None else None,
                "matchNote": data.match_note, "detailVersion": data.detail_version}
    if key == "erp_design_release_scrap_decision":
        return {"detailId": data.detail_id, "detailVersion": data.detail_version}
    if key == "erp_design_create_density":
        return {"density": {"materialMark": data.material_mark, "density": str(data.density)}}
    if key == "erp_design_update_density":
        return {"densityId": data.density_id, "density": {"materialMark": data.material_mark, "density": str(data.density)}}
    if key == "erp_design_delete_density":
        return {"densityId": data.id}
    if key in {"erp_design_create_group_rule", "erp_design_update_group_rule"}:
        rule = data.model_dump(by_alias=True, exclude={"confirm", "id"}, exclude_none=True)
        result = {"rule": rule}
        if key == "erp_design_update_group_rule":
            result["ruleId"] = data.id
        return result
    if key == "erp_design_toggle_group_rule":
        return {"ruleId": data.id, "status": data.status}
    if key == "erp_design_delete_group_rule":
        return {"ruleIds": [data.id]}
    if key in {"erp_design_create_group_keyword", "erp_design_update_group_keyword"}:
        keyword = data.model_dump(by_alias=True, exclude={"confirm", "id"}, exclude_none=True)
        result = {"keyword": keyword}
        if key == "erp_design_update_group_keyword":
            result["keywordId"] = data.id
        return result
    if key == "erp_design_delete_group_keyword":
        return {"keywordIds": [data.id]}
    if key == "erp_design_rename_standard_hardware":
        return {"relativePath": data.relative_path, "newFileName": data.new_file_name}
    if key == "erp_design_delete_standard_hardware":
        return {"relativePath": data.relative_path}
    if key == "erp_design_manage_change":
        return {"operation": data.operation, "changeId": data.change_id, "changeIds": data.change_ids or None,
                "payload": data.payload or None}
    if key == "erp_design_query_change_items":
        return {"operation": "list", "changeId": data.change_id, "itemIds": None, "payload": None}
    if key == "erp_design_manage_change_items":
        return {"operation": data.operation, "changeId": data.change_id, "itemIds": data.item_ids or None,
                "payload": data.payload or None}
    if key == "erp_design_manage_order":
        return {"operation": data.operation, "requestId": data.request_id, "approvalVersion": data.approval_version}
    if key == "erp_design_manage_order_draft_scrap":
        return {"operation": data.operation, "draftId": data.draft_id, "seq": data.seq,
                "payload": data.payload or None}
    if key == "erp_design_submit_upload_change":
        return {"payload": data.payload}
    if key == "erp_design_manage_mold_repair":
        return {"operation": data.operation, "exceptionId": data.exception_id, "batchId": data.batch_id,
                "groupToken": data.group_token, "orderId": data.order_id, "payload": data.payload or None}
    if key == "erp_design_manage_bom":
        return {"operation": data.operation, "bomIds": data.bom_ids or None, "payload": data.payload or None}
    if key == "erp_design_query_bom_shortage":
        return {"moldId": data.mold_id, "partId": data.part_id}
    if key == "erp_design_download_file":
        return data.model_dump(by_alias=True, exclude_none=True)
    raise DomainError("TOOL_UNKNOWN", "ERP 设计工具未实现", 403)


def execute_tool(db, user, key: str, arguments: dict, run=None):
    try:
        data = _INPUTS[key].model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "ERP 设计工具参数无效：" + error.errors()[0]["msg"]) from None
    if key in READ_TOOL_MAP:
        if key == "erp_design_query_bom_report":
            arguments = {"report": data.report, "query": data.query}
        elif key == "erp_design_get_record":
            arguments = {"resource": data.resource, "id": data.id}
        elif key == "erp_design_compare_drawing_versions":
            arguments = {"fromVersionId": data.from_version_id, "toVersionId": data.to_version_id}
        elif key == "erp_design_get_mold_repair_approval":
            arguments = {"batchId": data.batch_id}
        elif key == "erp_design_get_mold_repair_outsource_approval":
            arguments = {"approvalOrderId": data.approval_order_id}
        elif key == "erp_design_get_mold_repair_processor_response":
            arguments = {"groupToken": data.group_token, "orderId": data.order_id}
        else:
            arguments = {"query": data.query}
        return _result(call_mcp(READ_TOOL_MAP[key], arguments))
    if key in CONTROL_TOOL_MAP:
        if key == "erp_design_rematch_no_drawing":
            _owned_session(db, user, data.session_id)
        if key == "erp_design_download_file":
            result = _store_erp_download(db, user, run, call_design_control_mcp(CONTROL_TOOL_MAP[key], _control_arguments(key, data)))
            db.commit()
            return _result(result)
        if key in {"erp_design_upload_standard_hardware", "erp_design_upload_mold_repair_drawing", "erp_design_import_bom"}:
            file_id = data.file_ids[0] if key == "erp_design_upload_standard_hardware" else data.file_id
            if key == "erp_design_import_bom":
                source = files.uploaded_file(db, user, str(file_id))
                if Path(source.filename).suffix.lower() != ".xlsx":
                    raise DomainError("ERP_DESIGN_FILE_TYPE", "ERP BOM 导入仅支持 XLSX 文件")
            if key == "erp_design_upload_mold_repair_drawing":
                source = files.uploaded_file(db, user, str(file_id))
                if Path(source.filename).suffix.lower() != ".dxf":
                    raise DomainError("ERP_DESIGN_FILE_TYPE", "修模改模图纸上传仅支持 DXF 文件")
            selected_file_ids = data.file_ids if key == "erp_design_upload_standard_hardware" else [data.file_id]
            directory, paths = _temporary_files(db, user, run, selected_file_ids)
            try:
                if key == "erp_design_upload_standard_hardware":
                    control_args = {"filePaths": [str(path) for path in paths], "folderName": data.folder_name}
                elif key == "erp_design_upload_mold_repair_drawing":
                    control_args = {"filePath": str(paths[0]), "skipVision": data.skip_vision}
                else:
                    control_args = {"filePath": str(paths[0]), "moldId": data.mold_id}
                value = call_design_control_mcp(CONTROL_TOOL_MAP[key], control_args)
            finally:
                shutil.rmtree(directory, ignore_errors=True)
        else:
            value = call_design_control_mcp(CONTROL_TOOL_MAP[key], _control_arguments(key, data))
        record(db, user, "erp_design_mcp." + key.removeprefix("erp_design_"),
               str(getattr(data, "session_id", None) or getattr(data, "detail_id", None) or getattr(data, "id", "master")), {})
        db.commit()
        return _result(value)
    if key == "erp_design_parse_new_mold_upload":
        directory, path, source = _temporary_xlsx(db, user, run, data.file_id)
        try:
            value = call_mcp("parse_new_mold_design_file", {
                "filePath": str(path), "sheetType": data.sheet_type, "designOrderSubType": data.design_order_sub_type,
            })
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        session_id = value.get("sessionId") if isinstance(value, dict) else None
        if not isinstance(session_id, int) or session_id < 1:
            _failure("ERP 未返回有效上传会话编号")
        record(db, user, _SESSION_ACTION, str(session_id), {"file_id": str(source.id), "sheet_type": data.sheet_type})
        db.commit()
        return _result(value)
    _owned_session(db, user, data.session_id)
    if key == "erp_design_get_drawing_status":
        value = call_mcp("get_new_mold_upload_status", {"sessionId": data.session_id, "includeResult": data.include_result})
    elif key == "erp_design_get_upload_result":
        value = call_mcp("get_new_mold_upload_result", {"sessionId": data.session_id})
    elif key == "erp_design_validate_rows":
        value = call_mcp("validate_new_mold_design_rows", {"sessionId": data.session_id, "sheetType": data.sheet_type,
            "moldCode": data.mold_code, "previewRows": data.preview_rows, "pricingAlreadyEnriched": True})
    elif key == "erp_design_reprice_rows":
        value = call_mcp("reprice_new_mold_design_rows", {"sheetType": data.sheet_type,
            "moldCode": data.mold_code, "previewRows": data.preview_rows})
    elif key == "erp_design_get_approval_config":
        value = call_mcp("get_new_mold_approval_launch_config", {"sessionId": data.session_id})
    elif key == "erp_design_import_new_mold":
        validation = call_mcp("validate_new_mold_design_rows", {"sessionId": data.session_id, "sheetType": data.sheet_type,
            "moldCode": data.mold_code, "previewRows": data.preview_rows, "pricingAlreadyEnriched": True})
        if isinstance(validation, dict) and (validation.get("canImport") is False or validation.get("valid") is False or validation.get("errors")):
            raise DomainError("ERP_VALIDATION_FAILED", "ERP 校验未通过，不能导入。请处理明细错误后重新校验", 409)
        value = call_mcp("import_new_mold_design", {"sessionId": data.session_id, "sheetType": data.sheet_type,
            "moldCode": data.mold_code, "previewRows": data.preview_rows, "urgencyLevel": data.urgency_level,
            "expectedDate": data.expected_date, "purchaseReason": data.purchase_reason, "remark": data.remark,
            "allowDuplicate": data.allow_duplicate})
        record(db, user, "erp_design_mcp.imported", str(data.session_id), {"row_count": len(data.preview_rows)})
        db.commit()
    else:
        raise DomainError("TOOL_UNKNOWN", "ERP 设计工具未实现", 403)
    return _result(value)
