# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Versioned telemetry profiles for call, result, and outcome interpretation."""
from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .trace import Call, CoverageGap, Outcome, ResultState, ResultView, TraceView


_PROFILE_SOURCE = Path(__file__).read_bytes()
_TRACE_SOURCE = Path(__file__).with_name("trace.py").read_bytes()


def _digest(spec: dict[str, Any]) -> str:
    # Pin executable decoder semantics, not only a human-maintained version tag.
    # Hashing both modules is intentionally conservative: any adapter edit
    # requires a new diagnostic version and fresh acceptance.
    payload = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload + b"\0" + _PROFILE_SOURCE + b"\0" + _TRACE_SOURCE).hexdigest()


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _refs(*groups: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(ref for group in groups for ref in group))


@dataclass(frozen=True)
class Profile:
    id: str
    version: str
    digest: str

    @property
    def name(self) -> str:
        return self.id

    @property
    def reference(self) -> str:
        return f"{self.id}/{self.version}"

    @classmethod
    def load(cls, name: str) -> "Profile":
        factories = {
            "appworld/v1": AppWorldProfile,
            "itbench-sre/v1": ITBenchProfile,
        }
        try:
            return factories[name]()
        except KeyError as exc:
            raise ValueError(f"unknown trace profile {name!r}") from exc

    def calls(self, run: TraceView) -> Iterable[Call]:
        raise NotImplementedError

    def coverage_gaps(self, run: TraceView) -> Iterable[CoverageGap]:
        return ()


