# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Shared evidence construction and execution errors; no behavior semantics."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from .jev import Decision, EvidenceItem, PacketError, Verdict, _field_text


def evidence(source: Any, field: str) -> EvidenceItem:
    """Use the exact serialization that the judge validates, including nulls."""
    is_trace = not hasattr(source, "id") and hasattr(source, "trace_id")
    ref = "run" if is_trace else source.id
    return EvidenceItem(ref, field, _field_text(source, field, is_trace=is_trace))


def source_refs(items: Iterable[EvidenceItem]) -> tuple[str, ...]:
    """Stable, deduplicated references to the supplied evidence, not copied IDs."""
    return tuple(dict.fromkeys(item.ref for item in items))


def record_decision(recorder: Any, decision: Decision, items: Iterable[EvidenceItem],
                    *, subject: str) -> None:
    """Translate a successful bounded decision; exceptions are never uncertainty."""
    refs = source_refs(items)
    if not set(decision.evidence_refs) <= set(refs):
        raise PacketError("decision references are outside supplied evidence")
    recorder.visit(refs, subject)
    witness = decision.evidence_refs or refs
    if decision.verdict == Verdict.YES:
        recorder.violation(witness=witness, reason=decision.reason)
    elif decision.verdict == Verdict.UNCERTAIN:
        recorder.error(decision.reason, witness)
    elif decision.verdict != Verdict.NO:
        raise PacketError("unsupported semantic decision")


class DiagnosticProgramError(RuntimeError):
    """Operational/program failure with evidence collected before failure."""

    def __init__(self, cause: Exception, records: tuple[dict, ...],
                 jev_receipts: tuple[dict, ...]):
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.records = records
        self.jev_receipts = jev_receipts


def execute_program(program: Any, run: Any, recorder: Any, judge: Any) -> None:
    """One runtime boundary shared by teaching, backtests and deployed workers."""
    try:
        program(run, recorder, judge)
    except Exception as exc:
        raise DiagnosticProgramError(exc, tuple(recorder.records),
                                     tuple(dict(r) for r in judge.receipts)) from exc


def runtime_digest() -> str:
    """Identify the exact installed public authoring artifact used by workers."""
    from .authoring import artifact_digest
    return artifact_digest()
