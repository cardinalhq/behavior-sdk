# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Project the retained OpenInference occurrences needed for one agent behavior.

The input is LakeRunner's native ReadTraceResult. This bridge reads only an
assistant text block and submit_report tool spans; it does not infer messages
from prompts, tool arguments, or graph state snapshots.
"""
from __future__ import annotations

import json
import re
import struct
from collections.abc import Mapping
from typing import Any

from .otlp_behavior_trace import BehaviorTraceCoverageGap


_TRACE_ID = re.compile(r"[0-9a-f]{32}\Z")
_SPAN_ID = re.compile(r"[0-9a-f]{16}\Z")
_TEXT = "llm_output_messages_0_message_contents_0_message_content_text"
_ROLE = "llm_output_messages_0_message_role"


def _gap(reason: str) -> None:
    raise BehaviorTraceCoverageGap(reason)


def _cell(row: Mapping[str, Any], key: str, dtype: int = 0) -> str | int | None:
    matches = [item for item in row.get("columns", ()) if item.get("name") == key]
    if len(matches) > 1:
        _gap(f"duplicate retained column {key}")
    if not matches:
        return None
    item = matches[0]
    value = item.get("valueHex")
    if value is None:
        return None
    if item.get("dtype") != dtype or not isinstance(value, str):
        _gap(f"invalid retained column {key}")
    try:
        raw = bytes.fromhex(value)
        if dtype == 0:
            return raw.decode("utf-8")
        if len(raw) != 8:
            _gap(f"invalid retained integer {key}")
        return struct.unpack("<q", raw)[0]
    except (ValueError, UnicodeDecodeError):
        _gap(f"invalid retained bytes for {key}")


def _verify_assistant_output(row: Mapping[str, Any]) -> None:
    """Confirm that the projected text exhausts a completed Bedrock response.

    This investigator version uses non-streaming Bedrock Converse. Its LangChain
    parser maps every response text block into AIMessage.content, which the
    OpenInference LLM output serializes as output.value. An absent flattened
    text field is negative evidence only when that full content is retained.
    """
    if (_cell(row, "service_version") != "0.1.0"
            or _cell(row, "agent_instrumentation") != "openinference-instrumentation-langchain"
            or _cell(row, "status_code") != "STATUS_CODE_OK"
            or _cell(row, "output_mime_type") != "application/json"
            or _cell(row, _ROLE) != "assistant"):
        _gap("assistant output completeness contract is unavailable")
    raw = _cell(row, "output_value")
    if raw is None:
        _gap("serialized assistant output was not retained")
    try:
        output = json.loads(raw)
        groups = output["generations"]
        if len(groups) != 1 or len(groups[0]) != 1:
            raise ValueError("multiple or missing generations")
        generation = groups[0][0]
        message = generation["message"]
        kwargs = message["kwargs"]
        content = kwargs["content"]
        metadata = kwargs["response_metadata"]
        if (message["id"][-1] != "AIMessage" or kwargs["type"] != "ai"
                or metadata["model_provider"] != "bedrock_converse"
                or metadata["stopReason"] != _cell(row, "llm_stop_reason")
                or not isinstance(content, list) or not content
                or not all(isinstance(block, dict) and block.get("type") in
                           {"text", "reasoning_content", "tool_use"} for block in content)):
            raise ValueError("unsupported or incomplete assistant content")
        text_blocks = [block["text"] for block in content if block["type"] == "text"]
        if (not all(isinstance(text, str) for text in text_blocks)
                or len(text_blocks) > 1
                or generation["text"] != "".join(text_blocks)):
            raise ValueError("assistant text is not fully represented")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        _gap(f"assistant output completeness cannot be established: {exc}")
    retained_text = _cell(row, _TEXT)
    if retained_text != (text_blocks[0] if text_blocks else None):
        _gap("assistant text differs between serialized output and OpenInference field")


def adapt_openinference_read_trace(result: Mapping[str, Any]) -> dict[str, Any]:
    """Expose assistant text and submit_report as ordinary TraceView events."""
    trace_id = result.get("traceId") if isinstance(result, Mapping) else None
    if not isinstance(trace_id, str) or not _TRACE_ID.fullmatch(trace_id):
        _gap("invalid ReadTrace trace ID")
    rows = result.get("rows")
    if not isinstance(rows, list) or not rows or not result.get("contributors") or not result.get("receipt"):
        _gap("incomplete ReadTrace result")

    spans: dict[str, dict[str, Any]] = {}
    run_ids: set[str] = set()
    llm_rows: list[Mapping[str, Any]] = []
    occurrences: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, Mapping) or _cell(row, "trace_id") != trace_id:
            _gap("ReadTrace row belongs to another trace")
        sid = _cell(row, "id")
        parent = _cell(row, "parent_span_id")
        if (not isinstance(sid, str) or not _SPAN_ID.fullmatch(sid) or sid in spans
                or (parent is not None and (not isinstance(parent, str) or not _SPAN_ID.fullmatch(parent)))):
            _gap("invalid or duplicate span identity")
        service = _cell(row, "service_name")
        run_id = _cell(row, "agent_run_id")
        if service != "cardinal-investigator" or not isinstance(run_id, str) or not run_id:
            _gap("missing investigator identity")
        run_ids.add(run_id)
        start_ns = _cell(row, "chq_tsns", 1)
        end_ns = _cell(row, "end_tsns", 1)
        if (not isinstance(start_ns, int) or not isinstance(end_ns, int)
                or start_ns <= 0 or end_ns < start_ns):
            _gap("missing or invalid native span time")
        kind = _cell(row, "openinference_span_kind")
        spans[sid] = {"parent": parent, "kind": kind, "completed": _cell(row, "agent_completed")}
        if kind == "LLM":
            llm_rows.append(row)

        message = _cell(row, _TEXT)
        if message is not None:
            if kind != "LLM" or _cell(row, _ROLE) != "assistant" or not message:
                _gap("assistant text lacks an LLM role or content")
            occurrences.append((end_ns, {
                "id": f"{sid}:assistant:0", "parent_id": None,
                "kind": "message", "name": "assistant_message",
                "start": end_ns / 1e9, "end": end_ns / 1e9,
                "attrs": {"source_span_id": sid}, "input": None,
                "output": message, "error": None,
            }))
        if _cell(row, "tool_name") == "submit_report":
            if kind != "TOOL":
                _gap("submit_report is not an executed tool span")
            occurrences.append((start_ns, {
                "id": sid, "parent_id": None, "kind": "tool", "name": "submit_report",
                "start": start_ns / 1e9, "end": end_ns / 1e9,
                "attrs": {"source_span_id": sid}, "input": None,
                "output": None, "error": None,
            }))

    if len(run_ids) != 1:
        _gap("multiple agent runs share one trace ID")
    roots = [sid for sid, span in spans.items() if span["parent"] is None]
    if len(roots) != 1 or spans[roots[0]]["kind"] != "AGENT":
        _gap("missing unique agent root")
    if spans[roots[0]]["completed"] != "true":
        _gap("agent run is not confirmed complete")
    if any(span["parent"] and span["parent"] not in spans for span in spans.values()):
        _gap("missing parent span")
    root = next(row for row in rows if _cell(row, "id") == roots[0])
    if _cell(root, "agent_llm_calls", 1) != len(llm_rows):
        _gap("assistant output span count differs from completed run")
    for row in llm_rows:
        _verify_assistant_output(row)
    if not any(event["name"] == "submit_report" for _, event in occurrences):
        _gap("no retained submit_report invocation")
    occurrences.sort(key=lambda item: item[0])
    if len({time for time, _ in occurrences}) != len(occurrences):
        _gap("message and tool occurrence order is ambiguous")
    events = []
    for seq, (_, event) in enumerate(occurrences):
        events.append({**event, "seq": seq})
    return {"trace_id": trace_id, "agent": "cardinal-investigator",
            "attrs": {"agent_run_id": next(iter(run_ids))}, "events": events}
