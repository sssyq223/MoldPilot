"""Narrow bridge from MoldPilot's tool catalog to the ERP design-upload MCP.

The package owns ERP requests and returns ERP results.  This module never
copies design, drawing, BOM, purchase, or approval records into agent_db.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from base64 import b64decode
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, ValidationError, model_validator
from sqlalchemy import select

from domain_packs.mold import files, models as m
from agent_core.errors import DomainError
from agent_core.host_ports import host_ports
from agent_core.schemas import StrictModel
from domain_packs.mold.mcp_runtime import erp_design_upload_runtime


_host = host_ports()
object_storage = _host.object_storage
now = _host.now
record = _host.record
_RUNTIME_ROOT = erp_design_upload_runtime()
_ENV_FILE = _RUNTIME_ROOT / ".env"
_SERVER_FILE = _RUNTIME_ROOT / "node_modules" / "erp-design-upload-mcp" / "scripts" / "erp-design-upload-mcp.mjs"
_CONTROL_SERVER_FILE = _RUNTIME_ROOT / "scripts" / "erp-design-control-mcp.mjs"
_SESSION_ACTION = "erp_design_mcp.parsed"
_SHEET_TYPES = Literal["steel", "hardware"]
_PARSE_SHEET_TYPES = Literal["auto", "steel", "hardware"]
_MODIFY_MOLD_PURCHASE_REASON = Literal[
    "customer_change",
    "design_abnormal",
    "machining_abnormal",
    "assembly_abnormal",
    "trial_mold_abnormal",
    "outsource_abnormal",
    "process_improvement",
    "other_abnormal",
]
_DESIGN_FILE_EXTENSIONS = {".xlsx", ".xls", ".csv"}
_MOLD_REPAIR_FILENAME = re.compile(r"(?i)^M\d{6}-P")


TOOL_SPECS = {
    "erp_design_parse_new_mold_upload": {
        "description": "通过 ERP MCP 上传并解析新模设计清单，由 ERP 自动识别钢料或五金并创建上传会话；图纸处理请随后查询状态。",
        "permission": "design_route.create",
    },
    "erp_design_parse_modify_mold_upload": {
        "description": "通过 ERP 上传并解析修模改模采购清单，固定使用业务类型 repair_other（页面中的“改模”），由 ERP 自动识别钢料或五金并创建上传会话。",
        "permission": "design_route.create",
    },
    "erp_design_get_drawing_status": {
        "description": "查询 ERP 新模或改模设计上传会话的图纸匹配与归档处理状态；仅可查询本人发起的会话。",
        "permission": "design_route.read",
    },
    "erp_design_get_upload_result": {
        "description": "读取已完成 ERP 设计上传会话的完整解析结果与待核对明细；只读。",
        "permission": "design_route.read",
    },
    "erp_design_validate_rows": {
        "description": "使用 ERP 规则校验新模或改模设计上传明细，不创建请购或审批数据。",
        "permission": "design_route.create",
    },
    "erp_design_reprice_rows": {
        "description": "使用 D 盘 ERP 现有钢料价格、热处理与时效处理规则重新核算设计上传清单；时效仅对 ERP 支持的有效 45# 方料生效，不在 Agent 中另写价目规则。",
        "permission": "design_route.create",
    },
    "erp_design_evaluate_tolerances": {
        "description": "读取本人 ERP 新模或改模钢料上传会话中的公差表，按 ERP 现有档位规则计算方料的长度、宽度、厚度允许范围和对角公差；只读，不在 Agent 中维护另一份公差表。",
        "permission": "design_route.read",
    },
    "erp_design_preview_drawing": {
        "description": "预览当前 ERP 新模或改模上传会话中已经匹配的正式图纸；复用 ERP 的 DWG/DXF 预览结果，不重复处理图纸。",
        "permission": "design_route.read",
    },
    "erp_design_auto_correct_rows": {
        "description": "按 ERP 图纸识别结果整套修正清单料型、数量、长宽厚和直径；钢料修正后复用 ERP 现有核价接口。",
        "permission": "design_route.create",
    },
    "erp_design_get_approval_config": {
        "description": "读取 ERP 为新模设计上传会话返回的审批发起配置；只读。",
        "permission": "design_route.read",
    },
    "erp_design_get_modify_mold_approval_config": {
        "description": "读取 ERP 为修模改模清单上传会话返回的“设计修改模审批”发起配置；业务类型固定为 repair_other，只读。",
        "permission": "design_route.read",
    },
    "erp_design_import_new_mold": {
        "description": "在用户明确确认后，将已校验的新模设计明细导入 ERP 并创建 ERP 请购/审批数据；若 ERP 提示重复上传，须再次取得用户确认后才可传 allow_duplicate=true。",
        "permission": "design_route.execute",
        "write": True,
    },
    "erp_design_import_modify_mold": {
        "description": "在用户明确确认后，将已校验的修模改模设计清单以 repair_other 类型导入 ERP 并发起设计修改模审批；请购原因和交期必填，重复上传必须再次确认。",
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
    "erp_design_query_bom": {"description": "查询 ERP BOM 或物料清单明细。", "permission": "design_route.read"},
    "erp_design_query_bom_report": {"description": "查询 ERP BOM/物料清单汇总、物料、采购进度或待采购报表。", "permission": "design_route.read"},
    "erp_design_query_changes": {"description": "查询 ERP 设变申请及其流程状态。", "permission": "design_route.read"},
    "erp_design_query_standard_hardware": {"description": "查询 ERP 厂内标准件图纸目录。", "permission": "design_route.read"},
    "erp_design_query_master_data": {
        "description": "一次查询 ERP 设计基础资料：材质密度、设计分组规则和分组关键词；只读。",
        "permission": "design_route.read",
    },
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
    "erp_design_manage_density": "manage_design_density",
    "erp_design_manage_group_rule": "manage_design_group_rule",
    "erp_design_manage_group_keyword": "manage_design_group_keyword",
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
    "erp_design_confirm_mold_repair_quantity": "manage_mold_repair",
    "erp_design_submit_mold_repair_approval_batches": "manage_mold_repair",
    "erp_design_confirm_mold_repair_order_link": "manage_mold_repair",
    "erp_design_respond_mold_repair_processor": "manage_mold_repair",
    "erp_design_manage_bom": "manage_erp_bom",
    "erp_design_query_bom_shortage": "get_erp_bom_shortage",
    "erp_design_upload_mold_repair_drawing": "upload_mold_repair_drawing",
    "erp_design_import_bom": "import_erp_bom",
    "erp_design_download_file": "download_erp_design_file",
}
TOOL_SPECS.update({
    "erp_design_rematch_no_drawing": {"description": "在用户明确确认后，请 ERP 对本人新模或改模上传会话中的历史无图明细重新匹配图纸。", "permission": "design_route.execute", "write": True},
    "erp_design_update_order_item": {"description": "在用户明确确认后，修改 ERP 设计订单的一条可编辑明细，并保留修改原因。", "permission": "design_route.execute", "write": True},
    "erp_design_save_scrap_decision": {"description": "在用户明确确认后，为 ERP 设计订单明细保存闲置料使用决策。", "permission": "design_route.execute", "write": True},
    "erp_design_release_scrap_decision": {"description": "在用户明确确认后，释放 ERP 设计订单明细已占用的闲置料决策。", "permission": "design_route.execute", "write": True},
    "erp_design_manage_density": {"description": "在用户明确确认后，新增、修改或删除 ERP 设计材质密度。", "permission": "design_route.execute", "write": True},
    "erp_design_manage_group_rule": {"description": "在用户明确确认后，新增、修改、删除、启用或停用 ERP 设计分组与采购拆分规则。", "permission": "design_route.execute", "write": True},
    "erp_design_manage_group_keyword": {"description": "在用户明确确认后，新增、修改或删除 ERP 设计分组关键词。", "permission": "design_route.execute", "write": True},
    "erp_design_upload_standard_hardware": {"description": "在用户明确确认后，将当前会话选定的图纸附件上传到 ERP 厂内标准件目录。", "permission": "design_route.execute", "write": True},
    "erp_design_rename_standard_hardware": {"description": "在用户明确确认后，重命名 ERP 厂内标准件图纸。", "permission": "design_route.execute", "write": True},
    "erp_design_delete_standard_hardware": {"description": "在用户明确确认后，删除 ERP 厂内标准件图纸文件夹。", "permission": "design_route.execute", "write": True, "destructive": True},
    "erp_design_manage_change": {"description": "在用户明确确认后，创建、修改、删除、提交、评审或确认 ERP 设变申请。", "permission": "design_route.execute", "write": True},
    "erp_design_query_change_items": {"description": "查询指定 ERP 设变申请的明细。", "permission": "design_route.read"},
    "erp_design_manage_change_items": {"description": "在用户明确确认后维护或执行 ERP 设变明细。", "permission": "design_route.execute", "write": True},
    "erp_design_manage_order": {"description": "在用户明确确认后删除、审批或重新提交 ERP 设计订单。", "permission": "design_route.execute", "write": True},
    "erp_design_manage_order_draft_scrap": {"description": "在用户明确确认后保存或释放 ERP 设计草稿明细的闲置料决策。", "permission": "design_route.execute", "write": True},
    "erp_design_submit_upload_change": {"description": "在用户明确确认后，将已解析的设计上传清单提交为 ERP 变更请购。", "permission": "design_route.execute", "write": True},
    "erp_design_manage_mold_repair": {"description": "兼容既有调用的 ERP 修模改模通用办理工具；新调用优先使用数量确认、审批批次、订单关联和加工商响应专用工具。", "permission": "design_route.execute", "write": True},
    "erp_design_confirm_mold_repair_quantity": {"description": "在用户明确确认后，为一条 ERP 修模改模异常确认新图数量；数量必须是正整数，单位固定为 PCS。", "permission": "design_route.execute", "write": True},
    "erp_design_submit_mold_repair_approval_batches": {"description": "仅在 ERP 返回仍可提交的草稿批次且用户明确确认后，提交同一次修模改模图纸上传产生的一个或多个审批批次。普通上传已由 ERP 自动启动审批，不重复提交。", "permission": "design_route.execute", "write": True},
    "erp_design_confirm_mold_repair_order_link": {"description": "在用户明确确认后，把 ERP 修模改模异常与经过核对的采购单明细或委外单零件范围建立关联。", "permission": "design_route.execute", "write": True},
    "erp_design_respond_mold_repair_processor": {"description": "在当前加工商账号已收到对应图纸变更通知且用户明确确认后，向 ERP 提交同意改图或已加工异常反馈。", "permission": "design_route.execute", "write": True},
    "erp_design_manage_bom": {"description": "在用户明确确认后新增、修改或删除 ERP BOM。", "permission": "design_route.execute", "write": True},
    "erp_design_query_bom_shortage": {"description": "查询 ERP 指定模具及可选零件的 BOM 缺料情况。", "permission": "design_route.read"},
    "erp_design_upload_mold_repair_drawing": {"description": "在用户明确确认后，将当前会话中名称以 M+6位数字+-P 开头的 DXF 修模改模图纸上传至 ERP；ERP 对比正式零件图、生成异常并按现有规则自动启动可启动的设计主管审批。", "permission": "design_route.execute", "write": True},
    "erp_design_import_bom": {"description": "在用户明确确认后，将当前会话的 XLSX BOM 工艺清单导入指定 ERP 模具。", "permission": "design_route.execute", "write": True},
    "erp_design_download_file": {"description": "下载 ERP 图纸、标准件目录、修模图纸包或 BOM 导出文件，并保存为当前会话的私有附件。", "permission": "design_route.read"},
})

# These names are returned by the capability API and are the user-facing labels
# in the settings page.  Keep them next to the ERP tool catalogue so a newly
# registered tool cannot fall back to the generic "业务查询能力" label.
TOOL_NAMES = {
    "erp_design_parse_new_mold_upload": "解析新模设计上传清单",
    "erp_design_parse_modify_mold_upload": "解析改模设计上传清单",
    "erp_design_get_drawing_status": "查询设计上传图纸匹配状态",
    "erp_design_get_upload_result": "读取设计上传结果",
    "erp_design_validate_rows": "校验设计上传明细",
    "erp_design_reprice_rows": "核算 ERP 设计钢料价格",
    "erp_design_evaluate_tolerances": "判断 ERP 设计钢料公差",
    "erp_design_preview_drawing": "预览 ERP 设计上传图纸",
    "erp_design_auto_correct_rows": "按图纸自动修正清单参数",
    "erp_design_get_approval_config": "读取新模设计审批配置",
    "erp_design_get_modify_mold_approval_config": "读取改模设计审批配置",
    "erp_design_import_new_mold": "导入新模设计清单",
    "erp_design_import_modify_mold": "导入改模设计清单",
    "erp_design_query_orders": "查询 ERP 设计订单",
    "erp_design_query_drawing_versions": "查询 ERP 图纸版本",
    "erp_design_query_bom": "查询 ERP BOM",
    "erp_design_query_bom_report": "查询 ERP BOM 报表",
    "erp_design_query_changes": "查询 ERP 设计变更",
    "erp_design_query_standard_hardware": "查询 ERP 厂内标准件",
    "erp_design_query_master_data": "查询 ERP 设计基础资料",
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
    "erp_design_manage_density": "维护 ERP 材质密度",
    "erp_design_manage_group_rule": "维护 ERP 设计分组规则",
    "erp_design_manage_group_keyword": "维护 ERP 分组关键词",
    "erp_design_upload_standard_hardware": "上传 ERP 厂内标准件图纸",
    "erp_design_rename_standard_hardware": "重命名 ERP 厂内标准件图纸",
    "erp_design_delete_standard_hardware": "删除 ERP 厂内标准件图纸目录",
    "erp_design_manage_change": "办理 ERP 设计变更",
    "erp_design_query_change_items": "查询 ERP 设计变更明细",
    "erp_design_manage_change_items": "维护 ERP 设计变更明细",
    "erp_design_manage_order": "办理 ERP 设计订单",
    "erp_design_manage_order_draft_scrap": "维护设计订单草稿闲置料",
    "erp_design_submit_upload_change": "提交设计上传变更请购",
    "erp_design_manage_mold_repair": "办理修模改模图纸异常（兼容）",
    "erp_design_confirm_mold_repair_quantity": "确认修模改模新图数量",
    "erp_design_submit_mold_repair_approval_batches": "提交修模改模审批批次",
    "erp_design_confirm_mold_repair_order_link": "确认修模改模订单关联",
    "erp_design_respond_mold_repair_processor": "回复修模改模图纸变更",
    "erp_design_manage_bom": "维护 ERP BOM",
    "erp_design_query_bom_shortage": "查询 ERP BOM 缺料",
    "erp_design_upload_mold_repair_drawing": "上传修模改模图纸",
    "erp_design_import_bom": "导入 ERP BOM 工艺清单",
    "erp_design_download_file": "下载 ERP 设计资料",
}
for _tool_key, _tool_name in TOOL_NAMES.items():
    TOOL_SPECS[_tool_key]["name"] = _tool_name


class ParseInput(StrictModel):
    sheet_type: _PARSE_SHEET_TYPES = Field(
        default="auto",
        description="默认 auto，由 ERP 使用其现有清单解析规则识别钢料或五金；仅在人工明确指定时传 steel/hardware。",
    )
    file_id: UUID | None = Field(default=None, description="当前会话中的 XLSX、XLS 或 CSV 附件标识；未填写时自动使用唯一的设计清单附件。")
    design_order_sub_type: str | None = Field(default=None, max_length=100)


class ModifyMoldParseInput(StrictModel):
    sheet_type: _PARSE_SHEET_TYPES = Field(
        default="auto",
        description="默认 auto，由 ERP 自动识别钢料或五金；业务类型始终固定为页面中的“改模”(repair_other)。",
    )
    file_id: UUID | None = Field(default=None, description="当前会话中的 XLSX、XLS 或 CSV 改模采购清单附件；未填写时自动使用唯一的设计清单附件。")


class SessionInput(StrictModel):
    session_id: int = Field(ge=1)


class StatusInput(SessionInput):
    include_result: bool = False


class RowsInput(SessionInput):
    sheet_type: _SHEET_TYPES
    preview_rows: list[dict] = Field(min_length=1, max_length=1000)
    mold_code: str | None = Field(default=None, max_length=120)


class ToleranceInput(SessionInput):
    preview_rows: list[dict] | None = Field(
        default=None,
        max_length=1000,
        description="可选的当前已编辑钢料明细；省略时工具自行读取 ERP 会话的完整明细。",
    )


class PreviewDrawingInput(SessionInput):
    drawing_id: int = Field(ge=1, description="当前上传会话明细返回的 drawing_resource_id。")


class AutoCorrectRowsInput(RowsInput):
    row_numbers: list[int] = Field(
        default_factory=list,
        max_length=1000,
        description="按 rowIndex/row_index 指定要修正的行号；留空时修正所有带有图纸识别结果的行。",
    )


class ImportInput(RowsInput):
    confirm_import: Literal[True] = Field(description="仅在用户已经明确确认导入后设为 true。")
    urgency_level: Literal["normal", "important", "urgent"] = "normal"
    expected_date: str = Field(min_length=10, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$")
    purchase_reason: str | None = Field(default=None, max_length=200)
    remark: str | None = Field(default=None, max_length=1000)
    allow_duplicate: bool = Field(default=False, description="仅在 ERP 返回重复上传提示且用户再次明确确认后设为 true。")


class ModifyMoldImportInput(RowsInput):
    mold_code: str = Field(min_length=1, max_length=120, description="ERP 模具号；改模清单导入前必须核对。")
    confirm_import: Literal[True] = Field(description="仅在用户已经明确确认导入改模清单并发起审批后设为 true。")
    urgency_level: Literal["normal", "important", "urgent"] = "normal"
    expected_date: str = Field(
        min_length=10,
        max_length=10,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="改模采购交期，格式 YYYY-MM-DD；ERP 要求必填。",
    )
    purchase_reason: _MODIFY_MOLD_PURCHASE_REASON = Field(
        description="改模请购原因：客户设变、设计/加工/组立/试模/外协异常、制程改善或其他异常对应的 ERP 枚举。",
    )
    remark: str | None = Field(default=None, max_length=1000)
    allow_duplicate: bool = Field(default=False, description="仅在 ERP 返回重复上传提示且用户再次明确确认后设为 true。")


class QueryInput(StrictModel):
    query: dict = Field(default_factory=dict, max_length=30)

    @model_validator(mode="before")
    @classmethod
    def normalize_string_query(cls, value):
        if not isinstance(value, dict) or not isinstance(value.get("query"), str):
            return value
        mold_number = value["query"].strip()
        if not mold_number:
            return value
        return {**value, "query": {"moldNo": mold_number}}


class MasterDataQueryInput(StrictModel):
    """One read boundary for the three design-master-data catalogues."""

    material_mark: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        description="材质牌号；查询材质密度时优先使用，例如 CR12MOV。",
    )
    query: dict = Field(
        default_factory=dict,
        max_length=30,
        description="其他 ERP 筛选条件；材质密度优先使用顶层 material_mark。",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_material_query(cls, value):
        if not isinstance(value, dict) or not isinstance(value.get("query"), str):
            return value
        material_mark = value["query"].strip()
        if not material_mark:
            return value
        normalized = {**value, "material_mark": material_mark}
        normalized.pop("query", None)
        return normalized
    include: list[Literal["densities", "group_rules", "group_keywords"]] = Field(
        default_factory=lambda: ["densities", "group_rules", "group_keywords"],
        min_length=1,
        max_length=3,
        description="要返回的基础资料类别；省略时返回材质密度、分组规则和分组关键词。",
    )

    @model_validator(mode="after")
    def unique_include(self):
        if len(set(self.include)) != len(self.include):
            raise ValueError("基础资料类别不能重复")
        return self


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


class DensityManageInput(StrictModel):
    operation: Literal["create", "update", "delete"]
    id: int | None = Field(default=None, ge=1, description="修改或删除时的 ERP 密度记录 ID。")
    material_mark: str | None = Field(default=None, min_length=1, max_length=120)
    density: Decimal | None = Field(default=None, gt=0, le=100)
    confirm: Literal[True] = Field(description="仅在用户已经明确确认本次 ERP 材质密度维护后设为 true。")

    @model_validator(mode="after")
    def validate_operation(self):
        if self.operation in {"update", "delete"} and self.id is None:
            raise ValueError("修改或删除时必须提供密度记录 ID")
        if self.operation in {"create", "update"} and (self.material_mark is None or self.density is None):
            raise ValueError("维护材质密度时必须提供材质标识和密度")
        return self


class GroupRuleManageInput(StrictModel):
    operation: Literal["create", "update", "delete", "set_status"] = Field(
        description="新增、修改、删除；set_status 用于启用或停用分组规则。"
    )
    id: int | None = Field(default=None, ge=1, description="修改、删除或启停时的 ERP 分组规则 ID。")
    keyword_text: str | None = Field(default=None, min_length=1, max_length=120)
    category_name: str | None = Field(default=None, max_length=120)
    material_category: str | None = Field(default=None, max_length=120)
    match_scope: str | None = Field(default=None, min_length=1, max_length=80)
    group_prefix: str | None = Field(default=None, max_length=80)
    is_exact: int | None = Field(default=None, ge=0, le=1)
    force_split: int | None = Field(default=None, ge=0, le=1)
    priority: int | None = Field(default=None, ge=0, le=10000)
    status: Literal["active", "inactive"] | None = None
    remark: str | None = Field(default=None, max_length=500)
    confirm: Literal[True] = Field(description="仅在用户已经明确确认本次 ERP 分组规则维护后设为 true。")

    @model_validator(mode="after")
    def validate_operation(self):
        if self.operation in {"update", "delete", "set_status"} and self.id is None:
            raise ValueError("修改、删除或启停时必须提供记录 ID")
        if self.operation == "set_status" and self.status is None:
            raise ValueError("启用或停用分组规则时必须提供状态")
        if self.operation in {"create", "update"} and self.keyword_text is None:
            raise ValueError("维护分组规则时必须提供关键词")
        return self


class GroupKeywordManageInput(StrictModel):
    operation: Literal["create", "update", "delete"]
    id: int | None = Field(default=None, ge=1, description="修改或删除时的 ERP 分组关键词 ID。")
    keyword_text: str | None = Field(default=None, min_length=1, max_length=120)
    remark: str | None = Field(default=None, max_length=500)
    confirm: Literal[True] = Field(description="仅在用户已经明确确认本次 ERP 分组关键词维护后设为 true。")

    @model_validator(mode="after")
    def validate_operation(self):
        if self.operation in {"update", "delete"} and self.id is None:
            raise ValueError("修改或删除时必须提供分组关键词 ID")
        if self.operation in {"create", "update"} and self.keyword_text is None:
            raise ValueError("维护分组关键词时必须提供关键词")
        return self


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


class MoldRepairQuantityConfirmInput(StrictModel):
    exception_id: int = Field(ge=1, description="上传结果或审批详情返回的修模改模异常 ID。")
    new_qty: int = Field(gt=0, description="人工确认的新图数量，必须是正整数，单位固定为 PCS。")
    confirm: Literal[True] = Field(description="仅在用户已核对异常和数量并明确确认后设为 true。")


class MoldRepairApproverOverrideInput(StrictModel):
    node_code: Literal["DESIGN_MANAGER_APPROVAL"]
    role_ids: list[int] = Field(default_factory=list, max_length=20)
    user_ids: list[int] = Field(min_length=1, max_length=1)


class MoldRepairCategoryOverrideInput(StrictModel):
    exception_id: int = Field(ge=1)
    business_category: Literal["hardware", "attached_order"] = Field(
        description="hardware 表示五金采购/供应商，attached_order 表示附图订购/加工商。",
    )


class MoldRepairApprovalBatchesSubmitInput(StrictModel):
    batch_ids: list[int] = Field(
        min_length=1,
        max_length=100,
        description="同一次图纸上传返回且仍处于可提交草稿状态的审批批次 ID。",
    )
    approver_overrides: list[MoldRepairApproverOverrideInput] = Field(default_factory=list, max_length=1)
    category_overrides: list[MoldRepairCategoryOverrideInput] = Field(default_factory=list, max_length=100)
    confirm: Literal[True] = Field(description="仅在用户已核对所有批次、分类和审批人并明确确认后设为 true。")

    @model_validator(mode="after")
    def validate_batches(self):
        if len(set(self.batch_ids)) != len(self.batch_ids):
            raise ValueError("审批批次不能重复")
        exception_ids = [item.exception_id for item in self.category_overrides]
        if len(set(exception_ids)) != len(exception_ids):
            raise ValueError("同一异常不能重复指定接收方类型")
        return self


class MoldRepairOrderLinkConfirmInput(StrictModel):
    order_type: Literal["purchase_order", "entrust_outsource_order"] = Field(
        description="五金异常使用 purchase_order，附图订购异常使用 entrust_outsource_order。",
    )
    order_id: int = Field(ge=1)
    order_line_key: str = Field(
        min_length=1,
        max_length=160,
        description="ERP 候选订单返回的明细或委外范围键，不得自行拼造。",
    )
    exception_id: int = Field(ge=1)
    confirm: Literal[True] = Field(description="仅在用户已核对异常、订单和明细范围并明确确认后设为 true。")


class MoldRepairProcessorFeedbackItemInput(StrictModel):
    source_exception_id: int = Field(ge=1, description="原修模改模异常 ID。")
    processed_qty: float = Field(gt=0, description="已经加工的数量。")
    exception_type: str = Field(min_length=1, max_length=64)
    level: Literal["normal", "important", "urgent"] = "normal"
    description: str = Field(min_length=1, max_length=2000)
    handling: str | None = Field(default=None, max_length=1000)
    estimated_hours: float | None = Field(default=None, ge=0)
    attachments: str | None = Field(default=None, max_length=4000)


class MoldRepairProcessorResponseSubmitInput(StrictModel):
    group_token: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    order_id: int = Field(ge=1)
    response_type: Literal["agree", "processed_feedback"] = "agree"
    source_message_id: str = Field(
        min_length=1,
        max_length=255,
        description="ERP 发给当前加工商账号的原图纸变更通知消息 ID。",
    )
    items: list[MoldRepairProcessorFeedbackItemInput] = Field(default_factory=list, max_length=100)
    confirm: Literal[True] = Field(description="仅在当前加工商用户明确确认响应内容后设为 true。")

    @model_validator(mode="after")
    def validate_response(self):
        if self.response_type == "processed_feedback" and not self.items:
            raise ValueError("反馈已加工时至少需要一条已加工异常明细")
        if self.response_type == "agree" and self.items:
            raise ValueError("同意改图时不应提交已加工异常明细")
        exception_ids = [item.source_exception_id for item in self.items]
        if len(set(exception_ids)) != len(exception_ids):
            raise ValueError("同一原异常不能重复反馈")
        return self


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
    skip_vision: bool = Field(
        default=False,
        description="默认 false；只有用户明确要求跳过视觉识别或 ERP 运维明确指示时才设为 true。",
    )


class BomImportInput(SessionFileMutationInput):
    mold_id: int = Field(ge=1)


class DownloadInput(StrictModel):
    artifact: Literal["drawing_preview", "drawing_download", "standard_hardware_preview", "standard_hardware_folder",
                      "mold_repair_outsource_approval_drawing", "mold_repair_approval_drawing",
                      "mold_repair_entrust_order_drawing", "mold_repair_exception_drawing", "mold_repair_group_drawing",
                      "mold_repair_authorized_package", "bom_export"]
    drawing_id: int | None = Field(default=None, ge=1)
    preview_url: str | None = Field(default=None, min_length=1, max_length=500)
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
    "erp_design_parse_modify_mold_upload": ModifyMoldParseInput,
    "erp_design_get_drawing_status": StatusInput,
    "erp_design_get_upload_result": SessionInput,
    "erp_design_validate_rows": RowsInput,
    "erp_design_reprice_rows": RowsInput,
    "erp_design_evaluate_tolerances": ToleranceInput,
    "erp_design_preview_drawing": PreviewDrawingInput,
    "erp_design_auto_correct_rows": AutoCorrectRowsInput,
    "erp_design_get_approval_config": SessionInput,
    "erp_design_get_modify_mold_approval_config": SessionInput,
    "erp_design_import_new_mold": ImportInput,
    "erp_design_import_modify_mold": ModifyMoldImportInput,
    "erp_design_query_orders": QueryInput,
    "erp_design_query_drawing_versions": QueryInput,
    "erp_design_query_bom": QueryInput,
    "erp_design_query_bom_report": BomReportInput,
    "erp_design_query_changes": QueryInput,
    "erp_design_query_standard_hardware": QueryInput,
    "erp_design_query_master_data": MasterDataQueryInput,
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
    "erp_design_manage_density": DensityManageInput,
    "erp_design_manage_group_rule": GroupRuleManageInput,
    "erp_design_manage_group_keyword": GroupKeywordManageInput,
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
    "erp_design_confirm_mold_repair_quantity": MoldRepairQuantityConfirmInput,
    "erp_design_submit_mold_repair_approval_batches": MoldRepairApprovalBatchesSubmitInput,
    "erp_design_confirm_mold_repair_order_link": MoldRepairOrderLinkConfirmInput,
    "erp_design_respond_mold_repair_processor": MoldRepairProcessorResponseSubmitInput,
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
        try:
            for request in requests:
                process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except OSError:
            _failure("ERP 设计 MCP 通信中断，未取得处理结果", 503)
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


def _temporary_design_file(db, user, run, requested_id: UUID | None):
    if not run or run.user_id != user.id:
        raise DomainError("FILE_CONTEXT_INVALID", "设计上传须从当前会话的附件发起", 403)
    allowed_ids = {str(value) for value in db.scalars(select(m.RunFile.file_id).where(m.RunFile.run_id == run.id))}
    if requested_id:
        if str(requested_id) not in allowed_ids:
            raise DomainError("FILE_CONTEXT_INVALID", "所选文件不属于当前会话", 403)
        source = files.uploaded_file(db, user, str(requested_id))
    else:
        candidates = [files.uploaded_file(db, user, file_id) for file_id in allowed_ids]
        candidates = [item for item in candidates if Path(item.filename).suffix.lower() in _DESIGN_FILE_EXTENSIONS]
        if len(candidates) != 1:
            raise DomainError("ERP_DESIGN_FILE_REQUIRED", "请在当前会话附加且仅附加一份 XLSX、XLS 或 CSV 设计清单，或明确指定 file_id", 409)
        source = candidates[0]
    if Path(source.filename).suffix.lower() not in _DESIGN_FILE_EXTENSIONS:
        raise DomainError("ERP_DESIGN_FILE_TYPE", "ERP 设计清单上传仅支持 XLSX、XLS 或 CSV 文件")
    content = object_storage.read(source)
    filename, _ = files.validate_file(source.filename, content)
    directory = Path(tempfile.mkdtemp(prefix="moldpilot-erp-design-"))
    path = directory / filename
    path.write_bytes(content)
    return directory, path, source


def _temporary_files(db, user, run, file_ids: list[UUID], *, preserve_filename: bool = False):
    if not run or run.user_id != user.id:
        raise DomainError("FILE_CONTEXT_INVALID", "ERP 文件上传须从当前会话的附件发起", 403)
    allowed_ids = {str(value) for value in db.scalars(select(m.RunFile.file_id).where(m.RunFile.run_id == run.id))}
    requested_ids = [str(value) for value in file_ids]
    if len(set(requested_ids)) != len(requested_ids) or any(value not in allowed_ids for value in requested_ids):
        raise DomainError("FILE_CONTEXT_INVALID", "所选 ERP 上传文件不属于当前会话", 403)
    if preserve_filename and len(requested_ids) != 1:
        raise DomainError("ERP_DESIGN_FILE_REQUIRED", "保留原文件名的 ERP 上传一次只能处理一个附件", 409)
    directory = Path(tempfile.mkdtemp(prefix="moldpilot-erp-file-"))
    paths = []
    try:
        for index, file_id in enumerate(requested_ids, start=1):
            source = files.uploaded_file(db, user, file_id)
            filename, _ = files.validate_file(source.filename, object_storage.read(source))
            path = directory / (filename if preserve_filename else f"{index}-{filename}")
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


def _drawing_rows(value) -> list[dict]:
    if not isinstance(value, dict):
        return []
    source = value.get("data") if isinstance(value.get("data"), dict) else value
    rows = source.get("previewRows") if isinstance(source.get("previewRows"), list) else source.get("preview_rows")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _drawing_row(value, drawing_id: int) -> dict | None:
    for row in _drawing_rows(value):
        raw_id = row.get("drawing_resource_id") or row.get("drawingResourceId") or row.get("drawing_id") or row.get("drawingId")
        try:
            if int(raw_id) == drawing_id:
                return row
        except (TypeError, ValueError):
            continue
    return None


def _first(row: dict, *keys: str):
    for key in keys:
        if key in row and row[key] is not None and row[key] != "":
            return row[key]
    return None


def _number(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and result not in {float("inf"), float("-inf")} else None


def _set_pair(row: dict, snake: str, camel: str, value):
    row[snake] = value
    row[camel] = value


def _attached_order(row: dict) -> bool:
    return "附图订购" in str(_first(row, "material_type", "materialType") or "")


def _material_shape(row: dict, sheet_type: str) -> str:
    attached = _first(row, "attached_order_material_shape", "attachedOrderMaterialShape") if _attached_order(row) else None
    value = attached or _first(row, "material_shape", "materialShape")
    if value is None and sheet_type == "steel":
        value = _first(row, "material_type", "materialType")
    if value == "圆环":
        return "圆环料"
    if value in {"方料", "圆料", "圆环料"}:
        return str(value)
    if _first(row, "length") is not None and _first(row, "width") is not None:
        return "方料"
    if _first(row, "outer_diameter", "outerDiameter") is not None:
        return "圆环料" if _first(row, "inner_diameter", "innerDiameter") is not None else "圆料"
    return str(value or "")


def _tolerance_rules(value: dict) -> list[dict]:
    source = value.get("data") if isinstance(value.get("data"), dict) else value
    requirements = _first(source, "techRequirements", "tech_requirements")
    if not isinstance(requirements, dict):
        return []
    rules = _first(requirements, "tolerance_table", "toleranceTable")
    return [rule for rule in rules if isinstance(rule, dict)] if isinstance(rules, list) else []


def _tolerance_offsets(value) -> tuple[float, float] | None:
    matches = re.findall(r"[+-]?\d+(?:\.\d+)?", str(value or ""))
    if len(matches) < 2:
        return None
    offsets = (_number(matches[0]), _number(matches[1]))
    return offsets if offsets[0] is not None and offsets[1] is not None else None


def _allowed_range(nominal, tolerance) -> str:
    value = _number(nominal)
    offsets = _tolerance_offsets(tolerance)
    if value is None or value <= 0 or offsets is None:
        return "-"
    return f"{_dimension_text(value + offsets[0])}～{_dimension_text(value + offsets[1])}"


def _evaluate_steel_tolerances(rows: list[dict], rules: list[dict]) -> tuple[list[dict], int]:
    evaluated = deepcopy(rows)
    evaluated_count = 0
    for row in evaluated:
        result = {
            "tolerance_tier": "-",
            "length_allowed_range": "-",
            "width_allowed_range": "-",
            "thickness_allowed_range": "-",
            "diagonal_tolerance": "-",
        }
        if _material_shape(row, "steel") == "方料":
            length = _number(_first(row, "length"))
            width = _number(_first(row, "width"))
            if length is not None and width is not None and length > 0 and width > 0:
                specification = max(length, width)
                sequence = 1 if specification <= 500 else (2 if specification <= 800 else 3)
                rule = next((item for item in rules if _number(_first(item, "seq")) == sequence), None)
                if rule is None and sequence <= len(rules):
                    rule = rules[sequence - 1]
                if rule is not None:
                    length_tolerance = _first(rule, "length_tol", "lengthTol")
                    result = {
                        "tolerance_tier": str(_first(rule, "spec") or "-"),
                        "length_allowed_range": _allowed_range(length, length_tolerance),
                        "width_allowed_range": _allowed_range(width, length_tolerance),
                        "thickness_allowed_range": _allowed_range(
                            _first(row, "height", "thickness"),
                            _first(rule, "thick_tol", "thickTol"),
                        ),
                        "diagonal_tolerance": str(_first(rule, "diag_tol", "diagTol") or "-"),
                    }
                    evaluated_count += 1
        for snake, camel in [
            ("tolerance_tier", "toleranceTier"),
            ("length_allowed_range", "lengthAllowedRange"),
            ("width_allowed_range", "widthAllowedRange"),
            ("thickness_allowed_range", "thicknessAllowedRange"),
            ("diagonal_tolerance", "diagonalTolerance"),
        ]:
            _set_pair(row, snake, camel, result[snake])
    return evaluated, evaluated_count


def _expected_dimensions(row: dict) -> dict | None:
    value = _first(row, "drawing_dimension_value", "drawingDimensionValue")
    return value if isinstance(value, dict) else None


def _expected_shape(row: dict) -> str:
    explicit = str(_first(row, "drawing_material_shape", "drawingMaterialShape") or "")
    if explicit == "圆环":
        return "圆环料"
    if explicit in {"方料", "圆料", "圆环料"}:
        return explicit
    dimensions = _expected_dimensions(row) or {}
    if _first(dimensions, "length") is not None and _first(dimensions, "width") is not None:
        return "方料"
    if _first(dimensions, "outer_diameter", "outerDiameter") is not None:
        return "圆环料" if _first(dimensions, "inner_diameter", "innerDiameter") is not None else "圆料"
    return ""


def _clear_fields(row: dict, *names: str):
    for name in names:
        row.pop(name, None)


def _apply_shape(row: dict, sheet_type: str, shape: str):
    if _attached_order(row):
        _set_pair(row, "attached_order_material_shape", "attachedOrderMaterialShape", shape)
        row["attachedOrderShapeManuallyEdited"] = True
    else:
        _set_pair(row, "material_shape", "materialShape", shape)
        if sheet_type == "steel":
            _set_pair(row, "material_type", "materialType", shape)
    if shape == "方料":
        _set_pair(row, "outer_diameter", "outerDiameter", None)
        _set_pair(row, "inner_diameter", "innerDiameter", None)
    else:
        _set_pair(row, "length", "length", None)
        _set_pair(row, "width", "width", None)
        if shape == "圆料":
            _set_pair(row, "inner_diameter", "innerDiameter", None)
    if sheet_type == "hardware" and _attached_order(row) and shape != "圆料":
        for snake, camel in [
            ("attached_order_material_price", "attachedOrderMaterialPrice"),
            ("accounting_unit_price", "accountingUnitPrice"), ("unit_price", "unitPrice"),
            ("attached_order_total_price", "attachedOrderTotalPrice"), ("material_amount", "materialAmount"),
            ("accounting_amount", "accountingAmount"), ("process_unit_price", "processUnitPrice"),
            ("process_amount", "processAmount"), ("total_price", "totalPrice"),
            ("unit_weight", "unitWeight"), ("final_price_source", "finalPriceSource"),
        ]:
            _set_pair(row, snake, camel, None)
        row["density"] = None
        row["weight"] = None
        if shape == "方料":
            _set_pair(row, "calculation_process", "calculationProcess", "方料不参与自动核价")
            row["remark"] = "方料不参与自动核价"


def _row_quantity(row: dict) -> float:
    return _number(_first(row, "qty", "quantity")) or 0.0


def _sync_purchase_quantity(row: dict) -> float:
    quantity = _row_quantity(row)
    status = _first(row, "_scrap_decision_status", "scrapDecisionStatus", "scrap_decision_status")
    if status == "active":
        reserved = min(_number(_first(row, "_scrap_reserved_quantity", "scrapReservedQuantity", "scrap_reserved_quantity")) or 0, quantity)
        row["_scrap_reserved_quantity"] = reserved
        row["_scrap_purchase_quantity_after_deduction"] = max(0, quantity - reserved)
    elif status == "skipped":
        row["_scrap_purchase_quantity_after_deduction"] = quantity
    purchase = _number(_first(row, "_scrap_purchase_quantity_after_deduction", "purchaseQuantityAfterDeduction",
                              "purchase_quantity_after_deduction"))
    purchase = quantity if purchase is None else purchase
    _set_pair(row, "purchase_quantity", "purchaseQuantity", purchase)
    return purchase


def _dimension_text(value) -> str:
    number = _number(value) or 0
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _recalculate_steel_geometry(row: dict, sheet_type: str):
    shape = _material_shape(row, sheet_type)
    height = _number(row.get("height")) or 0
    density = _number(row.get("density")) or 0
    volume = 0.0
    if shape == "方料":
        volume = (_number(row.get("length")) or 0) * (_number(row.get("width")) or 0) * height
        row["spec_raw"] = f"{_dimension_text(row.get('length'))}L*{_dimension_text(row.get('width'))}W*{_dimension_text(row.get('height'))}T"
    elif shape == "圆料":
        outer = _number(_first(row, "outer_diameter", "outerDiameter")) or 0
        volume = 3.141592653589793 * (outer / 2) ** 2 * height
        row["spec_raw"] = f"Φ{_dimension_text(outer)}*{_dimension_text(height)}"
    elif shape == "圆环料":
        outer = _number(_first(row, "outer_diameter", "outerDiameter")) or 0
        inner = _number(_first(row, "inner_diameter", "innerDiameter")) or 0
        volume = 3.141592653589793 * ((outer / 2) ** 2 - (inner / 2) ** 2) * height
        row["spec_raw"] = f"Φ{_dimension_text(outer)}*{_dimension_text(inner)}*{_dimension_text(height)}"
    unit_weight = round(max(0, volume * density / 1_000_000), 4)
    _set_pair(row, "unit_weight", "unitWeight", unit_weight)
    row["weight"] = round(unit_weight * _row_quantity(row), 4)
    for snake, camel in [
        ("unit_price", "unitPrice"), ("material_unit_price", "materialUnitPrice"),
        ("material_amount", "materialAmount"), ("total_price", "totalPrice"),
        ("final_price_source", "finalPriceSource"), ("steel_surcharge_amount", "steelSurchargeAmount"),
        ("steel_base_unit_price", "steelBaseUnitPrice"), ("steel_surcharge_piece_fee", "steelSurchargePieceFee"),
    ]:
        _set_pair(row, snake, camel, None)


def _truthy(value) -> bool:
    return value is True or value == 1 or str(value or "").lower() == "true"


def _hardware_approved_unit_price(row: dict) -> float | None:
    if _attached_order(row):
        source = str(_first(row, "final_price_source", "finalPriceSource") or "")
        internal = _number(_first(row, "accounting_unit_price", "accountingUnitPrice", "unit_price", "unitPrice"))
        if source in {"attached_order_round_auto", "attached_manual_costing"} and internal is not None and internal > 0:
            return None
    explicit = _number(_first(row, "approved_unit_price", "approvedUnitPrice"))
    if explicit is not None:
        return explicit
    status = str(_first(row, "hardware_price_match_status", "hardwarePriceMatchStatus") or "").lower()
    price_id = _first(row, "hardware_price_id", "hardwarePriceId")
    if status != "matched" and price_id is None or status and status != "matched":
        return None
    return _number(_first(row, "unit_price", "unitPrice"))


def _recalculate_hardware_amounts(row: dict):
    quantity = _sync_purchase_quantity(row)
    if _truthy(_first(row, "paint_required", "paintRequired")):
        _set_pair(row, "accounting_amount", "accountingAmount", None)
        _set_pair(row, "total_price", "totalPrice", None)
        _set_pair(row, "paint_pricing_status", "paintPricingStatus", "pending_reprice")
        _set_pair(row, "calculation_process", "calculationProcess", "喷漆件信息已调整，确认导入时由 ERP 重新识别并计价")
        return
    approved = _hardware_approved_unit_price(row)
    if approved is not None:
        for snake, camel in [
            ("accounting_unit_price", "accountingUnitPrice"), ("accounting_amount", "accountingAmount"),
            ("attached_order_material_price", "attachedOrderMaterialPrice"),
            ("attached_order_total_price", "attachedOrderTotalPrice"), ("material_amount", "materialAmount"),
            ("process_unit_price", "processUnitPrice"), ("process_amount", "processAmount"),
        ]:
            _set_pair(row, snake, camel, None)
        _set_pair(row, "total_price", "totalPrice", round(approved * quantity, 2))
        return
    if _attached_order(row):
        values = {
            "material": _number(_first(row, "attached_order_material_price", "attachedOrderMaterialPrice")),
            "accounting": _number(_first(row, "accounting_unit_price", "accountingUnitPrice")),
            "process": _number(_first(row, "process_unit_price", "processUnitPrice")),
            "total": _number(_first(row, "attached_order_total_price", "attachedOrderTotalPrice")),
        }
        amounts = {key: None if value is None else round(value * quantity, 2) for key, value in values.items()}
        _set_pair(row, "material_amount", "materialAmount", amounts["material"])
        _set_pair(row, "accounting_amount", "accountingAmount", amounts["accounting"])
        _set_pair(row, "process_amount", "processAmount", amounts["process"])
        _set_pair(row, "total_price", "totalPrice", amounts["total"])
        calculation = str(_first(row, "calculation_process", "calculationProcess") or "")
        for label, value in [("核算金额", amounts["accounting"]), ("加工金额", amounts["process"]), ("总价", amounts["total"])]:
            if value is None or not calculation:
                continue
            replacement = f"{label}{value:g}"
            pattern = rf"{label}\s*[-+]?\d+(?:\.\d+)?"
            calculation = re.sub(pattern, replacement, calculation) if re.search(pattern, calculation) else f"{calculation}；{replacement}"
        _set_pair(row, "calculation_process", "calculationProcess", calculation)
        return
    unit_price = _number(_first(row, "unit_price", "unitPrice"))
    if unit_price is not None:
        _set_pair(row, "total_price", "totalPrice", round(unit_price * quantity, 2))


def _mark_drawing_fields_matched(row: dict, shape: str, dimensions: dict | None, quantity: float | None):
    if shape:
        _set_pair(row, "drawing_material_shape_match", "drawingMaterialShapeMatch", True)
        _set_pair(row, "drawing_material_shape_correction_message", "drawingMaterialShapeCorrectionMessage", "")
        _clear_fields(row, "drawing_material_shape_original", "drawingMaterialShapeOriginal")
    if dimensions:
        _set_pair(row, "drawing_dimension_match", "drawingDimensionMatch", True)
        _set_pair(row, "drawing_dimension_correction_message", "drawingDimensionCorrectionMessage", "")
        _clear_fields(row, "drawing_dimension_original", "drawingDimensionOriginal")
    if quantity is not None:
        _set_pair(row, "drawing_quantity_match", "drawingQuantityMatch", True)
        _set_pair(row, "drawing_quantity_correction_message", "drawingQuantityCorrectionMessage", "")
        _clear_fields(row, "drawing_quantity_original", "drawingQuantityOriginal")


def _auto_correct_rows(rows: list[dict], sheet_type: str, row_numbers: list[int]) -> tuple[list[dict], int, dict]:
    corrected = deepcopy(rows)
    selected = set(row_numbers)
    counts = {"material_shape": 0, "dimensions": 0, "quantity": 0}
    corrected_count = 0
    for index, row in enumerate(corrected, start=1):
        row_number = int(_number(_first(row, "rowIndex", "row_index")) or index)
        if selected and row_number not in selected:
            continue
        shape = _expected_shape(row)
        dimensions = _expected_dimensions(row)
        quantity = _number(_first(row, "drawing_quantity", "drawingQuantity"))
        if not shape and not dimensions and quantity is None:
            continue
        before = deepcopy(row)
        prior_shape = _material_shape(row, sheet_type)
        if shape:
            _apply_shape(row, sheet_type, shape)
            if prior_shape != shape:
                counts["material_shape"] += 1
        dimension_before = {key: _first(before, key, {"outer_diameter": "outerDiameter", "inner_diameter": "innerDiameter"}.get(key, key))
                            for key in ("length", "width", "outer_diameter", "inner_diameter", "height")}
        if dimensions:
            if shape == "方料":
                _set_pair(row, "length", "length", _number(_first(dimensions, "length")))
                _set_pair(row, "width", "width", _number(_first(dimensions, "width")))
                _set_pair(row, "outer_diameter", "outerDiameter", None)
                _set_pair(row, "inner_diameter", "innerDiameter", None)
            elif shape in {"圆料", "圆环料"}:
                _set_pair(row, "outer_diameter", "outerDiameter", _number(_first(dimensions, "outer_diameter", "outerDiameter")))
                inner = _number(_first(dimensions, "inner_diameter", "innerDiameter")) if shape == "圆环料" else None
                _set_pair(row, "inner_diameter", "innerDiameter", inner)
                _set_pair(row, "length", "length", None)
                _set_pair(row, "width", "width", None)
            if _first(dimensions, "height") is not None:
                _set_pair(row, "height", "height", _number(_first(dimensions, "height")))
            dimension_after = {key: _first(row, key, {"outer_diameter": "outerDiameter", "inner_diameter": "innerDiameter"}.get(key, key))
                               for key in dimension_before}
            if dimension_before != dimension_after:
                counts["dimensions"] += 1
        if quantity is not None:
            if _number(_first(row, "qty", "quantity")) != quantity:
                counts["quantity"] += 1
            _set_pair(row, "qty", "quantity", quantity)
        _sync_purchase_quantity(row)
        _mark_drawing_fields_matched(row, shape, dimensions, quantity)
        if sheet_type == "steel":
            _recalculate_steel_geometry(row, sheet_type)
        else:
            _recalculate_hardware_amounts(row)
        if row != before:
            corrected_count += 1
    return corrected, corrected_count, counts


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
    if key == "erp_design_manage_density":
        payload = ({"materialMark": data.material_mark, "density": str(data.density)}
                   if data.operation in {"create", "update"} else None)
        return {"operation": data.operation, "id": data.id, "payload": payload}
    if key == "erp_design_manage_group_rule":
        if data.operation == "set_status":
            payload = {"status": data.status}
        else:
            values = {
                "keywordText": data.keyword_text,
                "categoryName": data.category_name,
                "materialCategory": data.material_category,
                "matchScope": data.match_scope,
                "groupPrefix": data.group_prefix,
                "isExact": data.is_exact,
                "forceSplit": data.force_split,
                "priority": data.priority,
                "status": data.status,
                "remark": data.remark,
            }
            payload = {name: value for name, value in values.items() if value is not None}
            if data.operation == "create":
                payload.setdefault("matchScope", "material_name")
                payload.setdefault("isExact", 0)
                payload.setdefault("forceSplit", 0)
                payload.setdefault("priority", 100)
                payload.setdefault("status", "active")
        return {"operation": data.operation, "id": data.id, "payload": payload or None}
    if key == "erp_design_manage_group_keyword":
        payload = ({name: value for name, value in {
            "keywordText": data.keyword_text, "remark": data.remark,
        }.items() if value is not None} if data.operation in {"create", "update"} else None)
        return {"operation": data.operation, "id": data.id, "payload": payload}
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
    if key == "erp_design_confirm_mold_repair_quantity":
        return {
            "operation": "confirm_quantity",
            "exceptionId": data.exception_id,
            "batchId": None,
            "groupToken": None,
            "orderId": None,
            "payload": {"newQty": data.new_qty},
        }
    if key == "erp_design_submit_mold_repair_approval_batches":
        return {
            "operation": "submit_approval_batches",
            "exceptionId": None,
            "batchId": None,
            "groupToken": None,
            "orderId": None,
            "payload": {
                "batchIds": data.batch_ids,
                "approverOverrides": [
                    {
                        "nodeCode": item.node_code,
                        "roleIds": item.role_ids,
                        "userIds": item.user_ids,
                    }
                    for item in data.approver_overrides
                ],
                "categoryOverrides": [
                    {
                        "exceptionId": item.exception_id,
                        "businessCategory": item.business_category,
                    }
                    for item in data.category_overrides
                ],
            },
        }
    if key == "erp_design_confirm_mold_repair_order_link":
        return {
            "operation": "confirm_order_link",
            "exceptionId": None,
            "batchId": None,
            "groupToken": None,
            "orderId": None,
            "payload": {
                "orderType": data.order_type,
                "orderId": data.order_id,
                "orderLineKey": data.order_line_key,
                "exceptionId": data.exception_id,
            },
        }
    if key == "erp_design_respond_mold_repair_processor":
        return {
            "operation": "respond",
            "exceptionId": None,
            "batchId": None,
            "groupToken": data.group_token,
            "orderId": data.order_id,
            "payload": {
                "responseType": data.response_type,
                "sourceMessageId": data.source_message_id,
                "items": [
                    {
                        "sourceExceptionId": item.source_exception_id,
                        "processedQty": item.processed_qty,
                        "exceptionType": item.exception_type,
                        "level": item.level,
                        "description": item.description,
                        "handling": item.handling,
                        "estimatedHours": item.estimated_hours,
                        "attachments": item.attachments,
                    }
                    for item in data.items
                ],
            },
        }
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
    if key == "erp_design_preview_drawing":
        _owned_session(db, user, data.session_id)
        upload = call_mcp("get_new_mold_upload_status", {"sessionId": data.session_id, "includeResult": True})
        row = _drawing_row(upload, data.drawing_id)
        if row is None:
            upload = call_mcp("get_new_mold_upload_result", {"sessionId": data.session_id})
            row = _drawing_row(upload, data.drawing_id)
        if row is None:
            raise DomainError("ERP_DRAWING_NOT_IN_SESSION", "该图纸不属于当前上传会话", 404)
        preview_url = row.get("drawing_preview_url") or row.get("drawingPreviewUrl")
        downloaded = call_design_control_mcp("download_erp_design_file", {
            "artifact": "drawing_preview", "drawingId": data.drawing_id, "previewUrl": preview_url,
        })
        value = _store_erp_download(db, user, run, downloaded)
        db.commit()
        return _result(value)
    if key == "erp_design_query_master_data":
        reads = {
            "densities": "query_erp_design_densities",
            "group_rules": "query_erp_design_group_rules",
            "group_keywords": "query_erp_design_group_keywords",
        }
        query = dict(data.query)
        if data.material_mark:
            query["materialMark"] = data.material_mark
        value = {
            resource: call_mcp(reads[resource], {"query": query})
            for resource in data.include
        }
        return _result(value)
    if key in READ_TOOL_MAP:
        if key == "erp_design_query_bom_report":
            arguments = {"report": data.report, "query": data.query}
        elif key == "erp_design_query_orders":
            # The management-system order list searches mold numbers through
            # its generic ``keyword`` parameter.  QueryInput intentionally
            # accepts a short string for model ergonomics and normalizes it to
            # ``moldNo`` for the other ERP catalogues, so translate the common
            # mold aliases at this boundary instead of letting the ERP silently
            # ignore them and return an unfiltered page.
            query = dict(data.query)
            if not str(query.get("keyword") or "").strip():
                for alias in ("moldNo", "mold_no", "moldCode", "mold_code"):
                    candidate = str(query.get(alias) or "").strip()
                    if candidate:
                        query["keyword"] = candidate
                        break
            for alias in ("moldNo", "mold_no", "moldCode", "mold_code"):
                query.pop(alias, None)
            arguments = {"query": query}
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
                original_name = re.split(r"[\\/]", str(source.filename))[-1]
                if not _MOLD_REPAIR_FILENAME.match(original_name):
                    raise DomainError(
                        "ERP_MOLD_REPAIR_FILENAME",
                        f"修模改模图纸文件名不符合 ERP 规则：{original_name}；需以 M + 6 位数字 + -P 开头",
                    )
            selected_file_ids = data.file_ids if key == "erp_design_upload_standard_hardware" else [data.file_id]
            directory, paths = _temporary_files(
                db,
                user,
                run,
                selected_file_ids,
                preserve_filename=key in {"erp_design_upload_mold_repair_drawing", "erp_design_import_bom"},
            )
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
        resource_id = (
            getattr(data, "session_id", None)
            or getattr(data, "detail_id", None)
            or getattr(data, "request_id", None)
            or getattr(data, "id", None)
            or getattr(data, "relative_path", None)
            or "master"
        )
        record(db, user, "erp_design_mcp." + key.removeprefix("erp_design_"), str(resource_id), {})
        db.commit()
        return _result(value)
    if key in {"erp_design_parse_new_mold_upload", "erp_design_parse_modify_mold_upload"}:
        directory, path, source = _temporary_design_file(db, user, run, data.file_id)
        try:
            if key == "erp_design_parse_new_mold_upload":
                control_tool = "parse_new_mold_design_file_auto"
                control_arguments = {
                    "filePath": str(path),
                    "sheetType": data.sheet_type,
                    "designOrderSubType": data.design_order_sub_type,
                }
                design_order_type = "new_model"
            else:
                control_tool = "parse_modify_mold_design_file_auto"
                control_arguments = {"filePath": str(path), "sheetType": data.sheet_type}
                design_order_type = "repair_other"
            value = call_design_control_mcp(control_tool, control_arguments)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        session_id = value.get("sessionId") if isinstance(value, dict) else None
        if not isinstance(session_id, int) or session_id < 1:
            _failure("ERP 未返回有效上传会话编号")
        detected_sheet_type = value.get("sheetType") if isinstance(value, dict) else None
        if detected_sheet_type not in {"steel", "hardware"}:
            _failure("ERP 未返回有效的清单类型")
        record(db, user, _SESSION_ACTION, str(session_id), {
            "file_id": str(source.id),
            "sheet_type": detected_sheet_type,
            "design_order_type": design_order_type,
        })
        db.commit()
        return _result(value)
    _owned_session(db, user, data.session_id)
    if key == "erp_design_get_drawing_status":
        value = call_mcp("get_new_mold_upload_status", {"sessionId": data.session_id, "includeResult": data.include_result})
    elif key == "erp_design_get_upload_result":
        value = call_mcp("get_new_mold_upload_result", {"sessionId": data.session_id})
    elif key == "erp_design_evaluate_tolerances":
        source = call_mcp("get_new_mold_upload_result", {"sessionId": data.session_id})
        if not isinstance(source, dict):
            _failure("ERP 上传会话未返回有效结果")
        sheet_type = str(_first(source, "sheetType", "sheet_type") or "")
        if sheet_type and sheet_type != "steel":
            raise DomainError("ERP_TOLERANCE_STEEL_ONLY", "公差判断仅适用于 ERP 设计上传的钢料清单", 422)
        rules = _tolerance_rules(source)
        if not rules:
            raise DomainError("ERP_TOLERANCE_RULES_MISSING", "ERP 上传结果未返回钢料公差表", 409)
        rows = data.preview_rows if data.preview_rows is not None else _drawing_rows(source)
        if not rows:
            raise DomainError("ERP_TOLERANCE_ROWS_MISSING", "ERP 上传结果未返回可判断的钢料明细", 409)
        evaluated_rows, evaluated_count = _evaluate_steel_tolerances(rows, rules)
        value = dict(source)
        value.update({
            "sessionId": data.session_id,
            "sheetType": "steel",
            "previewRows": evaluated_rows,
            "toleranceEvaluation": {
                "evaluatedCount": evaluated_count,
                "rowCount": len(evaluated_rows),
                "ruleCount": len(rules),
                "source": "ERP techRequirements.tolerance_table",
            },
        })
    elif key == "erp_design_validate_rows":
        value = call_mcp("validate_new_mold_design_rows", {"sessionId": data.session_id, "sheetType": data.sheet_type,
            "moldCode": data.mold_code, "previewRows": data.preview_rows, "pricingAlreadyEnriched": True})
    elif key == "erp_design_reprice_rows":
        value = call_mcp("reprice_new_mold_design_rows", {"sheetType": data.sheet_type,
            "moldCode": data.mold_code, "previewRows": data.preview_rows})
    elif key == "erp_design_auto_correct_rows":
        corrected_rows, corrected_count, corrected_fields = _auto_correct_rows(
            data.preview_rows, data.sheet_type, data.row_numbers,
        )
        if data.sheet_type == "steel" and corrected_count:
            repriced = call_mcp("reprice_new_mold_design_rows", {
                "sheetType": data.sheet_type, "moldCode": data.mold_code, "previewRows": corrected_rows,
            })
            if not isinstance(repriced, dict):
                _failure("ERP 钢料重新核价未返回有效结果")
            value = dict(repriced)
            value.setdefault("previewRows", corrected_rows)
        else:
            value = {"previewRows": corrected_rows, "pricingAlreadyEnriched": True}
        value.update({
            "sessionId": data.session_id, "sheetType": data.sheet_type,
            "correctedCount": corrected_count, "correctedFields": corrected_fields,
        })
    elif key == "erp_design_get_approval_config":
        value = call_mcp("get_new_mold_approval_launch_config", {"sessionId": data.session_id})
    elif key == "erp_design_get_modify_mold_approval_config":
        value = call_design_control_mcp("get_modify_mold_approval_launch_config", {"sessionId": data.session_id})
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
    elif key == "erp_design_import_modify_mold":
        validation = call_mcp("validate_new_mold_design_rows", {
            "sessionId": data.session_id,
            "sheetType": data.sheet_type,
            "moldCode": data.mold_code,
            "previewRows": data.preview_rows,
            "pricingAlreadyEnriched": True,
        })
        if isinstance(validation, dict) and (
            validation.get("canImport") is False
            or validation.get("valid") is False
            or validation.get("errors")
        ):
            raise DomainError("ERP_VALIDATION_FAILED", "ERP 校验未通过，不能导入。请处理明细错误后重新校验", 409)
        value = call_design_control_mcp("import_modify_mold_design", {
            "sessionId": data.session_id,
            "sheetType": data.sheet_type,
            "moldCode": data.mold_code,
            "previewRows": data.preview_rows,
            "urgencyLevel": data.urgency_level,
            "expectedDate": data.expected_date,
            "purchaseReason": data.purchase_reason,
            "remark": data.remark,
            "allowDuplicate": data.allow_duplicate,
        })
        record(db, user, "erp_design_mcp.modify_mold_imported", str(data.session_id), {
            "row_count": len(data.preview_rows),
            "purchase_reason": data.purchase_reason,
        })
        db.commit()
    else:
        raise DomainError("TOOL_UNKNOWN", "ERP 设计工具未实现", 403)
    return _result(value)
