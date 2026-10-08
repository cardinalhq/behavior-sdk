# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Explicit three-valued composition with auditable references to JEV receipts."""
from .jev import Decision, Verdict, PacketError


def _value(operator, values, complete):
    if operator == "all":
        if Verdict.NO in values:
            return Verdict.NO
        return Verdict.YES if complete and all(v == Verdict.YES for v in values) else Verdict.UNCERTAIN
    if Verdict.YES in values:
        return Verdict.YES
    return Verdict.NO if complete and all(v == Verdict.NO for v in values) else Verdict.UNCERTAIN


def _combine(operator, decisions, complete):
    if type(complete) is not bool or any(not isinstance(d, Decision) for d in decisions):
        raise TypeError("compose Decisions with an explicit boolean completeness flag")
    proofs = []
    for decision in decisions:
        proof = decision.receipt.get("composition")
        if proof is None:
            receipt_id = decision.receipt.get("receipt_id")
            if not receipt_id:
                raise PacketError("composition requires receipted judgments")
            proof = {"receipt_id": receipt_id}
        proofs.append(proof)
    result = _value(operator, [d.verdict for d in decisions], complete)
    refs = tuple(dict.fromkeys(ref for d in decisions for ref in d.evidence_refs))
    proof = {"operator": operator, "complete": complete, "inputs": proofs}
    return Decision(result, refs, f"{operator} composition: {result.value}", {"composition": proof})


def all_of(*decisions, complete=True):
    """Conjunction: NO is decisive; otherwise missing/uncertain premises stay uncertain."""
    return _combine("all", decisions, complete)


def any_of(*decisions, complete=True):
    """Existential result: YES is decisive; unassessed candidates prevent definite NO."""
    return _combine("any", decisions, complete)


def unresolved_uncertainty(records, receipts):
    """Host integration: validate proofs before discharging irrelevant uncertainty.

    Without explicit assessments this is the legacy any-UNCERTAIN policy.
    Proofs describe Boolean receipt composition, never hidden semantic judgments.
    """
    lookup = {r["receipt_id"]: r for r in receipts}
    covered = set()
    nodes = 0

    def evaluate(proof, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 512 or depth > 16 or not isinstance(proof, dict):
            raise PacketError("invalid assessment proof")
        if set(proof) == {"receipt_id"}:
            key = proof["receipt_id"]
            if not isinstance(key, str) or key not in lookup:
                raise PacketError("assessment cites an unknown receipt")
            return Verdict(lookup[key]["decision"]), {key}
        if set(proof) != {"operator", "complete", "inputs"} or proof["operator"] not in {"all", "any"}:
            raise PacketError("invalid assessment operator")
        if type(proof["complete"]) is not bool or not isinstance(proof["inputs"], list) or len(proof["inputs"]) > 64:
            raise PacketError("invalid assessment inputs")
        children = [evaluate(p, depth + 1) for p in proof["inputs"]]
        return _value(proof["operator"], [v for v, _ in children], proof["complete"]), set().union(*(ids for _, ids in children))

    unresolved = False
    for record in records:
        if record["op"] != "assessment":
            continue
        verdict, ids = evaluate(record["proof"])
        if verdict.value != record["verdict"]:
            raise PacketError("assessment verdict differs from its receipt proof")
        if verdict == Verdict.UNCERTAIN:
            unresolved = True
        else:
            covered.update(ids)
    return unresolved or any(r.get("decision") == "UNCERTAIN" and r["receipt_id"] not in covered for r in receipts)
