"""Runtime model context accounting and model-facing compaction.

This module is part of the harness layer, not a business tool.  It estimates
the request context before model calls, records provider usage when available,
and compacts only the model-visible transcript.  Persisted tool receipts and
the user-visible run trace stay intact.
"""
from __future__ import annotations

import json
import math
import re
import time
from copy import deepcopy
from typing import Any


CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")
HISTORICAL_USER_PREFIX = "历史用户消息（仅用于连续对话和指代解析）：\n"
HISTORICAL_ASSISTANT_PREFIX = "历史助手答复（不是当前业务事实）：\n"
HISTORICAL_SUMMARY_PREFIX = "历史对话压缩摘要（完整记录仍保存在会话中，仅供连续对话和指代解析）：\n"
HISTORICAL_ATTACHMENT_MARKER = "\n该历史消息附件（元数据）："


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(CJK_RE.findall(text))
    non_cjk = max(0, len(text) - cjk)
    # CJK text is closer to one token per character; Latin/JSON averages nearer
    # four characters per token.  Keep a small floor so short protocol fragments
    # are still visible in the budget.
    return max(1, math.ceil(cjk * 1.1 + non_cjk / 4))


def estimate_json_tokens(value: Any) -> int:
    return estimate_text_tokens(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _metric_int(metrics: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = metrics.get(key)
        if isinstance(value, int):
            return value
    return 0


def _token_label(tokens: int) -> str:
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.1f}k"
    return str(tokens)


def _compact_sample(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "…"
    if isinstance(value, str):
        return value if len(value) <= 160 else value[:160] + "…"
    if isinstance(value, list):
        return [_compact_sample(item, depth + 1) for item in value[:3]]
    if isinstance(value, dict):
        items = list(value.items())
        if len(items) > 24:
            selected = items[:12] + items[-12:]
        else:
            selected = items
        result = {
            str(key): _compact_sample(item, depth + 1)
            for key, item in selected
        }
        if len(items) > len(selected):
            result["_omitted_key_count"] = len(items) - len(selected)
        return result
    return value


def usage_snapshot(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    context_window: int,
    max_output_tokens: int,
    model_metrics: dict[str, Any] | None = None,
    compactions: list[dict[str, Any]] | None = None,
    use_provider_input_tokens: bool = True,
) -> dict[str, Any]:
    metrics = model_metrics or {}
    message_tokens = estimate_json_tokens(messages)
    tool_schema_tokens = estimate_json_tokens(tools) if tools else 0
    estimated_input_tokens = message_tokens + tool_schema_tokens
    reported_input_tokens = _metric_int(metrics, "prompt_tokens", "prompt_eval_count")
    # Provider usage belongs to the request that just completed. Once another
    # message is appended or compaction rewrites the transcript, that input
    # count is stale and must not budget the next request.
    exact_input_tokens = reported_input_tokens if use_provider_input_tokens else 0
    output_tokens = _metric_int(metrics, "completion_tokens", "eval_count")
    total_tokens = _metric_int(metrics, "total_tokens")
    reasoning_tokens = _metric_int(metrics, "reasoning_tokens")
    used_tokens = exact_input_tokens or estimated_input_tokens
    safe_limit = max(1, context_window - max_output_tokens)
    remaining_tokens = max(0, context_window - used_tokens)
    percent = min(100, round(used_tokens / context_window * 100, 1)) if context_window else 0
    tool_message_tokens = sum(
        estimate_text_tokens(str(message.get("content") or ""))
        for message in messages
        if message.get("role") == "tool"
    )
    total_ms = metrics.get("total_ms") if isinstance(metrics.get("total_ms"), (int, float)) else 0
    generation_tokens_per_second = None
    if output_tokens and total_ms:
        generation_tokens_per_second = round(output_tokens / max(total_ms / 1000, 0.001), 1)
    return {
        "context_window": context_window,
        "max_output_tokens": max_output_tokens,
        "safe_limit": safe_limit,
        "used_tokens": used_tokens,
        "remaining_tokens": remaining_tokens,
        "used_percent": percent,
        "message_tokens_estimated": message_tokens,
        "tool_schema_tokens_estimated": tool_schema_tokens,
        "tool_message_tokens_estimated": tool_message_tokens,
        "input_tokens": reported_input_tokens,
        "input_tokens_estimated": estimated_input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens or (exact_input_tokens + output_tokens if exact_input_tokens or output_tokens else 0),
        "generation_tokens_per_second": generation_tokens_per_second,
        "tool_count": sum(1 for message in messages if message.get("role") == "tool"),
        "tool_schema_count": len(tools),
        "compaction_count": len(compactions or []),
        "latest_compaction": (compactions or [])[-1] if compactions else None,
        "token_source": "provider" if exact_input_tokens else "estimate",
        "remaining_label": _token_label(remaining_tokens),
        "window_label": _token_label(context_window),
        "used_label": _token_label(used_tokens),
    }


def _compact_payload(content: str) -> tuple[str, bool]:
    try:
        payload = json.loads(content or "{}")
    except (TypeError, ValueError):
        text = (content or "").strip()
        if len(text) <= 600:
            return content, False
        return text[:600] + "…", True
    # Compaction is deliberately idempotent. Re-compacting an already compact
    # tool row used to replace its remaining sample with ``null`` on the next
    # budget pass, leaving the model with an evidence id but no evidence.
    if all(key in payload for key in ("compact_summary", "data_shape", "sample")):
        return content, False
    evidence_id = payload.get("evidence_id")
    data = payload.get("data")
    if isinstance(data, list):
        data_shape = {"type": "list", "count": len(data)}
        sample = _compact_sample(data[:3])
    elif isinstance(data, dict):
        data_shape = {"type": "object", "keys": list(data.keys())[:20]}
        sample = _compact_sample(data)
    else:
        data_shape = {"type": type(data).__name__ if data is not None else "none"}
        sample = _compact_sample(data)
    compact = {
        "evidence_id": evidence_id,
        "compact_summary": payload.get("summary") or payload.get("message") or "工具结果已由运行时压缩，完整证据保留在本次任务记录中。",
        "data_shape": data_shape,
        "sample": sample,
    }
    # A tool result may carry two different kinds of information:
    #
    # * ``data`` is the audit/detail receipt, which can be reduced for the
    #   next provider request; and
    # * ``model_context``/``proposal`` are the tool's semantic contract with
    #   the model.  They are deliberately small projections containing the
    #   identifiers, dates, states, and approval facts needed to reason about
    #   this turn.
    #
    # Never run the generic depth/length sampler over those projections.  The
    # old behaviour converted nested business facts to ``…`` and then still
    # asked the model for a conclusion.  That is an architectural data-loss
    # bug: an evidence id without its semantic payload is not evidence.  The
    # complete receipt remains in the durable Step row; this exact projection
    # is the model-facing checkpoint-safe copy.
    semantic_keys = ("model_context", "proposal", "tool_error", "authoritative_receipt")
    # A raw business ``data`` payload without a semantic projection cannot be
    # safely reduced by a generic sampler.  Keeping only a shape/sample would
    # let the next model turn see an evidence id but not the facts behind it.
    # Domain tools must publish a model_context (or a proposal/error envelope)
    # before their result can cross a context checkpoint.  If they do not, the
    # caller's hard budget guard will fail closed instead of asking the model to
    # infer missing business values.
    if "data" in payload and not any(key in payload for key in semantic_keys):
        return content, False
    for key in semantic_keys:
        if key in payload:
            compact[key] = deepcopy(payload[key])
    for key in ("status", "resolution", "warnings", "suggestions", "record_count", "limitations"):
        if key in payload:
            compact[key] = _compact_sample(payload[key])
    encoded = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    return encoded, len(encoded) < len(content or "")


def _bounded_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit < 24:
        return text[:limit]
    head = max(1, limit * 2 // 3)
    tail = max(1, limit - head - 1)
    return text[:head] + "…" + text[-tail:]


def _attachment_manifest(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    selected = []
    for item in value:
        if not isinstance(item, dict):
            continue
        compact = {
            key: item[key]
            for key in ("id", "file_id", "filename", "name", "media_type", "content_type", "size")
            if item.get(key) is not None
        }
        if compact:
            selected.append(compact)
    return selected


def _historical_entries(message: dict[str, Any], text_limit: int) -> list[dict[str, Any]]:
    content = str(message.get("content") or "")
    if content.startswith(HISTORICAL_SUMMARY_PREFIX):
        try:
            payload = json.loads(content[len(HISTORICAL_SUMMARY_PREFIX):])
        except (TypeError, ValueError):
            return [{"role": "summary", "content": _bounded_text(content, text_limit)}]
        entries = payload.get("messages") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return [{"role": "summary", "content": _bounded_text(content, text_limit)}]
        normalized = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            entry = {"role": str(item.get("role") or "history")}
            text = _bounded_text(item.get("content"), text_limit)
            if text:
                entry["content"] = text
            attachments = _attachment_manifest(item.get("attachments"))
            if attachments:
                entry["attachments"] = attachments
            normalized.append(entry)
        return normalized
    if content.startswith(HISTORICAL_USER_PREFIX):
        body = content[len(HISTORICAL_USER_PREFIX):]
        raw_attachments = None
        if HISTORICAL_ATTACHMENT_MARKER in body:
            body, encoded = body.split(HISTORICAL_ATTACHMENT_MARKER, 1)
            try:
                raw_attachments = json.loads(encoded)
            except (TypeError, ValueError):
                raw_attachments = None
        entry: dict[str, Any] = {"role": "user"}
        text = _bounded_text(body, text_limit)
        if text:
            entry["content"] = text
        attachments = _attachment_manifest(raw_attachments)
        if attachments:
            entry["attachments"] = attachments
        return [entry]
    if content.startswith(HISTORICAL_ASSISTANT_PREFIX):
        body = content[len(HISTORICAL_ASSISTANT_PREFIX):]
        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            payload = {"summary": body}
        if isinstance(payload, dict):
            safe = {
                key: payload[key]
                for key in ("response_kind", "summary", "suggestions", "message", "error_code", "proposal_decision")
                if key in payload
            }
            safe = _compact_sample(safe)
            if isinstance(safe, dict):
                for key in ("summary", "message"):
                    if key in safe:
                        safe[key] = _bounded_text(safe[key], text_limit)
            body = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
        return [{"role": "assistant", "content": _bounded_text(body, max(text_limit, 40))}]
    return []


def _is_historical_message(message: dict[str, Any]) -> bool:
    content = str(message.get("content") or "")
    return content.startswith((HISTORICAL_USER_PREFIX, HISTORICAL_ASSISTANT_PREFIX, HISTORICAL_SUMMARY_PREFIX))


def _collapse_historical_messages(
    messages: list[dict[str, Any]], *, keep_recent: int, text_limit: int,
) -> list[dict[str, Any]]:
    positions = [index for index, message in enumerate(messages) if _is_historical_message(message)]
    collapse = positions[:-keep_recent] if keep_recent else positions
    if not collapse:
        return messages
    entries: list[dict[str, Any]] = []
    for position in collapse:
        entries.extend(_historical_entries(messages[position], text_limit))
    if not entries:
        return messages
    summary = {
        "role": "user",
        "content": HISTORICAL_SUMMARY_PREFIX + json.dumps(
            {"messages": entries}, ensure_ascii=False, separators=(",", ":")
        ),
    }
    first = collapse[0]
    removed = set(collapse)
    result = []
    for index, message in enumerate(messages):
        if index == first:
            result.append(summary)
        if index not in removed:
            result.append(message)
    return result


def compact_messages_for_model(
    messages: list[dict[str, Any]], *, required_savings: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    compacted = deepcopy(messages)
    changed = 0
    before = estimate_json_tokens(messages)
    for message in compacted:
        if message.get("role") != "tool":
            continue
        compact_content, did_change = _compact_payload(str(message.get("content") or ""))
        if did_change:
            message["content"] = compact_content
            changed += 1
    # If tool results were already compact, trim older assistant prose.  Keep tool
    # calls themselves so provider message ordering remains valid.
    for message in compacted[:-2]:
        if (message.get("role") == "assistant"
                and not _is_historical_message(message)
                and isinstance(message.get("content"), str)
                and len(message["content"]) > 800):
            message["content"] = message["content"][:800] + "…"
            changed += 1
    target = max(0, before - max(0, required_savings))
    history_compacted = False
    # Full conversation data remains durable in Run records.  Only when the
    # provider window is tight do we replace older model-visible turns with a
    # deterministic summary.  Attachment ids and filenames are retained so a
    # later request can still resolve and securely rebind the original file.
    if required_savings and estimate_json_tokens(compacted) > target:
        best = compacted
        best_tokens = estimate_json_tokens(best)
        for keep_recent, text_limit in ((12, 320), (8, 240), (4, 160), (0, 120), (0, 40), (0, 0)):
            candidate = _collapse_historical_messages(
                compacted, keep_recent=keep_recent, text_limit=text_limit,
            )
            candidate_tokens = estimate_json_tokens(candidate)
            if candidate_tokens < best_tokens:
                best, best_tokens = candidate, candidate_tokens
            if candidate_tokens <= target:
                break
        if best_tokens < estimate_json_tokens(compacted):
            changed += sum(1 for message in compacted if _is_historical_message(message))
            compacted = best
            history_compacted = True
    after = estimate_json_tokens(compacted)
    if not changed or after >= before:
        return messages, None
    record = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "strategy": "tool-and-conversation-summary" if history_compacted else "tool-result-summary",
        "before_tokens": before,
        "after_tokens": after,
        "saved_tokens": max(0, before - after),
        "message_count": changed,
        "summary": f"已压缩 {changed} 条模型可见历史，完整业务证据未删除。",
    }
    return compacted, record
