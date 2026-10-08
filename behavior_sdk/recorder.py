# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Canonical normalized traces and Recorder output ABI.

A UDF is a Python module with `evaluate(run: TraceView, recorder: Recorder, judge: JEV) -> None`.
It may compute anything from the trace. Everything it wants Cardinal (the renderer,
the population view) to know must go through the Recorder calls below; return values
are ignored. The renderer never sees UDF source.

Normalized trace model (agent-agnostic): a trace is a tree of Events.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence

Scalar = Any  # str | int | float | bool | None | list | dict (JSON values)

KINDS = ("agent", "llm", "tool", "message", "step", "outcome", "other")
# Converters MUST append the run's final deliverables (final answer, patch, report, final state)
# as kind="outcome" events (name = deliverable type, output = content) so witnesses can cite them.
# Converters MUST put run-level facts the source records (task, success/reward, totals, config)
# in Trace.attrs, and MUST NOT include benchmark judge labels / failure annotations.


@dataclass(frozen=True)
class Event:
    id: str
    parent_id: Optional[str]
    kind: str                     # one of KINDS
    name: str                     # tool name, model name, agent/role name, message role
    start: float                  # seconds (epoch or relative); order key with seq
    end: Optional[float]
    seq: int                      # total order assigned by the converter
    attrs: Mapping[str, Scalar]   # agent-specific attributes, verbatim where possible
    input: Optional[Scalar] = None    # tool args / prompt (may be truncated)
    output: Optional[Scalar] = None   # tool result / completion (may be truncated)
    error: Optional[str] = None       # None == success when the source records outcome


@dataclass(frozen=True)
class Trace:
    trace_id: str
    agent: str
    events: Sequence[Event]       # sorted by seq
    attrs: Mapping[str, Scalar]   # run-level: task, outcome, totals the source records


class Recorder:
    """The only output channel of a UDF. Every call appends one record."""

    def __init__(self, behavior_id: str, trace: Trace):
        self.behavior_id = behavior_id
        self._ids = {e.id for e in trace.events} | {"run"}  # "run" = the run itself (Trace.attrs)
        self.records: list[dict] = []

    def _ev(self, ids):
        ids = [ids] if isinstance(ids, str) else list(ids or [])
        for i in ids:
            if i not in self._ids:
                raise ValueError(f"unknown event id {i!r}")
        return ids

    def _add(self, rec):
        json.dumps(rec)  # must be JSON-serializable
        self.records.append(rec)

    # --- applicability / denominators -------------------------------------
    def visit(self, events, subject: str):
        """Event(s) this behavior examined as `subject` (denominator)."""
        self._add({"op": "visit", "events": self._ev(events), "subject": subject})

    def match(self, events, subject: str, *, bindings: Mapping[str, Scalar] | None = None):
        """Event(s) that satisfied the subject predicate (numerator before the verdict)."""
        self._add({"op": "match", "events": self._ev(events), "subject": subject,
                   "bindings": dict(bindings or {})})

    # --- verdict ------------------------------------------------------------
    def violation(self, *, witness: Sequence[str], reason: str,
                  bindings: Mapping[str, Scalar] | None = None, expected: str | None = None,
                  observed: Scalar = None, severity: str | None = None):
        """One violation instance. witness = event ids, in the order a human should read them."""
        self._add({"op": "violation", "witness": self._ev(witness), "reason": reason,
                   "bindings": dict(bindings or {}), "expected": expected, "observed": observed,
                   "severity": severity})

    def not_applicable(self, reason: str):
        """The behavior's applies_when precondition does not hold for this run."""
        self._add({"op": "not_applicable", "reason": reason})

    def error(self, reason: str, events=None):
        """The UDF could not decide (missing data, malformed trace). Not a violation."""
        self._add({"op": "error", "reason": reason, "events": self._ev(events)})

    def unknown(self, reason: str, events=None):
        """Semantic/missing-evidence UNKNOWN. Retains the legacy error wire record.

        Operational exceptions must propagate; they are host ERROR results.
        """
        self.error(reason, events)

    def assessment(self, *, proof: Mapping, verdict: str):
        """Receipt composition emitted by record_decision; host validates the proof."""
        if verdict not in {"YES", "NO", "UNCERTAIN"}:
            raise ValueError("unsupported assessment verdict")
        self._add({"op": "assessment", "proof": dict(proof), "verdict": verdict})

    # --- measurements -------------------------------------------------------
    def counter(self, name: str, value: float = 1, *, unit: str = "1", events=None,
                bindings: Mapping[str, Scalar] | None = None):
        self._add({"op": "counter", "name": name, "value": value, "unit": unit,
                   "events": self._ev(events), "bindings": dict(bindings or {})})

    def gauge(self, name: str, value: float, *, unit: str, events=None,
              bindings: Mapping[str, Scalar] | None = None, limit: float | None = None):
        self._add({"op": "gauge", "name": name, "value": value, "unit": unit, "limit": limit,
                   "events": self._ev(events), "bindings": dict(bindings or {})})

    def dimension(self, name: str, value: Scalar, events=None):
        """A categorical attribute of this evaluation for population slicing/grouping."""
        self._add({"op": "dimension", "name": name, "value": value, "events": self._ev(events)})

    # --- structure for the renderer -----------------------------------------
    def transition(self, src: str, dst: str, *, label: str, state_from: Scalar = None,
                   state_to: Scalar = None, bindings: Mapping[str, Scalar] | None = None):
        """An ordered edge between two events (retry chain, trigger->response, step->step)."""
        self._add({"op": "transition", "src": self._ev(src)[0], "dst": self._ev(dst)[0],
                   "label": label, "state_from": state_from, "state_to": state_to,
                   "bindings": dict(bindings or {})})

    def evidence(self, value: Scalar, *, role: str, source: Optional[str] = None,
                 sink: Optional[str] = None, status: str = "ok", note: str | None = None):
        """A value flowing source -> sink. role e.g. 'cited_id'. status ok|missing|foreign|stale."""
        self._add({"op": "evidence", "value": value, "role": role,
                   "source": self._ev(source)[0] if source else None,
                   "sink": self._ev(sink)[0] if sink else None, "status": status, "note": note})


def freeze_attrs(d: Mapping) -> Mapping:
    return MappingProxyType(dict(d))
