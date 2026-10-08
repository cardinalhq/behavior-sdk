# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Public compilation review metadata; never an execution DSL."""
from dataclasses import dataclass

CLAUSE_KINDS = frozenset({
    "population", "trigger", "scope", "identity", "temporal_window",
    "evidence_requirement", "success_or_failure_definition", "threshold",
    "allowed_exceptions", "unknown_policy",
})


@dataclass(frozen=True)
class ContractClause:
    """Source-linked behavioral obligation.

    source_ref names behavior, sdk, profile, context, correction, or
    example:<trace_id> for a supplied authored teaching example. source_text
    must quote one contiguous substring of that source (whitespace normalized).
    implementation_anchor names an actual top-level function in the UDF.
    """
    kind: str
    interpretation: str
    source_ref: str
    source_text: str
    implementation_anchor: str

    def __post_init__(self) -> None:
        if self.kind not in CLAUSE_KINDS or not all((self.interpretation, self.source_ref,
                                                      self.source_text, self.implementation_anchor)):
            raise ValueError("contract clause needs a known kind, text, source, and code anchor")


@dataclass(frozen=True)
class JudgeSite:
    """Declare a bounded semantic question and mechanical candidate selection.

    The proposition must preserve material contract conditions/exclusions and
    fit the JEV API bounds. Plan prose is review metadata, never executed policy.
    """
    name: str
    proposition: str
    candidate_rule: str
    selection_kind: str = "mechanical"

    def __post_init__(self) -> None:
        if not all((self.name, self.proposition, self.candidate_rule)):
            raise ValueError("judge site must declare proposition and candidate rule")
        if self.selection_kind != "mechanical":
            raise ValueError("JEV candidates may be gated only by mechanically knowable facts")


@dataclass(frozen=True)
class CompilePlan:
    """One clause per kind, including unknown_policy; schema version is exactly 1."""
    clauses: tuple[ContractClause, ...]
    judge_sites: tuple[JudgeSite, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1 or not self.clauses:
            raise ValueError("plan v1 requires at least one clause")
        if len({c.kind for c in self.clauses}) != len(self.clauses):
            raise ValueError("duplicate contract clause kind")
        if "unknown_policy" not in {c.kind for c in self.clauses}:
            raise ValueError("plan must state its unknown policy")
