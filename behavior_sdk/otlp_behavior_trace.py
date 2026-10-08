# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Behavior Trace v1 adapter for LakeRunner's uninterpreted ReadTrace cells.

The reserved cell contains exact OTLP attributes and native span events.  This
module alone interprets those facts as a Behavior SDK trace.  A rejected trace
is a coverage gap, never a negative behavioral finding.
"""
from __future__ import annotations

import json
import math
import re
import struct
from collections.abc import Mapping
from typing import Any


BEHAVIOR_COLUMN = "chq_behavior_otlp_v1"
_PREFIX = "cardinal.behavior."
_HEX_ID = re.compile(r"[0-9a-f]{16}")
_HEX_TRACE = re.compile(r"[0-9a-f]{32}")


class BehaviorTraceCoverageGap(ValueError):
    """The frozen program cannot make a confident finding from these cells."""

    verdict = "UNKNOWN"

    def __init__(self, reason: str, ref: str = "run"):
        super().__init__(reason)
        self.coverage_gaps = ({"ref": ref, "reason": reason},)


def _gap(reason: str, ref: str = "run") -> None:
    raise BehaviorTraceCoverageGap(reason, ref)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out = {}
    for key, value in pairs:
        if key in out:
            _gap(f"duplicate JSON key {key}")
        out[key] = value
    return out


def _cell(row: Mapping[str, Any], name: str, *, required: bool = True) -> bytes | None:
    matches = [cell for cell in row.get("columns", ()) if cell.get("name") == name]
    if len(matches) > 1:
        _gap(f"duplicate LakeRunner column {name}")
    if not matches:
        if required:
            _gap(f"missing LakeRunner column {name}")
        return None
    cell = matches[0]
    if cell.get("dtype") != 0:
        _gap(f"wrong LakeRunner dtype for {name}")
    value = cell.get("valueHex")
    if not isinstance(value, str):
        if required:
            _gap(f"null LakeRunner column {name}")
        return None
    try:
        return bytes.fromhex(value)
    except ValueError:
        _gap(f"invalid hex cell {name}")


def _text_cell(row: Mapping[str, Any], name: str, *, required: bool = True) -> str | None:
    raw = _cell(row, name, required=required)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        _gap(f"invalid UTF-8 cell {name}")


def _int_field(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _gap(f"invalid {label}")
    return value


def _typed(raw: Any) -> Any:
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
        _gap("malformed OTLP AnyValue")
    kind = raw["type"]
    if kind in ("missing", "null"):
        if "value" in raw:
            _gap("malformed null/missing OTLP AnyValue")
        return None
    if kind != "double" and "value" not in raw:
        _gap("OTLP AnyValue has no value")
    value = raw.get("value")
    if kind == "string" and isinstance(value, str):
        return value
    if kind == "bool" and type(value) is bool:
        return value
    if kind == "int" and type(value) is int and -(2**63) <= value < 2**63:
        return value
    if kind == "double":
        bits = raw.get("bits")
        if not isinstance(bits, str) or not re.fullmatch(r"[0-9a-f]{16}", bits):
            _gap("malformed double bits")
        number = struct.unpack(">d", bytes.fromhex(bits))[0]
        if not math.isfinite(number):
            _gap("non-finite double is not an SDK JSON value")
        return number
    if kind == "bytes" and isinstance(value, str):
        try:
            bytes.fromhex(value)
        except ValueError:
            _gap("malformed OTLP bytes")
        _gap("OTLP bytes have no lossless Behavior SDK JSON projection")
    if kind == "array" and isinstance(value, list):
        return [_typed(item) for item in value]
    if kind == "kvlist" and isinstance(value, list):
        return _attrs(value)
    _gap(f"malformed OTLP AnyValue of type {kind}")


def _attrs(entries: Any) -> dict[str, Any]:
    if not isinstance(entries, list):
        _gap("OTLP attributes are not a list")
    out: dict[str, Any] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("key"), str):
            _gap("malformed OTLP KeyValue")
        key = entry["key"]
        if key in out:
            _gap(f"duplicate exact OTLP key {key}")
        if ("value" not in entry or not isinstance(entry["value"], dict)
                or entry["value"].get("type") == "missing"):
            _gap(f"missing OTLP value for {key}")
        out[key] = _typed(entry["value"])
    return out


def _required(attrs: Mapping[str, Any], key: str, kind: type, *, nonempty: bool = False) -> Any:
    if key not in attrs or type(attrs[key]) is not kind:
        _gap(f"missing or mistyped {key}")
    value = attrs[key]
    if nonempty and not value:
        _gap(f"empty {key}")
    return value


def _occurrence(attrs: Mapping[str, Any], seen_ids: set[str], seen_seq: set[int]) -> tuple[str, int]:
    eid = _required(attrs, _PREFIX + "event.id", str, nonempty=True)
    seq = _required(attrs, _PREFIX + "event.seq", int)
    if seq < 0 or eid in seen_ids or seq in seen_seq:
        _gap("duplicate ID, duplicate sequence, or negative sequence", eid)
    seen_ids.add(eid)
    seen_seq.add(seq)
    return eid, seq


def _source_refs(attrs: Mapping[str, Any]) -> list[str]:
    value = attrs.get(_PREFIX + "source.refs", [])
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        _gap("invalid source refs")
    return value


def adapt_read_trace(result: Mapping[str, Any]) -> dict[str, Any]:
    """Validate Step 5 v1 and project one complete ITBench trace.

    ``result`` is the JSON ReadTraceResult, not a projected query row.  The
    frozen ITBench profile is only safe when no agent message falls between a
    call and its returned result; such overlap is explicitly rejected.
    """
    if not isinstance(result, Mapping):
        _gap("ReadTraceResult is not an object")
    otel_id = result.get("traceId")
    if not isinstance(otel_id, str) or not _HEX_TRACE.fullmatch(otel_id):
        _gap("invalid ReadTrace trace ID")
    rows = result.get("rows")
    if not isinstance(rows, list) or not rows:
        _gap("ReadTrace has no rows")
    if not isinstance(result.get("contributors"), list) or not result["contributors"]:
        _gap("ReadTrace has no contributors")
    if not isinstance(result.get("receipt"), dict):
        _gap("ReadTrace has no receipt")

    spans: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or _text_cell(row, "trace_id") != otel_id:
            _gap("ReadTrace row belongs to another trace")
        span_id = _text_cell(row, "id")
        if not isinstance(span_id, str) or not _HEX_ID.fullmatch(span_id) or span_id in spans:
            _gap("invalid or duplicate native span ID")
        parent_id = _text_cell(row, "parent_span_id", required=False)
        if parent_id and not _HEX_ID.fullmatch(parent_id):
            _gap("invalid native parent span ID")
        payload = _text_cell(row, BEHAVIOR_COLUMN, required=False)
        if payload is None:
            spans[span_id] = {"parent": parent_id, "attrs": {}, "events": []}
            continue
        try:
            envelope = json.loads(payload, object_pairs_hook=_unique_object)
        except (ValueError, TypeError) as exc:
            _gap(f"invalid retained behavior envelope: {exc}")
        if not isinstance(envelope, dict) or envelope.get("version") != 1:
            _gap("unsupported retained behavior envelope version")
        if _int_field(envelope.get("dropped_attributes_count", 0), "dropped attributes"):
            _gap("span attributes were dropped")
        if _int_field(envelope.get("dropped_events_count", 0), "dropped events"):
            _gap("span events were dropped")
        attrs = _attrs(envelope.get("span_attributes"))
        raw_events = envelope.get("events")
        if not isinstance(raw_events, list):
            _gap("retained span events are missing")
        events = []
        positions = set()
        for raw in raw_events:
            if not isinstance(raw, dict):
                _gap("malformed retained span event")
            pos = _int_field(raw.get("position"), "event position")
            if pos in positions:
                _gap("duplicate native event position")
            positions.add(pos)
            if _int_field(raw.get("dropped_attributes_count", 0), "event dropped attributes"):
                _gap("event attributes were dropped")
            nanos = _int_field(raw.get("time_unix_nano"), "native event timestamp")
            if not isinstance(raw.get("name"), str):
                _gap("missing native event name")
            events.append({"name": raw["name"], "time": nanos / 1e9,
                           "attrs": _attrs(raw.get("attributes"))})
        if positions != set(range(len(raw_events))):
            _gap("native event position gap")
        spans[span_id] = {"parent": parent_id, "attrs": attrs, "events": events}

    roots = [(sid, span) for sid, span in spans.items() if not span["parent"]]
    if len(roots) != 1:
        _gap("trace must have exactly one native root span")
    root_id, root = roots[0]
    ra = root["attrs"]
    if (_required(ra, _PREFIX + "contract.version", str) != "1"
            or _required(ra, "gen_ai.operation.name", str) != "invoke_agent"):
        _gap("root is not an invoke_agent v1 span")
    run_id = _required(ra, _PREFIX + "run.id", str, nonempty=True)
    if _required(ra, _PREFIX + "run.finalized", bool) is not True:
        _gap("run is not finalized")
    expected_count = _required(ra, _PREFIX + "run.event_count", int)
    if expected_count < 0:
        _gap("negative run event count")
    # This v1 projection has one explicit ITBench namespace: no implicit
    # service.name, conversation ID, or arbitrary agent-ID to name mapping.
    agent = _required(ra, "gen_ai.agent.name", str, nonempty=True)
    if agent != "itbench-sre":
        _gap("no declared ITBench agent identity mapping")
    for sid, span in spans.items():
        if sid != root_id and span["parent"] not in spans:
            _gap("orphan native span")

    events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_seq: set[int] = set()
    calls: dict[str, dict[str, Any]] = {}
    results: dict[str, tuple[dict[str, Any], str]] = {}
    for sid, span in spans.items():
        attrs = span["attrs"]
        operation = attrs.get("gen_ai.operation.name")
        if operation == "execute_tool":
            eid, seq = _occurrence(attrs, seen_ids, seen_seq)
            tool = _required(attrs, "gen_ai.tool.name", str, nonempty=True)
            call_id = _required(attrs, "gen_ai.tool.call.id", str, nonempty=True)
            args = _required(attrs, "gen_ai.tool.call.arguments", dict)
            if tool == "apply_patch" and ("input" not in args
                                          or not isinstance(args["input"], str)):
                _gap("ITBench apply_patch input argument is missing", eid)
            if tool == "shell" and not any(
                key in args and isinstance(args[key], (str, list))
                for key in ("command", "cmd", "input", "raw")
            ):
                _gap("ITBench shell command argument is missing", eid)
            if call_id in calls or sid == root_id:
                _gap("duplicate call ID or root is a tool", eid)
            call = {"id": eid, "parent_id": None, "kind": "tool", "name": tool,
                    "start": 0.0, "end": None, "seq": seq,
                    "attrs": {"call_id": call_id, "source_refs": _source_refs(attrs)},
                    "input": args, "output": None, "error": None}
            calls[call_id] = {"event": call, "span_id": sid}
            events.append(call)
        elif _PREFIX + "event.id" in attrs:
            _gap("behavioral span ID on a non-tool span")
        for native in span["events"]:
            ea = native["attrs"]
            event_type = ea.get(_PREFIX + "event.type")
            if event_type is None:
                if native["name"].startswith(_PREFIX) or any(k.startswith(_PREFIX) for k in ea):
                    _gap("behavioral native event lacks event.type")
                continue
            if event_type not in ("message", "tool_result", "outcome"):
                _gap("invalid behavior event type")
            eid, seq = _occurrence(ea, seen_ids, seen_seq)
            common = {"id": eid, "parent_id": None, "start": native["time"],
                      "end": native["time"], "seq": seq}
            if event_type == "message":
                name = _required(ea, _PREFIX + "event.name", str, nonempty=True)
                role = _required(ea, _PREFIX + "message.role", str, nonempty=True)
                body = _required(ea, _PREFIX + "message.text", str)
                source_role = {"assistant": "assistant", "agent_message": "assistant",
                               "reasoning": "assistant", "user": "user",
                               "user_message": "user"}.get(name)
                if source_role is None or role != source_role:
                    _gap("ITBench message name and role conflict", eid)
                events.append({**common, "kind": "message", "name": name,
                               "attrs": {"role": role, "source_refs": _source_refs(ea)},
                               "input": None, "output": body, "error": None})
            elif event_type == "tool_result":
                if native["name"] != _PREFIX + "tool.result" or sid == root_id:
                    _gap("tool result has wrong native owner or name", eid)
                call_id = _required(ea, "gen_ai.tool.call.id", str, nonempty=True)
                present = _required(ea, _PREFIX + "tool.output.present", bool)
                outcome = _required(ea, _PREFIX + "tool.outcome", str)
                if outcome not in ("success", "failure", "timeout", "unknown"):
                    _gap("invalid explicit tool outcome", eid)
                if present != (_PREFIX + "tool.output" in ea):
                    _gap("tool output presence marker conflicts with payload", eid)
                error = ea.get(_PREFIX + "tool.error.text")
                if error is not None and not isinstance(error, str):
                    _gap("invalid tool error text", eid)
                out = ea.get(_PREFIX + "tool.output")
                attrs_out: dict[str, Any] = {"call_id": call_id, "outcome": outcome,
                                             "output_present": present,
                                             "source_refs": _source_refs(ea)}
                for field in ("output_line", "exit_code"):
                    key = _PREFIX + "itbench." + field
                    if key in ea:
                        attrs_out[field] = _required(ea, key, int)
                result_event = {**common, "kind": "message", "name": "tool_result",
                                "attrs": attrs_out, "input": None, "output": out,
                                "error": error}
                if call_id in results:
                    _gap("duplicate tool result for one call", eid)
                results[call_id] = (result_event, sid)
                events.append(result_event)
            else:
                name = _required(ea, _PREFIX + "event.name", str, nonempty=True)
                output = ea.get(_PREFIX + "outcome.output")
                events.append({**common, "kind": "outcome", "name": name,
                               "attrs": {"source_refs": _source_refs(ea)},
                               "input": None, "output": output, "error": None})

    if len(seen_seq) != expected_count or seen_seq != set(range(expected_count)):
        _gap("run event count or contiguous sequence is incomplete")
    if set(results) != set(calls):
        _gap("closed ITBench run has a call without an observed result")
    for call_id, (result_event, owner) in results.items():
        call = calls.get(call_id)
        if call is None or call["span_id"] != owner:
            _gap("tool result call ID or owning span conflicts", result_event["id"])
        target = call["event"]
        standard_result = spans[owner]["attrs"].get("gen_ai.tool.call.result")
        explicit_outcome = result_event["attrs"]["outcome"]
        if ("gen_ai.tool.call.result" in spans[owner]["attrs"]
                and (explicit_outcome != "success" or standard_result != result_event["output"])):
            _gap("standard tool result conflicts with observed result event", result_event["id"])
        if result_event["seq"] <= target["seq"]:
            _gap("tool result precedes its call", result_event["id"])
        if any(target["seq"] < event["seq"] < result_event["seq"]
               and event["kind"] == "message" and event["name"] in ("assistant", "agent_message")
               for event in events):
            _gap("frozen ITBench profile cannot order a message during a pending tool call",
                 target["id"])
        result_event["parent_id"] = target["id"]
        target["output"] = result_event["output"]
        target["error"] = result_event["error"]
        target["attrs"].update({key: value for key, value in result_event["attrs"].items()
                                if key in ("output_line", "exit_code")})
        target["attrs"]["result_event_id"] = result_event["id"]
        target["attrs"]["explicit_outcome"] = result_event["attrs"]["outcome"]

    events.sort(key=lambda event: event["seq"])
    projected = {"trace_id": run_id, "agent": agent, "attrs": {}, "events": events}
    # The accepted diagnostic uses the frozen ITBench profile.  Its derived
    # outcome must agree with the producer's explicit result classification;
    # otherwise coalescing could manufacture a confident verdict.
    from .trace import TraceView

    calls_by_event = {call["event"]["id"]: call for call in calls.values()}
    for call in TraceView(projected, profile="itbench-sre/v1", finalized=True).calls():
        explicit = calls_by_event[call.ref]["event"]["attrs"]["explicit_outcome"]
        if call.outcome.value.lower() != explicit:
            _gap("explicit tool outcome conflicts with frozen ITBench evidence", call.ref)
    return projected
