# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Narrow Megan Anthropic/GA ReadTrace contract; no private reasoning projection."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .openinference_read_trace import _cell
from .otlp_behavior_trace import BehaviorTraceCoverageGap
from .profiles import Profile
from .trace import Call, CoverageGap, Outcome, ResultState, ResultView


TOOLS = {"ga_account_summaries", "ga_run_report"}


def _gap(reason):
    raise BehaviorTraceCoverageGap(reason)


def _retained(value):
    if value is None or (isinstance(value, str) and "__REDACTED__" in value):
        _gap("required Megan conversational/tool content is absent or redacted")
    if isinstance(value, dict):
        for child in value.values():
            _retained(child) if child is not None else None
    elif isinstance(value, list):
        for child in value:
            _retained(child)
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _gap("duplicate key in retained Megan JSON")
        result[key] = value
    return result


def _json(raw):
    _retained(raw)
    try:
        return json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, TypeError):
        _gap("required Megan JSON content is invalid")


def _tool_result(raw):
    _retained(raw)
    if not isinstance(raw, str) or not raw:
        _gap("unsupported Megan tool result representation")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except json.JSONDecodeError:
        return raw
    return _retained(value)


def _public(content):
    """Validate public blocks and remove thinking/signature fields completely."""
    if isinstance(content, str):
        return [{"type": "text", "text": _retained(content)}]
    if not isinstance(content, list):
        _gap("Megan message content is not retained")
    blocks = []
    for block in content:
        if not isinstance(block, dict):
            _gap("invalid Megan message block")
        kind = block.get("type")
        if kind in {"thinking", "redacted_thinking"}:
            continue
        if kind == "text" and isinstance(block.get("text"), str):
            blocks.append({"type": kind, "text": _retained(block["text"])})
        elif kind == "tool_use":
            if (not isinstance(block.get("id"), str) or not block["id"]
                    or block.get("name") not in TOOLS or not isinstance(block.get("input"), dict)):
                _gap("unsupported or incomplete Megan tool call")
            blocks.append({k: _retained(block[k]) for k in ("type", "id", "name", "input")})
        elif kind == "tool_result":
            if not isinstance(block.get("tool_use_id"), str) or not block["tool_use_id"]:
                _gap("Megan tool history has no call identity")
            blocks.append({"type": kind, "tool_use_id": block["tool_use_id"],
                           "content": _tool_result(block.get("content"))})
        else:
            _gap("unsupported or incomplete public Megan content")
    return blocks


def _history(messages):
    if not isinstance(messages, list) or not messages:
        _gap("Megan input history is missing")
    history = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            _gap("unsupported Megan input history role")
        blocks = _public(message.get("content"))
        if any(b["type"] == "tool_result" for b in blocks) and message["role"] != "user":
            _gap("tool result has the wrong history role")
        if any(b["type"] == "tool_use" for b in blocks) and message["role"] != "assistant":
            _gap("tool call has the wrong history role")
        history.append({"role": message["role"], "content": blocks})
    return history


