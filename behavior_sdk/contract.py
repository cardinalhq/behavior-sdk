# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Public compilation review metadata; never an execution DSL."""
from dataclasses import dataclass
import ast

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
    obligation_id: str = ""
    judge_site: str = ""

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
    implementation_anchor: str = ""

    def __post_init__(self) -> None:
        if not all((self.name, self.proposition, self.candidate_rule)):
            raise ValueError("judge site must declare proposition and candidate rule")
        if self.selection_kind != "mechanical":
            raise ValueError("JEV candidates may be gated only by mechanically knowable facts")


@dataclass(frozen=True)
class CompilePlan:
    """Review metadata. V1 has one clause per kind; V2 links individual obligations.

    V2 requires unique obligation IDs and executable JudgeSite anchors. It does
    not interpret policy or prove that a proposition preserves its source.
    """
    clauses: tuple[ContractClause, ...]
    judge_sites: tuple[JudgeSite, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version not in (1, 2) or not self.clauses:
            raise ValueError("plan v1/v2 requires at least one clause")
        if self.schema_version == 1 and len({c.kind for c in self.clauses}) != len(self.clauses):
            raise ValueError("duplicate contract clause kind")
        if "unknown_policy" not in {c.kind for c in self.clauses}:
            raise ValueError("plan must state its unknown policy")
        names = {s.name for s in self.judge_sites}
        if len(names) != len(self.judge_sites):
            raise ValueError("duplicate judge site name")
        if self.schema_version == 2:
            ids = [c.obligation_id for c in self.clauses]
            if not all(ids) or len(set(ids)) != len(ids):
                raise ValueError("v2 requires unique nonempty obligation IDs")
            if any(c.judge_site and c.judge_site not in names for c in self.clauses):
                raise ValueError("obligation references an unknown judge site")
            if any(not s.implementation_anchor for s in self.judge_sites):
                raise ValueError("v2 judge sites require executable anchors")

    def validate_source(self, source):
        """Check anchors and literal site propositions, not behavioral equivalence."""
        tree = ast.parse(source)
        functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        constants = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = value
        for clause in self.clauses:
            if clause.implementation_anchor not in functions:
                raise ValueError("missing obligation implementation anchor")
        if self.schema_version == 1:
            return
        for site in self.judge_sites:
            node = functions.get(site.implementation_anchor)
            if node is None:
                raise ValueError("missing judge site implementation anchor")
            propositions = []
            for call in ast.walk(node):
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute) or call.func.attr != "decide":
                    continue
                for kw in call.keywords:
                    if kw.arg == "proposition":
                        propositions.append(kw.value.value if isinstance(kw.value, ast.Constant) else
                                            constants.get(kw.value.id) if isinstance(kw.value, ast.Name) else None)
            if site.proposition not in propositions:
                raise ValueError(f"judge site {site.name} proposition is not present at its anchor")
