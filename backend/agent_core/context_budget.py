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
    for key in ("model_context", "proposal", "tool_error", "authoritative_receipt"):
        if key in payload:
            compact[key] = deepcopy(payload[key])
    for key in ("status", "resolution", "warnings", "suggestions", "record_count", "limitations"):
        if key in payload:
            compact[key] = _compact_sample(payload[key])
    encoded = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    return encoded, len(encoded) < len(content or "")


def compact_messages_for_model(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
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
        if message.get("role") == "assistant" and isinstance(message.get("content"), str) and len(message["content"]) > 800:
            message["content"] = message["content"][:800] + "…"
            changed += 1
    after = estimate_json_tokens(compacted)
    if not changed or after >= before:
        return messages, None
    record = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "strategy": "tool-result-summary",
        "before_tokens": before,
        "after_tokens": after,
        "saved_tokens": max(0, before - after),
        "message_count": changed,
        "summary": f"已压缩 {changed} 条模型可见历史，完整业务证据未删除。",
    }
    return compacted, record
