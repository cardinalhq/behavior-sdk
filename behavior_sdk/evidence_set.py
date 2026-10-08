# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Immutable ordered evidence groups; no reconstruction, inference, or truncation."""
from collections.abc import Sequence
from dataclasses import dataclass
import json

from .jev import EvidenceItem
from .runtime import evidence, source_refs


@dataclass(frozen=True)
class EvidenceSet(Sequence):
    """Trace-backed fields and optional author-assigned structural roles.

    Pass this collection to decide/preflight and pass its frame explicitly when
    roles matter. Roles label evidence, not facts established by a model.
    No Call overload: choose origin/result events and fields explicitly.
    """
    items: tuple[EvidenceItem, ...] = ()
    roles: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "roles", tuple(self.roles) or ("",) * len(self.items))
        if len(self.roles) != len(self.items) or any(not isinstance(i, EvidenceItem) for i in self.items):
            raise TypeError("evidence items and roles must have matching lengths")
        if any(not isinstance(r, str) or len(r) > 64 for r in self.roles):
            raise ValueError("roles must be strings of at most 64 characters")

    def __len__(self):
        return len(self.items)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return EvidenceSet(self.items[index], self.roles[index])
        return self.items[index]

    @classmethod
    def from_events(cls, events, *, fields=("input", "output"), role="", include_missing=False):
        """Select exact original fields; skip null fields unless explicitly requested."""
        if isinstance(fields, str):
            raise TypeError("fields must be a sequence, not a string")
        items = []
        for event in events:
            for field in fields:
                item = evidence(event, field)  # validates field even when it is absent
                value = event.attrs.get(field[6:]) if field.startswith("attrs.") else getattr(event, field)
                if value is not None or include_missing:
                    items.append(item)
        return cls(tuple(items), (role,) * len(items))

    def concat(self, *others):
        """Preserve occurrence, field, role and order, including repeated selections."""
        items, roles = list(self.items), list(self.roles)
        for other in others:
            if not isinstance(other, EvidenceSet):
                raise TypeError("concat requires EvidenceSet")
            items.extend(other.items)
            roles.extend(other.roles)
        return EvidenceSet(tuple(items), tuple(roles))

    @property
    def refs(self):
        return source_refs(self.items)

    @property
    def frame(self):
        """Deterministic role-to-packet-item map; subject to ordinary frame limits."""
        groups = {}
        for index, role in enumerate(self.roles, 1):
            if role:
                groups.setdefault(role, []).append(index)
        return json.dumps({"evidence_roles": groups}, separators=(",", ":")) if groups else ""
