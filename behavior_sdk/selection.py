# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Mechanical selection. Sequence order is not evidence availability or authority."""
from dataclasses import dataclass
from collections.abc import Sequence


@dataclass(frozen=True)
class EventSelection(Sequence):
    """Ordered occurrences from one TraceView; equal content never merges events."""
    run: object
    events: tuple

    def __post_init__(self):
        events = tuple(self.events)
        for event in events:
            self.run.event(event)
        if any(left.seq >= right.seq for left, right in zip(events, events[1:])):
            raise ValueError("selections require distinct events in canonical sequence order")
        object.__setattr__(self, "events", events)

    def __len__(self):
        return len(self.events)

    def __getitem__(self, index):
        value = self.events[index]
        return EventSelection(self.run, value) if isinstance(index, slice) else value

    @property
    def refs(self):
        return tuple(event.id for event in self.events)

    def select(self, *, kind=None, name=None):
        return EventSelection(self.run, tuple(e for e in self if
            (kind is None or e.kind == kind) and (name is None or e.name == name)))

    def before(self, ref):
        """Strict canonical sequence comparison, not a result-availability claim."""
        anchor = self.run.event(ref)
        return EventSelection(self.run, tuple(e for e in self if e.seq < anchor.seq))

    def after(self, ref):
        anchor = self.run.event(ref)
        return EventSelection(self.run, tuple(e for e in self if e.seq > anchor.seq))

    def latest(self):
        """Last retained occurrence, or None. Does not imply finality/authority."""
        return self.events[-1] if self.events else None

    def earliest(self):
        return self.events[0] if self.events else None

    def available_before(self, ref, *, field="output", anchor="start"):
        """Use the profile's explicit availability policy; unknowns stay separate.

        The native profile compares exact result end-ns with anchor start/end-ns.
        A sequence-availability profile must explicitly guarantee its sequence.
        These are recorded temporal facts, not proof of the agent's knowledge.
        """
        target = self.run.event(ref)
        if field not in {"input", "output", "error", "name"}:
            raise ValueError("availability requires input/output/error/name")
        if anchor not in {"start", "end"}:
            raise ValueError("anchor must be start or end")
        yes, unknown = [], []
        for event in self:
            status = self.run.profile.available_before(event, field, target, anchor)
            if status is True:
                yes.append(event)
            elif status is None:
                unknown.append(event.id)
            elif status is not False:
                raise TypeError("profile availability must be True, False, or None")
        return Availability(EventSelection(self.run, tuple(yes)), tuple(unknown))


@dataclass(frozen=True)
class Availability:
    """Known available events and occurrences whose availability is unresolved.

    An empty unknown_refs tuple is not a claim of trace-wide coverage.
    """
    events: EventSelection
    unknown_refs: tuple[str, ...]

    def __bool__(self):
        raise TypeError("inspect events and unknown_refs explicitly")