def adapt_megan_read_trace(result: Mapping[str, Any]) -> dict[str, Any]:
    """Read typed native rows, proving completion and public tool/history coverage.

    Structural or retention gaps raise BehaviorTraceCoverageGap for worker UNKNOWN.
    The narrow contract is one completed Megan conversation with known GA tools.
    """
    tid = result.get("traceId")
    rows = result.get("rows")
    if (not isinstance(tid, str) or not re.fullmatch("[0-9a-f]{32}", tid)
            or not isinstance(rows, list) or not rows or not result.get("contributors")
            or not result.get("receipt")):
        _gap("incomplete Megan ReadTrace result")
    spans = {}
    for row in rows:
        if not isinstance(row, Mapping) or _cell(row, "trace_id") != tid or _cell(row, "service_name") != "megan":
            _gap("ReadTrace row belongs to another trace or service")
        sid, parent = _cell(row, "id"), _cell(row, "parent_span_id")
        if (not isinstance(sid, str) or not re.fullmatch("[0-9a-f]{16}", sid) or sid in spans
                or parent is not None and (not isinstance(parent, str) or not re.fullmatch("[0-9a-f]{16}", parent))):
            _gap("invalid or duplicate Megan span identity")
        start, end = _cell(row, "chq_tsns", 1), _cell(row, "end_tsns", 1)
        if not isinstance(start, int) or not isinstance(end, int) or start <= 0 or end < start:
            _gap("missing or invalid native Megan span time")
        kind = _cell(row, "openinference_span_kind")
        if kind not in {"CHAIN", "AGENT", "LLM", "TOOL"}:
            _gap("unsupported Megan span kind")
        spans[sid] = dict(row=row, parent=parent, start=start, end=end, kind=kind)
    roots = [sid for sid, s in spans.items() if s["parent"] is None]
    agents = [sid for sid, s in spans.items() if s["kind"] == "AGENT"]
    if len(roots) != 1 or len(agents) != 1:
        _gap("Megan requires a unique rooted conversation and agent")
    for sid in spans:
        seen, current = set(), sid
        while current is not None:
            if current not in spans or current in seen:
                _gap("Megan parent tree is missing or cyclic")
            seen.add(current)
            current = spans[current]["parent"]
        if spans[sid]["kind"] in {"LLM", "TOOL"} and agents[0] not in seen:
            _gap("Megan occurrence is outside the conversation agent")
    ordered = sorted(spans.items(), key=lambda pair: pair[1]["start"])
    if len({s["start"] for s in spans.values()}) != len(spans):
        _gap("Megan occurrence order is ambiguous")
    events, expected_history, pending, executed, call_ids = [], None, {}, [], set()
    terminal = False
    previous_end = 0
    for seq, (sid, span) in enumerate(ordered):
        row, kind = span["row"], span["kind"]
        name = _cell(row, "name") or ""
        attrs = {"source_kind": kind, "source_span_id": sid}
        inp, out = None, None
        if kind in {"LLM", "TOOL"}:
            if terminal or span["start"] < previous_end:
                _gap("Megan conversational occurrence order is incomplete or ambiguous")
            previous_end = span["end"]
        if kind == "LLM":
            if _cell(row, "status_code") != "STATUS_CODE_OK":
                _gap("Megan model response is not complete")
            request, response = _json(_cell(row, "input_value")), _json(_cell(row, "output_value"))
            if not isinstance(request, dict) or not isinstance(response, dict):
                _gap("Megan model request/response is not retained")
            history = _history(request.get("messages"))
            if expected_history is None:
                if (len(history) != 1 or history[0]["role"] != "user"
                        or not history[0]["content"]
                        or any(b["type"] != "text" or not b["text"] for b in history[0]["content"])):
                    _gap("Megan conversation starts with unaccounted history")
            else:
                if pending:
                    _gap("assistant tool call lacks a retained result span")
                required = expected_history + [{"role": "user", "content": executed}]
                if history != required:
                    _gap("Megan input history differs from retained outputs/tool results")
            stop = response.get("stop_reason")
            if (response.get("role") != "assistant" or stop not in {"tool_use", "end_turn"}
                    or stop != _cell(row, "llm_finish_reason")):
                _gap("Megan response finality is unavailable or contradictory")
            blocks = _public(response.get("content"))
            if any(b["type"] == "tool_result" for b in blocks):
                _gap("assistant response contains a tool result")
            calls = [b for b in blocks if b["type"] == "tool_use"]
            if (stop == "tool_use") != bool(calls):
                _gap("Megan response finality contradicts tool calls")
            for b in calls:
                if b["id"] in call_ids:
                    _gap("duplicate Megan tool call identity")
                call_ids.add(b["id"])
                pending[b["id"]] = (b, sid)
            expected_history, executed = history + [{"role": "assistant", "content": blocks}], []
            terminal = stop == "end_turn"
            kind, name = "message", "assistant_final" if terminal else "assistant_step"
            attrs["finish_reason"] = stop
            inp = "\n".join(b["text"] for m in history if m["role"] == "user" for b in m["content"] if b["type"] == "text")
            out = "\n".join(b["text"] for b in blocks if b["type"] == "text")
            if terminal and not out:
                _gap("Megan final public answer is not retained")
        elif kind == "TOOL":
            name = _cell(row, "tool_name")
            if name not in TOOLS or _cell(row, "name") != "tool " + name:
                _gap("unknown or contradictory Megan tool identity")
            inp, out = _json(_cell(row, "input_value")), _tool_result(_cell(row, "output_value"))
            if not isinstance(inp, dict):
                _gap("Megan tool arguments are not retained")
            matches = [(cid, b, origin) for cid, (b, origin) in pending.items() if b["name"] == name and b["input"] == inp]
            if len(matches) != 1:
                _gap("cannot uniquely reconcile Megan tool span with assistant call")
            cid, _, origin = matches[0]
            del pending[cid]
            attrs.update(call_id=cid, origin_refs=[origin])
            executed.append({"type": "tool_result", "tool_use_id": cid, "content": out})
            kind = "tool"
        else:
            kind = kind.lower()
        events.append(dict(id=sid, parent_id=span["parent"], kind=kind, name=name,
                           start=span["start"] / 1e9, end=span["end"] / 1e9, seq=seq,
                           attrs=attrs, input=inp, output=out, error=None))
    if not terminal or pending or executed:
        _gap("Megan run lacks a completed final end_turn response")
    return {"trace_id": tid, "agent": "megan", "attrs": {"coverage_gaps": [],
            "scope": "Completed model response; not proof of Slack delivery."}, "events": events}


class MeganProfile(Profile):
    """Only explicit GA errors fail; only recognized GA result envelopes succeed."""
    def __init__(self):
        super().__init__("megan-ga-native", "v1", hashlib.sha256(Path(__file__).read_bytes()).hexdigest())

    def calls(self, run):
        for event in run.events:
            if event.kind != "tool":
                continue
            out = event.output
            outcome = Outcome.UNKNOWN
            if event.name in TOOLS and isinstance(out, str) and out.startswith("GA error: "):
                outcome = Outcome.FAILURE
            elif isinstance(out, dict) and "error" not in out and (
                event.name == "ga_account_summaries" and isinstance(out.get("accountSummaries"), list)
                or event.name == "ga_run_report" and out.get("kind") == "analyticsData#runReport"
                and isinstance(out.get("rows"), list) and isinstance(out.get("metricHeaders"), list)
            ):
                outcome = Outcome.SUCCESS
            present = out is not None and out != "__REDACTED__"
            yield Call(event.id, event.attrs.get("call_id", event.id), event.name,
                       event.input if isinstance(event.input, dict) else {},
                       ResultView(ResultState.PRESENT if present else ResultState.MISSING,
                                  (event.id,) if present else (), out), outcome, (outcome.value,),
                       (event.id,), tuple(event.attrs.get("origin_refs", ())), event.seq)

    def coverage_gaps(self, run):
        for gap in run.trace.attrs.get("coverage_gaps", []):
            yield CoverageGap(**gap)
        for event in run.events:
            if event.kind in {"message", "tool"} and (event.output is None or event.output == "__REDACTED__"):
                yield CoverageGap(event.id, "Required public content is absent or redacted.")
        if not run.finalized:
            yield CoverageGap("run", "Megan conversation is not finalized.")