def _api_calls(source: str) -> list[tuple[str, dict[str, Any]]]:
    """Find syntactic AppWorld calls in executable Python, without evaluating it."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found = []
    # A call in a branch, function body, or loop is only *possible* from the
    # static source.  Do not promote it to an executed operation without a
    # more specific execution record.  The known AppWorld cells are straight
    # line top-level statements.
    direct = (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)
    for statement in tree.body:
        if not isinstance(statement, direct):
            continue
        for node in ast.walk(statement):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            app_node = node.func.value
            if not isinstance(app_node, ast.Attribute) or not isinstance(app_node.value, ast.Name):
                continue
            if app_node.value.id != "apis":
                continue
            args: dict[str, Any] = {}
            for i, arg in enumerate(node.args):
                args[f"arg{i}"] = _arg_value(arg, source)
            for keyword in node.keywords:
                args[keyword.arg or "**"] = _arg_value(keyword.value, source)
            found.append((node.lineno, node.col_offset, f"{app_node.attr}.{node.func.attr}", args))
    found.sort(key=lambda item: (item[0], item[1]))
    return [(name, args) for _, _, name, args in found]


def _arg_value(node: ast.AST, source: str) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return {"expression": ast.get_source_segment(source, node) or "<unresolved>"}


class AppWorldProfile(Profile):
    def __init__(self):
        spec = {
            "id": "appworld", "version": "v1", "calls": "apis.app.method in executed Python cell",
            "execution": "tool result or llm cell with direct reply child",
            "result": "same tool event or direct reply child; multiple replies ambiguous",
            "outcome": "error/traceback failure; successful execution/reply after call success; otherwise unknown",
        }
        super().__init__("appworld", "v1", _digest(spec))

    def calls(self, run: TraceView) -> Iterable[Call]:
        children: dict[str, list[Any]] = {}
        for event in run.events:
            if event.parent_id:
                children.setdefault(event.parent_id, []).append(event)
        for event in run.events:
            if event.kind == "tool" and event.name == "python":
                source = _text(event.input)
                result_events = [event] if event.output is not None or event.error is not None else []
            elif event.kind == "llm":
                direct = children.get(event.id, [])
                # A tool child is the executed cell and carries its own result.
                if any(child.kind == "tool" for child in direct):
                    continue
                result_events = [child for child in direct if child.kind == "message" and child.name == "reply"]
                if not result_events:
                    continue  # no evidence that this proposed code was executed
                source = _text(event.output)
            else:
                continue
            operations = _api_calls(source)
            if not operations:
                continue
            for ordinal, (name, arguments) in enumerate(operations, 1):
                # A cell-wide reply/output normally belongs to the terminal
                # API call. Earlier operations need their own result signal.
                result = (_result(result_events) if ordinal == len(operations)
                          else ResultView(ResultState.MISSING))
                outcome, signals, contradictory = self._outcome(
                    event, result_events, name, ordinal, len(operations), run.finalized
                )
                origin_refs = (event.id,)
                source_refs = _refs(origin_refs, result.refs)
                yield Call(event.id, f"{event.id}:{ordinal}", name, arguments, result,
                           outcome, signals, source_refs, origin_refs, event.seq, contradictory)

    def coverage_gaps(self, run: TraceView) -> Iterable[CoverageGap]:
        children: dict[str, list[Any]] = {}
        for event in run.events:
            if event.parent_id:
                children.setdefault(event.parent_id, []).append(event)
        for event in run.events:
            if event.kind == "tool" and event.name == "python":
                source = _text(event.input)
            elif event.kind == "llm":
                direct = children.get(event.id, [])
                if any(child.kind == "tool" for child in direct):
                    continue
                if not any(child.kind == "message" and child.name == "reply" for child in direct):
                    continue
                source = _text(event.output)
            else:
                continue
            syntactic_calls = len(re.findall(r"apis\.[A-Za-z_]\w*\.[A-Za-z_]\w*\s*\(", source))
            if syntactic_calls > len(_api_calls(source)):
                yield CoverageGap(event.id, "possible API calls in unsupported code shape")

    @staticmethod
    def _outcome(event: Any, result_events: list[Any], name: str, ordinal: int, n_operations: int,
                 finalized: bool) -> tuple[Outcome, tuple[str, ...], bool]:
        if len(result_events) > 1:
            return Outcome.UNKNOWN, ("multiple_reply_children",), False
        if not result_events:
            return (Outcome.UNKNOWN if finalized else Outcome.PENDING,
                    ("result_absent",), False)
        result = result_events[0]
        text = _text(result.output)
        failure = bool(result.error) or text.lstrip().startswith("Execution failed. Traceback:")
        success = text.lstrip().startswith("Code executed successfully.")
        signals = []
        if result.error:
            signals.append("error_field")
        if text.lstrip().startswith("Execution failed. Traceback:"):
            signals.append("python_execution_failed")
        if success:
            signals.append("python_execution_succeeded")
        if result is not event and result.kind == "message" and result.name == "reply":
            # AppWorld's direct reply is emitted after the Python code cell runs.
            # It is a result, not a new call.  A reply after several API calls
            # is evidence for the terminal call only; earlier calls lack their
            # own result and remain unknown rather than inheriting success.
            if ordinal == n_operations:
                success = True
                signals.append("direct_reply_after_execution")
            else:
                signals.append("reply_does_not_identify_earlier_call")
        if failure and success:
            return Outcome.UNKNOWN, tuple(signals + ["contradictory_status"]), True
        if failure:
            # A failed code block may contain more than one operation.  The
            # traceback only identifies the operation that actually failed.
            if n_operations > 1 and name not in _api_names(text + "\n" + _text(result.error)):
                return Outcome.UNKNOWN, tuple(signals + ["failure_not_attributed_to_call"]), False
            return Outcome.FAILURE, tuple(signals), False
        if success:
            if ordinal < n_operations:
                return Outcome.UNKNOWN, tuple(signals + ["cell_result_not_attributed_to_call"]), False
            return Outcome.SUCCESS, tuple(signals), False
        if "reply_does_not_identify_earlier_call" in signals:
            return Outcome.UNKNOWN, tuple(signals), False
        return Outcome.UNKNOWN, ("result_without_status",), False


def _api_names(source: str) -> set[str]:
    return set(f"{app}.{method}" for app, method in re.findall(
        r"apis\.([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*\(", source))


def _result(events: list[Any]) -> ResultView:
    if not events:
        return ResultView(ResultState.MISSING)
    if len(events) > 1:
        return ResultView(ResultState.AMBIGUOUS, tuple(event.id for event in events))
    event = events[0]
    return ResultView(ResultState.PRESENT, (event.id,), event.output)


_MCP_ERROR_PREFIXES = ("Error:", "Input validation error", "No metric files")
_HARNESS_ERROR_PREFIXES = (
    "failed to parse function arguments", "execution error", "apply_patch verification failed",
    "patch rejected", "unsupported call", "resources/list failed", "command timed out",
)


class ITBenchProfile(Profile):
    def __init__(self):
        spec = {
            "id": "itbench-sre", "version": "v1", "calls": "kind=tool",
            "result": "same-event output with output_line return marker",
            "mcp": "mcp__ or list_mcp_resources JSON text envelope, explicit error prefixes",
            "outcome": "conflicts unknown; timeout; failure field, exit, MCP, harness; explicit success only",
        }
        super().__init__("itbench-sre", "v1", _digest(spec))

    def calls(self, run: TraceView) -> Iterable[Call]:
        for event in run.events:
            if event.kind != "tool":
                continue
            attrs = event.attrs or {}
            is_mcp = event.name.startswith("mcp__") or event.name == "list_mcp_resources"
            arrived = attrs.get("output_line") is not None or event.output is not None or event.error is not None
            result = _result([event]) if arrived else _result([])
            outcome, signals, contradictory = self._outcome(event, is_mcp, arrived, run.finalized)
            arguments = event.input if isinstance(event.input, dict) else {"raw": event.input}
            yield Call(event.id, event.id, event.name, arguments, result, outcome, signals,
                       (event.id,), (event.id,), event.seq, contradictory)

    @staticmethod
    def _outcome(event: Any, is_mcp: bool, arrived: bool, finalized: bool
                 ) -> tuple[Outcome, tuple[str, ...], bool]:
        attrs = event.attrs or {}
        ec = attrs.get("exit_code")
        failure = []
        success = []
        timeout = False
        if event.error not in (None, ""):
            failure.append("error_field")
        if isinstance(ec, int) and not isinstance(ec, bool):
            if ec == 0:
                success.append("exit_code=0")
            else:
                failure.append(f"exit_code={ec}")
                timeout = ec == 124
        if is_mcp:
            status, detail = _mcp_status(event.output)
            if status == "failure":
                failure.append(detail)
            elif status == "success":
                success.append(detail)
            elif status == "timeout":
                failure.append(detail)
                timeout = True
            elif status == "conflict":
                return Outcome.UNKNOWN, (detail, "contradictory_status"), True
        elif isinstance(event.output, str):
            stripped = event.output.lstrip().lower()
            if stripped.startswith("command timed out") or stripped.startswith("timed out"):
                failure.append("harness_timeout")
                timeout = True
            elif stripped.startswith(tuple(prefix.lower() for prefix in _HARNESS_ERROR_PREFIXES)):
                failure.append("harness_error")
        if failure and success:
            return Outcome.UNKNOWN, tuple(failure + success + ["contradictory_status"]), True
        if timeout:
            return Outcome.TIMEOUT, tuple(failure), False
        if failure:
            return Outcome.FAILURE, tuple(failure), False
        if success:
            return Outcome.SUCCESS, tuple(success), False
        if not arrived:
            return (Outcome.UNKNOWN if finalized else Outcome.PENDING,
                    ("result_absent",), False)
        return Outcome.UNKNOWN, ("result_without_status",), False


def _mcp_status(value: Any) -> tuple[str, str]:
    """Decode only the ITBench MCP envelope, never arbitrary tool output text."""
    if not isinstance(value, str):
        return "unknown", "mcp_unrecognized_output"
    try:
        decoded = json.loads(value)
    except (ValueError, TypeError):
        if value.startswith('err: "tool call error:') and "timed out" in value:
            return "timeout", "mcp_transport_timeout"
        return "unknown", "mcp_unrecognized_output"
    if isinstance(decoded, dict):
        if decoded.get("isError") is True or decoded.get("is_error") is True:
            return "failure", "mcp_structured_error"
        structured_success = decoded.get("isError") is False or decoded.get("is_error") is False
        blocks = decoded.get("content")
    else:
        structured_success = False
        blocks = decoded
    if not isinstance(blocks, list):
        return ("success", "mcp_structured_success") if structured_success else ("unknown", "mcp_unrecognized_output")
    if not blocks:
        return "success", "mcp_empty_result"
    texts = [block.get("text") for block in blocks if isinstance(block, dict)
             and block.get("type") == "text" and isinstance(block.get("text"), str)]
    if len(texts) != len(blocks):
        return "unknown", "mcp_partial_envelope"
    if any(text.lstrip().startswith(_MCP_ERROR_PREFIXES) for text in texts):
        if structured_success:
            return "conflict", "mcp_structured_success_conflicts_with_error_text"
        return "failure", "mcp_error_text_item"
    return "success", "mcp_text_result"
