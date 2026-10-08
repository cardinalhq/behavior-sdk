# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Profile-backed, evidence-preserving views over normalized traces.

The behavioral predicate remains ordinary Python.  Profiles only interpret
executed operations, their results, and status signals from a trace format.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from behavior_sdk.recorder import Event, Trace


class Outcome(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"


class ResultState(str, Enum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class CoverageGap:
    ref: str
    reason: str


@dataclass(frozen=True)
class ResultView:
    state: ResultState
    refs: tuple[str, ...] = ()
    value: Any = None


@dataclass(frozen=True)
class Call:
    # ref is always a real event id usable by the existing Recorder.  call_id
    # disambiguates multiple operations executed in one code cell.
    ref: str
    call_id: str
    name: str
    arguments: Mapping[str, Any]
    result: ResultView
    outcome: Outcome
    signals: tuple[str, ...]
    source_refs: tuple[str, ...]
    origin_refs: tuple[str, ...]
    seq: int
    contradictory: bool = False


def _coerce_trace(raw: Trace | Mapping[str, Any]) -> Trace:
    # udf.run imports the same frozen source as top-level ``behavior``;
    # accept that loader's Trace class without depending on import spelling.
    if isinstance(raw, Trace) or (hasattr(raw, "events") and hasattr(raw, "trace_id")
                                  and hasattr(raw, "agent") and hasattr(raw, "attrs")):
        return raw
    events = []
    for item in raw["events"]:
        attrs = dict(item.get("attrs") or {})
        events.append(Event(
            id=item["id"], parent_id=item.get("parent_id"), kind=item["kind"],
            name=item.get("name") or "", start=item.get("start") or 0.0,
            end=item.get("end"), seq=item["seq"], attrs=attrs,
            input=item.get("input"), output=item.get("output"), error=item.get("error"),
        ))
    events.sort(key=lambda event: event.seq)
    return Trace(raw["trace_id"], raw["agent"], tuple(events), dict(raw.get("attrs") or {}))


class TraceView:
    """A trace and one immutable profile version, with explicit finality."""

    def __init__(self, trace: Trace | Mapping[str, Any], *, profile: Any, finalized: bool = True):
        from .profiles import Profile

        self.trace = _coerce_trace(trace)
        self.profile = Profile.load(profile) if isinstance(profile, str) else profile
        if not isinstance(self.profile, Profile):
            raise TypeError("profile must be a Profile or registered profile name")
        self.finalized = bool(finalized)
        self.events = tuple(sorted(self.trace.events, key=lambda event: event.seq))
        self._calls: tuple[Call, ...] | None = None

    @property
    def coverage_gaps(self) -> tuple[CoverageGap, ...]:
        return tuple(self.profile.coverage_gaps(self))

    def calls(self, *, name: str | None = None) -> tuple[Call, ...]:
        if self._calls is None:
            self._calls = tuple(self.profile.calls(self))
        return self._calls if name is None else tuple(call for call in self._calls if call.name == name)
