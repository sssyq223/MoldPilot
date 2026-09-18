#!/usr/bin/env node

import { env, stdin, stdout, stderr } from "node:process";
import fsp from "node:fs/promises";
import path from "node:path";

const SERVER_NAME = "erp-design-control";
const SERVER_VERSION = "1.0.0";
const DEFAULT_BASE_URL = "http://127.0.0.1:9099";
const DEFAULT_TIMEOUT_MS = 30000;
const MAX_RESPONSE_BYTES = 2_000_000;
const MAX_FILE_BYTES = 20 * 1024 * 1024;

const tools = [
  {
    name: "rematch_new_mold_upload_drawings",
    description: "Retry ERP drawing matching for historical no-drawing rows in one new-mold upload session.",
    inputSchema: { type: "object", additionalProperties: false, properties: { sessionId: { type: "integer", minimum: 1 } }, required: ["sessionId"] }
  },
  {
    name: "update_design_order_item",
    description: "Update one editable ERP design-order item and preserve the supplied modification reason.",
    inputSchema: { type: "object", additionalProperties: false, properties: {
      detailId: { type: "integer", minimum: 1 }, materialMark: { type: ["string", "null"] },
      specification: { type: ["string", "null"] }, modifyReason: { type: "string", minLength: 1 }
    }, required: ["detailId", "modifyReason"] }
  },
  {
    name: "save_design_order_scrap_decision",
    description: "Save an ERP design-order item's idle-material decision using its optimistic detail version.",
    inputSchema: { type: "object", additionalProperties: false, properties: {
      detailId: { type: "integer", minimum: 1 }, decision: { type: "string", enum: ["use", "partial", "skip"] },
      scrapInventoryId: { type: ["integer", "null"], minimum: 1 }, usedQuantity: { type: ["string", "number", "null"] },
      matchNote: { type: ["string", "null"] }, detailVersion: { type: "string", minLength: 1 }
    }, required: ["detailId", "decision", "detailVersion"] }
  },
  {
    name: "release_design_order_scrap_decision",
    description: "Release an ERP design-order item's idle-material reservation using its optimistic detail version.",
    inputSchema: { type: "object", additionalProperties: false, properties: {
      detailId: { type: "integer", minimum: 1 }, detailVersion: { type: "string", minLength: 1 }
    }, required: ["detailId", "detailVersion"] }
  },
  {
    name: "create_design_density",
    description: "Create one ERP design material-density record.",
    inputSchema: { type: "object", additionalProperties: false, properties: { density: { type: "object" } }, required: ["density"] }
  },
  {
    name: "update_design_density",
    description: "Update one ERP design material-density record.",
    inputSchema: { type: "object", additionalProperties: false, properties: { densityId: { type: "integer", minimum: 1 }, density: { type: "object" } }, required: ["densityId", "density"] }
  },
  {
    name: "delete_design_density",
    description: "Delete one ERP design material-density record.",
    inputSchema: { type: "object", additionalProperties: false, properties: { densityId: { type: "integer", minimum: 1 } }, required: ["densityId"] }
  },
  {
    name: "create_design_group_rule",
    description: "Create one ERP design grouping and procurement-split rule.",
    inputSchema: { type: "object", additionalProperties: false, properties: { rule: { type: "object" } }, required: ["rule"] }
  },
  {
    name: "update_design_group_rule",
    description: "Update one ERP design grouping and procurement-split rule.",
    inputSchema: { type: "object", additionalProperties: false, properties: { ruleId: { type: "integer", minimum: 1 }, rule: { type: "object" } }, required: ["ruleId", "rule"] }
  },
  {
    name: "toggle_design_group_rule",
    description: "Enable or disable one ERP design grouping and procurement-split rule.",
    inputSchema: { type: "object", additionalProperties: false, properties: { ruleId: { type: "integer", minimum: 1 }, status: { type: "string", enum: ["active", "inactive"] } }, required: ["ruleId", "status"] }
  },
  {
    name: "delete_design_group_rule",
    description: "Delete one or more ERP design grouping and procurement-split rules.",
    inputSchema: { type: "object", additionalProperties: false, properties: { ruleIds: { type: "array", items: { type: "integer", minimum: 1 }, minItems: 1 } }, required: ["ruleIds"] }
  },
  {
    name: "create_design_group_keyword",
    description: "Create one ERP design grouping keyword.",
    inputSchema: { type: "object", additionalProperties: false, properties: { keyword: { type: "object" } }, required: ["keyword"] }
  },
  {
    name: "update_design_group_keyword",
    description: "Update one ERP design grouping keyword.",
    inputSchema: { type: "object", additionalProperties: false, properties: { keywordId: { type: "integer", minimum: 1 }, keyword: { type: "object" } }, required: ["keywordId", "keyword"] }
  },
  {
    name: "delete_design_group_keyword",
    description: "Delete one or more ERP design grouping keywords.",
    inputSchema: { type: "object", additionalProperties: false, properties: { keywordIds: { type: "array", items: { type: "integer", minimum: 1 }, minItems: 1 } }, required: ["keywordIds"] }
  },
  {
    name: "upload_standard_hardware_drawings",
    description: "Upload selected local drawing files to one ERP internal standard-hardware folder.",
    inputSchema: { type: "object", additionalProperties: false, properties: {
      filePaths: { type: "array", minItems: 1, items: { type: "string", minLength: 1 } }, folderName: { type: "string", minLength: 1 }
    }, required: ["filePaths", "folderName"] }
  },
  {
    name: "rename_standard_hardware_drawing",
    description: "Rename one ERP internal standard-hardware drawing or folder entry.",
    inputSchema: { type: "object", additionalProperties: false, properties: {
      relativePath: { type: "string", minLength: 1 }, newFileName: { type: "string", minLength: 1 }
    }, required: ["relativePath", "newFileName"] }
  },
  {
    name: "delete_standard_hardware_folder",
    description: "Delete one ERP internal standard-hardware folder.",
    inputSchema: { type: "object", additionalProperties: false, properties: { relativePath: { type: "string", minLength: 1 } }, required: ["relativePath"] }
  },
  {
    name: "manage_design_change",
    description: "Create, update, delete, submit, review, or confirm an ERP design-change application through its fixed ERP routes.",
    inputSchema: { type: "object", additionalProperties: false, properties: { operation: { type: "string", enum: ["create", "update", "delete", "submit", "review", "confirm"] }, changeId: { type: ["integer", "null"], minimum: 1 }, changeIds: { type: ["array", "null"], items: { type: "integer", minimum: 1 } }, payload: { type: ["object", "null"] } }, required: ["operation"] }
  },
  {
    name: "manage_design_change_items",
    description: "List, create, batch create, update, delete, or execute ERP design-change items through their fixed ERP routes.",
    inputSchema: { type: "object", additionalProperties: false, properties: { operation: { type: "string", enum: ["list", "create", "batch_create", "update", "delete", "execute"] }, changeId: { type: ["integer", "null"], minimum: 1 }, itemIds: { type: ["array", "null"], items: { type: "integer", minimum: 1 } }, payload: { type: ["object", "null"] } }, required: ["operation"] }
  },
  {
    name: "manage_design_order",
    description: "Delete, approve, or resubmit one ERP design order through its fixed ERP routes.",
    inputSchema: { type: "object", additionalProperties: false, properties: { operation: { type: "string", enum: ["delete", "approve", "resubmit"] }, requestId: { type: "integer", minimum: 1 }, approvalVersion: { type: ["string", "null"], minLength: 1 } }, required: ["operation", "requestId"] }
  },
  {
    name: "manage_design_order_draft_scrap",
    description: "Save or release the idle-material decision of an ERP design-order draft item.",
    inputSchema: { type: "object", additionalProperties: false, properties: { operation: { type: "string", enum: ["save", "release"] }, draftId: { type: "integer", minimum: 1 }, seq: { type: "integer", minimum: 1 }, payload: { type: ["object", "null"] } }, required: ["operation", "draftId", "seq"] }
  },
  {
    name: "submit_design_upload_change",
    description: "Submit a parsed ERP design-upload change request.",
    inputSchema: { type: "object", additionalProperties: false, properties: { payload: { type: "object" } }, required: ["payload"] }
  },
  {
    name: "manage_mold_repair",
    description: "Confirm a repair quantity, submit repair approvals, link an order, or send a processor response using fixed ERP mold-repair routes.",
    inputSchema: { type: "object", additionalProperties: false, properties: { operation: { type: "string", enum: ["confirm_quantity", "submit_approval", "confirm_order_link", "respond", "submit_approval_batches"] }, exceptionId: { type: ["integer", "null"], minimum: 1 }, batchId: { type: ["integer", "null"], minimum: 1 }, groupToken: { type: ["string", "null"], minLength: 1 }, orderId: { type: ["integer", "null"], minimum: 1 }, payload: { type: ["object", "null"] } }, required: ["operation"] }
  },
  {
    name: "manage_erp_bom",
    description: "Create, update, or delete ERP BOM records through fixed ERP routes.",
    inputSchema: { type: "object", additionalProperties: false, properties: { operation: { type: "string", enum: ["create", "update", "delete"] }, bomIds: { type: ["array", "null"], items: { type: "integer", minimum: 1 } }, payload: { type: ["object", "null"] } }, required: ["operation"] }
  },
  {
    name: "get_erp_bom_shortage",
    description: "Read ERP BOM shortage information for one mold.",
    inputSchema: { type: "object", additionalProperties: false, properties: { moldId: { type: "integer", minimum: 1 }, partId: { type: ["integer", "null"], minimum: 1 } }, required: ["moldId"] }
  },
  {
    name: "upload_mold_repair_drawing",
    description: "Upload one local mold-repair drawing to ERP and create its repair exception analysis.",
    inputSchema: { type: "object", additionalProperties: false, properties: { filePath: { type: "string", minLength: 1 }, skipVision: { type: "boolean" } }, required: ["filePath"] }
  },
  {
    name: "import_erp_bom",
    description: "Import one local Excel BOM workbook into a specified ERP mold.",
    inputSchema: { type: "object", additionalProperties: false, properties: { moldId: { type: "integer", minimum: 1 }, filePath: { type: "string", minLength: 1 } }, required: ["moldId", "filePath"] }
  },
  {
    name: "download_erp_design_file",
    description: "Download a fixed ERP design drawing, drawing package, standard-hardware folder, or BOM export as a private MoldPilot file artifact.",
    inputSchema: { type: "object", additionalProperties: false, properties: { artifact: { type: "string", enum: ["drawing_preview", "drawing_download", "standard_hardware_preview", "standard_hardware_folder", "mold_repair_outsource_approval_drawing", "mold_repair_approval_drawing", "mold_repair_entrust_order_drawing", "mold_repair_exception_drawing", "mold_repair_group_drawing", "mold_repair_authorized_package", "bom_export"] }, drawingId: { type: ["integer", "null"], minimum: 1 }, relativePath: { type: ["string", "null"], minLength: 1 }, approvalOrderId: { type: ["integer", "null"], minimum: 1 }, exceptionId: { type: ["integer", "null"], minimum: 1 }, batchId: { type: ["integer", "null"], minimum: 1 }, orderId: { type: ["integer", "null"], minimum: 1 }, groupToken: { type: ["string", "null"], minLength: 1 }, kind: { type: ["string", "null"], minLength: 1 }, routeType: { type: ["string", "null"], minLength: 1 }, orderNo: { type: ["string", "null"] }, partnerId: { type: ["integer", "null"], minimum: 1 }, moldId: { type: ["integer", "null"], minimum: 1 } }, required: ["artifact"] }
  }
];

