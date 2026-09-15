"""Deterministic XLSX material preview.

This parser is intentionally small and conservative. It reads only OOXML cell
values already present in the workbook package, never executes formulas, macros
or external links, and returns a draft `material_data` payload plus issues that
must be reviewed before any future material binding can become business
evidence.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile
from .errors import DomainError
from .material_rules import validate_contract, typed

NS = {
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
CELL_RE = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})$")
MAX_CELLS = 100_000
MAX_ROW = 1_048_576


@dataclass(frozen=True)
class Cell:
    value: str | bool | None
    formula: bool = False


def _issue(code, message, **extra):
    return {"code": code, "message": message, **{k: v for k, v in extra.items() if v is not None}}


def _col_index(letters: str) -> int:
    value = 0
    for ch in letters:
        value = value * 26 + ord(ch) - 64
    return value


def _col_letters(index: int) -> str:
    text = ""
    while index:
        index, rem = divmod(index - 1, 26)
        text = chr(65 + rem) + text
    return text


def _cell_ref(ref: str) -> tuple[int, int] | None:
    match = CELL_RE.fullmatch(str(ref).upper())
    if not match:
        return None
    return int(match.group(2)), _col_index(match.group(1))


def _read_xml(archive: ZipFile, name: str):
    try:
        return ET.fromstring(archive.read(name))
    except KeyError:
        raise DomainError("XLSX_STRUCTURE_INVALID", "Excel 文件缺少必要的工作簿结构", 400) from None
    except ET.ParseError:
        raise DomainError("XLSX_STRUCTURE_INVALID", "Excel 文件 XML 结构无效", 400) from None


def _shared_strings(archive: ZipFile):
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _read_xml(archive, "xl/sharedStrings.xml")
    return ["".join(t.text or "" for t in item.findall(".//s:t", NS)) for item in root.findall(".//s:si", NS)]


def _workbook_sheets(archive: ZipFile):
    book = _read_xml(archive, "xl/workbook.xml")
    rels = _read_xml(archive, "xl/_rels/workbook.xml.rels")
    targets = {}
    for rel in rels.findall("rel:Relationship", NS):
        target = rel.attrib.get("Target", "").lstrip("/")
        if target and not target.startswith("xl/"):
            target = "xl/" + target
        targets[rel.attrib.get("Id")] = target
    sheets = {}
    for sheet in book.findall(".//s:sheet", NS):
        name = sheet.attrib.get("name", "")
        rid = sheet.attrib.get(f"{{{NS['r']}}}id")
        if name and rid in targets:
            sheets[name] = targets[rid]
    if not sheets:
        raise DomainError("XLSX_STRUCTURE_INVALID", "Excel 文件没有可读取的工作表", 400)
    return sheets


def _load_sheet(archive: ZipFile, path: str, shared: list[str]):
    root = _read_xml(archive, path)
    cells: dict[tuple[int, int], Cell] = {}
    for c in root.findall(".//s:c", NS):
        ref = _cell_ref(c.attrib.get("r", ""))
        if not ref:
            continue
        if len(cells) >= MAX_CELLS:
            raise DomainError("XLSX_TOO_LARGE", "Excel 单元格数量超出预览限制", 413)
        formula = c.find("s:f", NS) is not None
        if formula:
            cells[ref] = Cell(None, True)
            continue
        kind = c.attrib.get("t")
        if kind == "inlineStr":
            value: str | bool | None = "".join(t.text or "" for t in c.findall(".//s:t", NS))
        else:
            raw = c.findtext("s:v", default="", namespaces=NS)
            if kind == "s":
                try:
                    value = shared[int(raw)]
                except (ValueError, IndexError):
                    value = ""
            elif kind == "b":
                value = raw == "1"
            else:
                value = raw
        cells[ref] = Cell(value)
    max_row = max((row for row, _ in cells), default=0)
    return {"cells": cells, "max_row": max_row}


def _cell(sheet, row: int, col: int) -> Cell:
    return sheet["cells"].get((row, col), Cell(None))


def _normalize(value, field):
    if value is None or value == "":
        raise ValueError("VALUE_MISSING")
    if field["type"] == "text":
        if not isinstance(value, str):
            value = str(value)
        value = value.strip()
        if not value:
            raise ValueError("VALUE_MISSING")
        typed(value, field)
        return value
    if field["type"] == "boolean":
        if type(value) is bool:
            normalized = value
        elif isinstance(value, str) and value.strip().casefold() in {"true", "false", "是", "否", "1", "0"}:
            normalized = value.strip().casefold() in {"true", "是", "1"}
        else:
            raise ValueError("VALUE_INVALID")
        typed(normalized, field)
        return normalized
    if field["type"] == "date":
        if not isinstance(value, str):
            raise ValueError("VALUE_INVALID")
        normalized = value.strip()
        typed(normalized, field)
        return date.fromisoformat(normalized).isoformat()
    if field["type"] in {"decimal", "money"}:
        normalized = str(value).strip()
        try:
            number = typed(normalized, field)
        except (ValueError, InvalidOperation):
            raise ValueError("VALUE_INVALID") from None
        return str(number)
    raise ValueError("VALUE_INVALID")


def _column_for(selector: str, header: dict[str, int]) -> int | None:
    selector = str(selector).strip()
    if re.fullmatch(r"[A-Za-z]{1,3}", selector):
        return _col_index(selector.upper())
    return header.get(selector.casefold())


def _mapping_invalid(message):
    raise DomainError("INVALID_XLSX_MAPPING", message, 400)


def validate_xlsx_mapping(contract: dict, mapping: dict):
    headers, tables = validate_contract(contract)
    if not isinstance(mapping, dict) or set(mapping) - {"sheet", "fields", "tables"}:
        _mapping_invalid("Excel 映射只能包含默认工作表、表头字段和明细表配置")
    if "sheet" in mapping and (not isinstance(mapping["sheet"], str) or not 1 <= len(mapping["sheet"].strip()) <= 80):
        _mapping_invalid("默认工作表名称无效")
    fields = mapping.get("fields", {})
    if not isinstance(fields, dict) or set(fields) - set(headers):
        _mapping_invalid("表头字段映射包含未登记字段")
    for key, cell in fields.items():
        if not isinstance(cell, str) or len(cell) > 100 or ("!" in cell and len(cell.split("!", 1)[0]) > 80):
            _mapping_invalid("表头字段单元格映射无效")
        target = cell.split("!", 1)[-1]
        if not _cell_ref(target):
            _mapping_invalid("表头字段单元格坐标无效")
    table_mapping = mapping.get("tables", {})
    if not isinstance(table_mapping, dict) or set(table_mapping) - set(tables):
        _mapping_invalid("明细表映射包含未登记明细表")
    for table_key, tm in table_mapping.items():
        if not isinstance(tm, dict) or set(tm) - {"sheet", "header_row", "first_data_row", "last_data_row", "columns", "row_id_column"}:
            _mapping_invalid("明细表映射结构无效")
        if "sheet" in tm and (not isinstance(tm["sheet"], str) or not 1 <= len(tm["sheet"].strip()) <= 80):
            _mapping_invalid("明细表工作表名称无效")
        header_row = tm.get("header_row", 1)
        first_data_row = tm.get("first_data_row", header_row + 1 if isinstance(header_row, int) else None)
        last_data_row = tm.get("last_data_row")
        if not isinstance(header_row, int) or not 1 <= header_row <= MAX_ROW:
            _mapping_invalid("表头行号无效")
        if not isinstance(first_data_row, int) or not header_row < first_data_row <= MAX_ROW:
            _mapping_invalid("首个数据行号必须大于表头行")
        if last_data_row is not None and (not isinstance(last_data_row, int) or not first_data_row <= last_data_row <= MAX_ROW):
            _mapping_invalid("末尾数据行号无效")
        columns = tm.get("columns", {})
        if columns is not None:
            if not isinstance(columns, dict) or set(columns) - set(tables[table_key]["fields"]):
                _mapping_invalid("明细列映射包含未登记字段")
            for selector in columns.values():
                if not isinstance(selector, str) or not 1 <= len(selector.strip()) <= 100:
                    _mapping_invalid("明细列映射值无效")
        row_id = tm.get("row_id_column")
        if row_id is not None and (not isinstance(row_id, str) or not 1 <= len(row_id.strip()) <= 100):
            _mapping_invalid("稳定行标识列映射无效")
    return mapping


def preview_xlsx(data: bytes, contract: dict, mapping: dict):
    validate_xlsx_mapping(contract, mapping)
    headers, tables = validate_contract(contract)
    try:
        with ZipFile(BytesIO(data)) as archive:
            names = set(archive.namelist())
            issues = []
            if any(name.startswith("xl/externalLinks/") for name in names):
                issues.append(_issue("EXTERNAL_LINK_NOT_ACCEPTED", "Excel 文件包含外部链接，预览不会读取外部数据"))
            shared = _shared_strings(archive)
            sheet_paths = _workbook_sheets(archive)
            workbook = {name: _load_sheet(archive, path, shared) for name, path in sheet_paths.items()}
    except BadZipFile:
        raise DomainError("XLSX_STRUCTURE_INVALID", "Excel 文件不是有效的 XLSX 包", 400) from None

    def sheet_named(name: str):
        sheet = workbook.get(name)
        if sheet is None:
            issues.append(_issue("SHEET_MISSING", "映射指定的工作表不存在", sheet=name))
        return sheet

    result = {"fields": {}, "tables": {}}
    field_mapping = mapping.get("fields", {}) if isinstance(mapping.get("fields", {}), dict) else {}
    default_sheet = mapping.get("sheet")
    for key, field in headers.items():
        ref = field_mapping.get(key)
        if not ref:
            issues.append(_issue("FIELD_MAPPING_MISSING", "表头字段缺少单元格映射", field=key, label=field["label"]))
            continue
        sheet_name, cell_ref = (ref.split("!", 1) if "!" in ref else (default_sheet, ref))
        sheet = sheet_named(sheet_name) if sheet_name else None
        pos = _cell_ref(cell_ref)
        if not sheet or not pos:
            issues.append(_issue("FIELD_MAPPING_INVALID", "表头字段映射无效", field=key, label=field["label"], cell=ref))
            continue
        cell = _cell(sheet, *pos)
        if cell.formula:
            issues.append(_issue("FORMULA_NOT_ACCEPTED", "单元格包含公式，预览不会使用公式或缓存值", field=key, cell=ref))
            continue
        try:
            result["fields"][key] = _normalize(cell.value, field)
        except ValueError as error:
            issues.append(_issue(str(error), "表头字段值缺失或类型不匹配", field=key, label=field["label"], cell=ref))

    table_mapping = mapping.get("tables", {}) if isinstance(mapping.get("tables", {}), dict) else {}
    for table_key, table in tables.items():
        tm = table_mapping.get(table_key)
        if not isinstance(tm, dict):
            issues.append(_issue("TABLE_MAPPING_MISSING", "明细表缺少 Excel 映射", table=table_key, label=table["definition"]["label"]))
            continue
        sheet = sheet_named(tm.get("sheet", default_sheet))
        if not sheet:
            continue
        header_row = int(tm.get("header_row", 1))
        first_data_row = int(tm.get("first_data_row", header_row + 1))
        last_data_row = int(tm.get("last_data_row", sheet["max_row"] or first_data_row - 1))
        header = {}
        for col in range(1, 300):
            cell = _cell(sheet, header_row, col)
            if cell.value not in {None, ""}:
                header[str(cell.value).strip().casefold()] = col
        configured = tm.get("columns", {}) if isinstance(tm.get("columns", {}), dict) else {}
        columns = {}
        for field_key, field in table["fields"].items():
            selector = configured.get(field_key, field["label"])
            col = _column_for(selector, header) or _column_for(field_key, header)
            if not col:
                issues.append(_issue("COLUMN_MISSING", "Excel 明细表缺少字段列", table=table_key, field=field_key, label=field["label"]))
            else:
                columns[field_key] = col
        if not columns:
            result["tables"][table_key] = []
            continue
        rows = []
        seen_ids = set()
        row_id_selector = tm.get("row_id_column")
        row_id_col = _column_for(row_id_selector, header) if row_id_selector else columns.get(next(iter(table["fields"])))
        for row_no in range(first_data_row, last_data_row + 1):
            raw_values = {field_key: _cell(sheet, row_no, col) for field_key, col in columns.items()}
            if all(cell.value in {None, ""} and not cell.formula for cell in raw_values.values()):
                continue
            values = {}
            for field_key, cell in raw_values.items():
                field = table["fields"][field_key]
                if cell.formula:
                    issues.append(_issue("FORMULA_NOT_ACCEPTED", "单元格包含公式，预览不会使用公式或缓存值", table=table_key, row=row_no, field=field_key, column=_col_letters(columns[field_key])))
                    continue
                try:
                    values[field_key] = _normalize(cell.value, field)
                except ValueError as error:
                    issues.append(_issue(str(error), "明细字段值缺失或类型不匹配", table=table_key, row=row_no, field=field_key, label=field["label"], column=_col_letters(columns[field_key])))
            row_id_cell = _cell(sheet, row_no, row_id_col) if row_id_col else Cell(None)
            row_id = str(row_id_cell.value).strip() if row_id_cell.value not in {None, ""} and not row_id_cell.formula else f"row:{row_no}"
            if row_id.startswith("row:"):
                issues.append(_issue("ROW_ID_FROM_POSITION", "明细行缺少稳定行标识，暂以行号作为待核对标识", table=table_key, row=row_no, row_id=row_id))
            if row_id in seen_ids:
                issues.append(_issue("ROW_ID_DUPLICATE", "明细行标识重复，不能直接用于审批条件", table=table_key, row=row_no, row_id=row_id))
            seen_ids.add(row_id)
            rows.append({"id": row_id[:100], "values": values})
        result["tables"][table_key] = rows
        if not rows:
            issues.append(_issue("ROWS_MISSING", "明细表没有可预览的数据行", table=table_key, label=table["definition"]["label"]))
    return {"material_data": result, "issues": issues, "status": "NEEDS_REVIEW" if issues else "READY_FOR_REVIEW"}
