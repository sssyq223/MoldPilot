"""Allow-listed, read-only projections of a single ERP upload result.

Column selection is a tool argument, not a collection of prompt phrase rules.
Keep row identity and missing values; never join materials by their display name.
"""
from typing import Literal

# key: (display label, ERP aliases). Amounts are copied, never recalculated.
UPLOAD_FIELDS = {
    "material": ("材质", ("material_mark",)),
    "spec": ("规格", ("spec_raw",)),
    "shape": ("料型", ("material_shape",)),
    "quantity": ("请购数量", ("qty", "quantity")),
    "purchase_quantity": ("采购数量", ("purchase_quantity",)),
    "unit": ("单位", ("unit",)),
    "length": ("长 (mm)", ("length",)),
    "width": ("宽 (mm)", ("width",)),
    "height": ("厚/高 (mm)", ("height", "thickness")),
    "outer_diameter": ("外径 (mm)", ("outer_diameter", "outerDiameter")),
    "inner_diameter": ("内径 (mm)", ("inner_diameter", "innerDiameter")),
    "brand": ("品牌", ("brand",)),
    "idle_quantity": ("闲置匹配数量", ("idle_quantity", "idleQuantity")),
    "heat_treatment": ("热处理", ("heat_treatment", "heatTreatment")),
    "post_treatment": ("时效处理", ("post_treatment", "postTreatment")),
    "hardness": ("硬度 HRC", ("hardness",)),
    "density": ("密度", ("density",)),
    "unit_weight": ("单件毛重 (kg)", ("unit_weight", "unitWeight")),
    "unit_price": ("单价", ("unit_price", "unitPrice", "material_unit_price", "materialUnitPrice")),
    "approved_unit_price": ("有效已审批价", ("approved_unit_price", "approvedUnitPrice")),
    "accounting_unit_price": ("核算单价", ("accounting_unit_price", "accountingUnitPrice")),
    "material_amount": ("核算金额", ("accounting_amount", "accountingAmount", "material_amount", "materialAmount")),
    "total_price": ("总价", ("total_price", "totalPrice", "total_amount", "totalAmount")),
    "processing_technology": ("加工工艺", ("processing_technology", "processingTechnology")),
    "milling_surface": ("铣面", ("milling_surface", "millingSurface")),
    "grinding": ("研磨", ("grinding",)),
    "chamfer": ("倒角", ("chamfer_c", "chamferC")),
    "calculation": ("计算过程", ("calculation_process", "calculationProcess", "accounting_process", "accountingProcess")),
    "remark": ("备注", ("remark",)),
    "drawing": ("图纸", ("drawing_resource_id", "drawingResourceId", "drawing_id", "drawingId")),
    "match_status": ("图纸匹配状态", ("drawing_match_status", "drawingMatchStatus", "drawing_status", "drawingStatus", "drawing_processing_status", "drawingProcessingStatus")),
    "tolerance_tier": ("公差档位", ("tolerance_tier", "toleranceTier")),
    "length_allowed_range": ("长度允许范围", ("length_allowed_range", "lengthAllowedRange")),
    "width_allowed_range": ("宽度允许范围", ("width_allowed_range", "widthAllowedRange")),
    "thickness_allowed_range": ("厚度允许范围", ("thickness_allowed_range", "thicknessAllowedRange")),
    "diagonal_tolerance": ("对角公差", ("diagonal_tolerance", "diagonalTolerance")),
}
UploadField = Literal[tuple(UPLOAD_FIELDS)]
DEFAULT_UPLOAD_FIELDS = [
    "material", "spec", "shape", "purchase_quantity", "unit", "length", "width", "height",
    "outer_diameter", "inner_diameter", "processing_technology", "remark",
]


def project_upload_rows(rows, fields):
    """Return one ordered table, including unmatched drawings and null cells."""
    fields = list(dict.fromkeys(fields))
    columns = [
        {"key": "item_code_full", "label": "编码", "fields": ["item_code_full"]},
        {"key": "item_name", "label": "名称", "fields": ["item_name"]},
    ]
    for key in fields:
        label, _ = UPLOAD_FIELDS[key]
        columns.append({"key": key, "label": label, "fields": [key],
                        **({"kind": "preview"} if key == "drawing" else {})})
    projected = []
    for raw in rows:
        row = {key: raw.get(key) for key in ("rowIndex", "item_code_full", "item_name")}
        for key in fields:
            _, aliases = UPLOAD_FIELDS[key]
            value = next((raw.get(alias) for alias in aliases
                          if raw.get(alias) is not None and raw.get(alias) != ""), None)
            if key == "drawing":
                # Only an ERP resource id reaches the preview component. The
                # authenticated preview endpoint rechecks session ownership.
                try:
                    drawing_id = int(value)
                    if drawing_id <= 0 or float(value) != drawing_id:
                        drawing_id = None
                except (TypeError, ValueError, OverflowError):
                    drawing_id = None
                row["drawing_resource_id"] = drawing_id
                row["drawing_file_name"] = raw.get("drawing_file_name") or raw.get("drawingFileName") or ""
                row[key] = drawing_id
            else:
                row[key] = value
        projected.append(row)
    return columns, projected