function log(message) {
  stderr.write(`[${SERVER_NAME}] ${message}\n`);
}

function readSettings() {
  const configured = String(env.ERP_DESIGN_UPLOAD_BASE_URL || DEFAULT_BASE_URL).trim();
  const baseUrl = configured.replace(/\/+$/, "");
  let parsed;
  try {
    parsed = new URL(baseUrl);
  } catch {
    throw new Error("ERP design base URL is invalid.");
  }
  if (!/^https?:$/.test(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("ERP design base URL must be a plain HTTP(S) origin.");
  }
  const token = String(env.ERP_DESIGN_UPLOAD_TOKEN || "").trim();
  if (!token) throw new Error("ERP design access token is not configured.");
  const timeoutMs = Number(env.ERP_DESIGN_UPLOAD_TIMEOUT_MS || DEFAULT_TIMEOUT_MS);
  if (!Number.isFinite(timeoutMs) || timeoutMs < 1000 || timeoutMs > 900000) {
    throw new Error("ERP design timeout must be between 1000 and 900000 milliseconds.");
  }
  return { baseUrl, token, timeoutMs };
}

function positiveInteger(value, name) {
  if (!Number.isInteger(value) || value < 1) throw new Error(`${name} must be a positive integer.`);
}

function object(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${name} must be an object.`);
}

function ids(value, name) {
  if (!Array.isArray(value) || value.length < 1 || value.some((item) => !Number.isInteger(item) || item < 1)) {
    throw new Error(`${name} must contain positive integer IDs.`);
  }
}

function normalizeToken(token) {
  return /^Bearer\s+/i.test(token) ? token : `Bearer ${token}`;
}

async function requestErp({ method, route, body, formData, searchParams }) {
  const { baseUrl, token, timeoutMs } = readSettings();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const url = new URL(`${baseUrl}${route}`);
    for (const [key, value] of Object.entries(searchParams || {})) {
      if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
    }
    const headers = { Authorization: normalizeToken(token), Accept: "application/json" };
    if (!formData) headers["Content-Type"] = "application/json";
    const response = await fetch(url, {
      method,
      headers,
      body: formData || (body === undefined ? undefined : JSON.stringify(body)),
      signal: controller.signal,
      redirect: "error"
    });
    const length = Number(response.headers.get("content-length") || 0);
    if (length > MAX_RESPONSE_BYTES) throw new Error("ERP response is too large.");
    const raw = await response.text();
    if (raw.length > MAX_RESPONSE_BYTES) throw new Error("ERP response is too large.");
    let payload;
    try {
      payload = raw ? JSON.parse(raw) : {};
    } catch {
      throw new Error(`ERP ${method} ${route} returned invalid JSON.`);
    }
    const message = payload?.msg || payload?.message || response.statusText || "ERP request failed";
    if (!response.ok || payload?.success === false || (typeof payload?.code === "number" && payload.code !== 200)) {
      throw new Error(`ERP ${method} ${route} failed (${response.status}): ${message}`);
    }
    return Object.hasOwn(payload || {}, "data") ? payload.data : payload;
  } catch (caught) {
    if (caught?.name === "AbortError") throw new Error(`ERP ${method} ${route} timed out.`);
    throw caught;
  } finally {
    clearTimeout(timer);
  }
}

function filenameFromDisposition(value, fallback) {
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(value || "")?.[1];
  if (encoded) {
    try { return decodeURIComponent(encoded).replace(/[\\/:\x00-\x1f]/g, "_").slice(0, 200) || fallback; } catch { return fallback; }
  }
  const simple = /filename="?([^";]+)"?/i.exec(value || "")?.[1];
  return (simple || fallback).replace(/[\\/:\x00-\x1f]/g, "_").slice(0, 200);
}

async function requestErpFile({ method, route, formData, searchParams, fallbackName }) {
  const { baseUrl, token, timeoutMs } = readSettings();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const url = new URL(`${baseUrl}${route}`);
    for (const [key, value] of Object.entries(searchParams || {})) {
      if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
    }
    const response = await fetch(url, { method, headers: { Authorization: normalizeToken(token), Accept: "application/octet-stream" }, body: formData, signal: controller.signal, redirect: "error" });
    const length = Number(response.headers.get("content-length") || 0);
    if (length > MAX_FILE_BYTES) throw new Error("ERP file exceeds the 20 MB MoldPilot artifact limit.");
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.length > MAX_FILE_BYTES) throw new Error("ERP file exceeds the 20 MB MoldPilot artifact limit.");
    if (!response.ok) {
      const text = new TextDecoder().decode(bytes).slice(0, 1000);
      throw new Error(`ERP ${method} ${route} failed (${response.status}): ${text || response.statusText}`);
    }
    return { fileName: filenameFromDisposition(response.headers.get("content-disposition"), fallbackName), mediaType: response.headers.get("content-type") || "application/octet-stream", base64: Buffer.from(bytes).toString("base64") };
  } catch (caught) {
    if (caught?.name === "AbortError") throw new Error(`ERP ${method} ${route} timed out.`);
    throw caught;
  } finally {
    clearTimeout(timer);
  }
}

function requiredObject(value, name) {
  object(value, name);
  return value;
}

function requiredIds(value, name) {
  ids(value, name);
  return value;
}

function token(value, name) {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]{1,120}$/.test(value)) throw new Error(`${name} is invalid.`);
  return value;
}

async function uploadSingleFile(route, inputPath, fields = {}) {
  if (typeof inputPath !== "string" || !path.isAbsolute(inputPath)) throw new Error("filePath must be absolute.");
  const sourcePath = path.resolve(inputPath);
  const stat = await fsp.stat(sourcePath).catch(() => null);
  if (!stat?.isFile()) throw new Error(`Upload file is unavailable: ${sourcePath}`);
  const form = new FormData();
  form.append("file", new Blob([await fsp.readFile(sourcePath)]), path.basename(sourcePath));
  for (const [key, value] of Object.entries(fields)) form.append(key, String(value));
  return requestErp({ method: "POST", route, formData: form });
}

async function manageDesignChange(args) {
  const operation = args?.operation;
  if (operation === "create") return requestErp({ method: "POST", route: "/designChange/apply", body: requiredObject(args?.payload, "payload") });
  if (operation === "update") return requestErp({ method: "PUT", route: "/designChange/apply", body: requiredObject(args?.payload, "payload") });
  if (operation === "delete") return requestErp({ method: "DELETE", route: `/designChange/apply/${requiredIds(args?.changeIds, "changeIds").join(",")}` });
  if (operation === "submit") { positiveInteger(args?.changeId, "changeId"); return requestErp({ method: "POST", route: `/designChange/apply/submit/${args.changeId}` }); }
  if (operation === "review") return requestErp({ method: "POST", route: "/designChange/apply/review", body: requiredObject(args?.payload, "payload") });
  if (operation === "confirm") return requestErp({ method: "POST", route: "/designChange/apply/confirm", body: requiredObject(args?.payload, "payload") });
  throw new Error("Unsupported design-change operation.");
}

async function manageDesignChangeItems(args) {
  const operation = args?.operation;
  if (operation === "list") { positiveInteger(args?.changeId, "changeId"); return requestErp({ method: "GET", route: `/designChange/apply/item/list/${args.changeId}` }); }
  if (operation === "create") return requestErp({ method: "POST", route: "/designChange/apply/item", body: requiredObject(args?.payload, "payload") });
  if (operation === "batch_create") return requestErp({ method: "POST", route: "/designChange/apply/item/batch", body: requiredObject(args?.payload, "payload") });
  if (operation === "update") return requestErp({ method: "PUT", route: "/designChange/apply/item", body: requiredObject(args?.payload, "payload") });
  if (operation === "delete") return requestErp({ method: "DELETE", route: `/designChange/apply/item/${requiredIds(args?.itemIds, "itemIds").join(",")}` });
  if (operation === "execute") return requestErp({ method: "POST", route: "/designChange/apply/item/execute", body: requiredObject(args?.payload, "payload") });
  throw new Error("Unsupported design-change item operation.");
}

async function manageDesignOrder(args) {
  positiveInteger(args?.requestId, "requestId");
  if (args?.operation === "delete") return requestErp({ method: "DELETE", route: `/design/order/${args.requestId}` });
  if (args?.operation === "resubmit") return requestErp({ method: "POST", route: `/design/order/${args.requestId}/resubmit` });
  if (args?.operation === "approve") {
    if (typeof args?.approvalVersion !== "string" || !args.approvalVersion.trim()) throw new Error("approvalVersion is required.");
    return requestErp({ method: "POST", route: `/design/order/${args.requestId}/approve`, body: { approvalVersion: args.approvalVersion } });
  }
  throw new Error("Unsupported design-order operation.");
}

async function manageDesignOrderDraftScrap(args) {
  positiveInteger(args?.draftId, "draftId"); positiveInteger(args?.seq, "seq");
  const route = `/design/order/draft/${args.draftId}/item/${args.seq}/scrap-`;
  if (args?.operation === "save") return requestErp({ method: "POST", route: route + "decision", body: requiredObject(args?.payload, "payload") });
  if (args?.operation === "release") return requestErp({ method: "POST", route: route + "release" });
  throw new Error("Unsupported draft idle-material operation.");
}

async function manageMoldRepair(args) {
  const operation = args?.operation;
  if (operation === "confirm_quantity") { positiveInteger(args?.exceptionId, "exceptionId"); return requestErp({ method: "POST", route: `/design/upload/mold-repair/${args.exceptionId}/quantity/confirm`, body: requiredObject(args?.payload, "payload") }); }
  if (operation === "submit_approval") { positiveInteger(args?.batchId, "batchId"); return requestErp({ method: "POST", route: `/design/upload/mold-repair/${args.batchId}/submit-approval`, body: requiredObject(args?.payload, "payload") }); }
  if (operation === "confirm_order_link") return requestErp({ method: "POST", route: "/design/upload/mold-repair/order-link/confirm", body: requiredObject(args?.payload, "payload") });
  if (operation === "respond") { const groupToken = token(args?.groupToken, "groupToken"); positiveInteger(args?.orderId, "orderId"); return requestErp({ method: "POST", route: `/design/upload/mold-repair/group/${groupToken}/entrust-order/${args.orderId}/response`, body: requiredObject(args?.payload, "payload") }); }
  if (operation === "submit_approval_batches") return requestErp({ method: "POST", route: "/design/upload/mold-repair/submit-approval-batches", body: requiredObject(args?.payload, "payload") });
  throw new Error("Unsupported mold-repair operation.");
}

async function manageErpBom(args) {
  if (args?.operation === "create") return requestErp({ method: "POST", route: "/system/bom", body: requiredObject(args?.payload, "payload") });
  if (args?.operation === "update") return requestErp({ method: "PUT", route: "/system/bom", body: requiredObject(args?.payload, "payload") });
  if (args?.operation === "delete") return requestErp({ method: "DELETE", route: `/system/bom/${requiredIds(args?.bomIds, "bomIds").join(",")}` });
  throw new Error("Unsupported ERP BOM operation.");
}

async function downloadErpDesignFile(args) {
  const artifact = args?.artifact;
  if (artifact === "drawing_preview" || artifact === "drawing_download") {
    positiveInteger(args?.drawingId, "drawingId");
    return requestErpFile({ method: "GET", route: `/design/drawing-version/${args.drawingId}/${artifact === "drawing_preview" ? "preview" : "download"}`, fallbackName: `drawing-${args.drawingId}.dxf` });
  }
  if (artifact === "standard_hardware_preview" || artifact === "standard_hardware_folder") {
    if (typeof args?.relativePath !== "string" || !args.relativePath.trim()) throw new Error("relativePath is required.");
    return requestErpFile({ method: "GET", route: artifact === "standard_hardware_preview" ? "/design/standard-hardware/preview" : "/design/standard-hardware/download-folder", searchParams: { relativePath: args.relativePath }, fallbackName: artifact === "standard_hardware_preview" ? "standard-hardware.dxf" : "standard-hardware.zip" });
  }
  if (artifact === "mold_repair_outsource_approval_drawing") {
    positiveInteger(args?.approvalOrderId, "approvalOrderId"); positiveInteger(args?.exceptionId, "exceptionId");
    return requestErpFile({ method: "GET", route: `/design/upload/mold-repair/approval-order/${args.approvalOrderId}/drawing/${args.exceptionId}/${token(args?.kind, "kind")}`, fallbackName: "mold-repair.dxf" });
  }
  if (artifact === "mold_repair_approval_drawing") { positiveInteger(args?.batchId, "batchId"); return requestErpFile({ method: "GET", route: `/design/upload/mold-repair/approval/${args.batchId}/drawing`, fallbackName: `mold-repair-approval-${args.batchId}.dxf` }); }
  if (artifact === "mold_repair_entrust_order_drawing") { positiveInteger(args?.orderId, "orderId"); positiveInteger(args?.exceptionId, "exceptionId"); return requestErpFile({ method: "GET", route: `/design/upload/mold-repair/entrust-order/${args.orderId}/drawing/${args.exceptionId}/${token(args?.kind, "kind")}`, fallbackName: "mold-repair.dxf" }); }
  if (artifact === "mold_repair_exception_drawing") { positiveInteger(args?.exceptionId, "exceptionId"); return requestErpFile({ method: "GET", route: `/design/upload/mold-repair/${args.exceptionId}/drawing/${token(args?.kind, "kind")}`, fallbackName: "mold-repair.dxf" }); }
  if (artifact === "mold_repair_group_drawing") return requestErpFile({ method: "GET", route: `/design/upload/mold-repair/group/${token(args?.groupToken, "groupToken")}/drawing/${token(args?.kind, "kind")}`, fallbackName: "mold-repair.dxf" });
  if (artifact === "mold_repair_authorized_package") return requestErpFile({ method: "GET", route: `/design/upload/mold-repair/group/${token(args?.groupToken, "groupToken")}/authorized-package`, searchParams: { routeType: token(args?.routeType, "routeType"), orderNo: args?.orderNo, partnerId: args?.partnerId }, fallbackName: "mold-repair-package.zip" });
  if (artifact === "bom_export") {
    positiveInteger(args?.moldId, "moldId");
    const form = new FormData(); form.append("moldId", String(args.moldId));
    return requestErpFile({ method: "POST", route: "/system/bom/exportByMold", formData: form, fallbackName: `bom-${args.moldId}.xlsx` });
  }
  throw new Error("Unsupported ERP design file artifact.");
}

async function callTool(name, args) {
  if (name === "rematch_new_mold_upload_drawings") {
    positiveInteger(args?.sessionId, "sessionId");
    return requestErp({ method: "POST", route: `/design/upload/session/${args.sessionId}/rematch-no-drawing` });
  }
  if (name === "update_design_order_item") {
    positiveInteger(args?.detailId, "detailId");
    if (typeof args?.modifyReason !== "string" || !args.modifyReason.trim()) throw new Error("modifyReason is required.");
    return requestErp({ method: "PUT", route: `/design/order/item/${args.detailId}`, body: {
      materialMark: args.materialMark ?? null, specification: args.specification ?? null, modifyReason: args.modifyReason
    }});
  }
  if (name === "save_design_order_scrap_decision") {
    positiveInteger(args?.detailId, "detailId");
    if (!["use", "partial", "skip"].includes(args?.decision) || typeof args?.detailVersion !== "string" || !args.detailVersion) {
      throw new Error("decision and detailVersion are required.");
    }
    return requestErp({ method: "POST", route: `/design/order/item/${args.detailId}/scrap-decision`, body: {
      decision: args.decision, scrapInventoryId: args.scrapInventoryId ?? null, usedQuantity: args.usedQuantity ?? null,
      matchNote: args.matchNote ?? null, detailVersion: args.detailVersion
    }});
  }
  if (name === "release_design_order_scrap_decision") {
    positiveInteger(args?.detailId, "detailId");
    if (typeof args?.detailVersion !== "string" || !args.detailVersion) throw new Error("detailVersion is required.");
    return requestErp({ method: "POST", route: `/design/order/item/${args.detailId}/scrap-release`, body: { detailVersion: args.detailVersion } });
  }
  if (name === "create_design_density") {
    object(args?.density, "density");
    return requestErp({ method: "POST", route: "/design/density", body: args.density });
  }
  if (name === "update_design_density") {
    positiveInteger(args?.densityId, "densityId"); object(args?.density, "density");
    return requestErp({ method: "PUT", route: `/design/density/${args.densityId}`, body: args.density });
  }
  if (name === "delete_design_density") {
    positiveInteger(args?.densityId, "densityId");
    return requestErp({ method: "DELETE", route: `/design/density/${args.densityId}` });
  }
  if (name === "create_design_group_rule") {
    object(args?.rule, "rule");
    return requestErp({ method: "POST", route: "/design/group-rule", body: args.rule });
  }
  if (name === "update_design_group_rule") {
    positiveInteger(args?.ruleId, "ruleId"); object(args?.rule, "rule");
    return requestErp({ method: "PUT", route: "/design/group-rule", body: { ...args.rule, id: args.ruleId } });
  }
  if (name === "toggle_design_group_rule") {
    positiveInteger(args?.ruleId, "ruleId");
    if (!["active", "inactive"].includes(args?.status)) throw new Error("status must be active or inactive.");
    return requestErp({ method: "PUT", route: `/design/group-rule/${args.ruleId}/toggle/${args.status}` });
  }
  if (name === "delete_design_group_rule") {
    ids(args?.ruleIds, "ruleIds");
    return requestErp({ method: "DELETE", route: `/design/group-rule/${args.ruleIds.join(",")}` });
  }
  if (name === "create_design_group_keyword") {
    object(args?.keyword, "keyword");
    return requestErp({ method: "POST", route: "/design/group-keyword", body: args.keyword });
  }
  if (name === "update_design_group_keyword") {
    positiveInteger(args?.keywordId, "keywordId"); object(args?.keyword, "keyword");
    return requestErp({ method: "PUT", route: "/design/group-keyword", body: { ...args.keyword, id: args.keywordId } });
  }
  if (name === "delete_design_group_keyword") {
    ids(args?.keywordIds, "keywordIds");
    return requestErp({ method: "DELETE", route: `/design/group-keyword/${args.keywordIds.join(",")}` });
  }
  if (name === "upload_standard_hardware_drawings") {
    if (!Array.isArray(args?.filePaths) || args.filePaths.length < 1 || typeof args?.folderName !== "string" || !args.folderName.trim()) {
      throw new Error("filePaths and folderName are required.");
    }
    const form = new FormData();
    for (const inputPath of args.filePaths) {
      if (typeof inputPath !== "string" || !path.isAbsolute(inputPath)) throw new Error("Each filePath must be absolute.");
      const sourcePath = path.resolve(inputPath);
      const stat = await fsp.stat(sourcePath).catch(() => null);
      if (!stat?.isFile()) throw new Error(`Standard-hardware drawing is unavailable: ${sourcePath}`);
      form.append("files", new Blob([await fsp.readFile(sourcePath)]), path.basename(sourcePath));
    }
    form.append("folderName", args.folderName.trim());
    return requestErp({ method: "POST", route: "/design/standard-hardware/upload", formData: form });
  }
  if (name === "rename_standard_hardware_drawing") {
    if (typeof args?.relativePath !== "string" || !args.relativePath.trim() || typeof args?.newFileName !== "string" || !args.newFileName.trim()) {
      throw new Error("relativePath and newFileName are required.");
    }
    return requestErp({ method: "PUT", route: "/design/standard-hardware/rename", body: {
      relativePath: args.relativePath, newFileName: args.newFileName,
    }});
  }
  if (name === "delete_standard_hardware_folder") {
    if (typeof args?.relativePath !== "string" || !args.relativePath.trim()) throw new Error("relativePath is required.");
    return requestErp({ method: "DELETE", route: "/design/standard-hardware", searchParams: { relativePath: args.relativePath } });
  }
  if (name === "manage_design_change") return manageDesignChange(args);
  if (name === "manage_design_change_items") return manageDesignChangeItems(args);
  if (name === "manage_design_order") return manageDesignOrder(args);
  if (name === "manage_design_order_draft_scrap") return manageDesignOrderDraftScrap(args);
  if (name === "submit_design_upload_change") return requestErp({ method: "POST", route: "/design/upload/change-submit", body: requiredObject(args?.payload, "payload") });
  if (name === "manage_mold_repair") return manageMoldRepair(args);
  if (name === "manage_erp_bom") return manageErpBom(args);
  if (name === "get_erp_bom_shortage") {
    positiveInteger(args?.moldId, "moldId");
    if (args?.partId !== undefined && args?.partId !== null) positiveInteger(args.partId, "partId");
    return requestErp({ method: "GET", route: `/system/bom/shortage/${args.moldId}`, searchParams: { partId: args.partId } });
  }
  if (name === "upload_mold_repair_drawing") return uploadSingleFile("/design/upload/mold-repair", args?.filePath, { skipVision: Boolean(args?.skipVision) });
  if (name === "import_erp_bom") {
    positiveInteger(args?.moldId, "moldId");
    return uploadSingleFile("/system/bom/importByMold", args?.filePath, { moldId: args.moldId });
  }
  if (name === "download_erp_design_file") return downloadErpDesignFile(args);
  throw new Error(`Unknown tool: ${name}`);
}

function send(value) {
  stdout.write(`${JSON.stringify(value)}\n`);
}

async function handle(message) {
  if (!message || message.jsonrpc !== "2.0") return;
  const { id, method, params } = message;
  try {
    if (method === "initialize") {
      send({ jsonrpc: "2.0", id, result: { protocolVersion: params?.protocolVersion || "2024-11-05", capabilities: { tools: {} }, serverInfo: { name: SERVER_NAME, version: SERVER_VERSION } } });
      return;
    }
    if (method === "notifications/initialized" || method === "initialized") return;
    if (method === "ping") {
      send({ jsonrpc: "2.0", id, result: {} });
      return;
    }
    if (method === "tools/list") {
      send({ jsonrpc: "2.0", id, result: { tools } });
      return;
    }
    if (method === "tools/call") {
      const value = await callTool(params?.name, params?.arguments || {});
      send({ jsonrpc: "2.0", id, result: { content: [{ type: "text", text: JSON.stringify(value) }], structuredContent: value } });
      return;
    }
    if (id !== undefined) send({ jsonrpc: "2.0", id, error: { code: -32601, message: `Method not found: ${method}` } });
  } catch (caught) {
    const messageText = caught instanceof Error ? caught.message : String(caught);
    if (id !== undefined) send({ jsonrpc: "2.0", id, result: { isError: true, content: [{ type: "text", text: messageText }] } });
    else log(messageText);
  }
}

let buffer = "";
stdin.setEncoding("utf8");
stdin.on("data", (chunk) => {
  buffer += chunk;
  let index;
  while ((index = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, index).trim();
    buffer = buffer.slice(index + 1);
    if (!line) continue;
    try {
      handle(JSON.parse(line)).catch((caught) => log(caught instanceof Error ? caught.message : String(caught)));
    } catch (caught) {
      log(caught instanceof Error ? caught.message : String(caught));
    }
  }
});
