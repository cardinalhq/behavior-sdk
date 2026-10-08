# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
import itertools
import json
import unittest
from dataclasses import replace

from behavior_sdk import (TraceView, Recorder, EvidenceSet, Judge, JudgeConfig,
                          Verdict, all_of, any_of, record_decision, evidence)
from behavior_sdk.jev import PacketError, Decision
from behavior_sdk.native_profile import NativeProfile
from behavior_sdk.profiles import Profile
from behavior_sdk.logic import unresolved_uncertainty
from behavior_sdk.contract import CompilePlan, ContractClause, JudgeSite
from behavior_sdk.selection import EventSelection


def run(events=None, profile=None):
    events = events or [dict(id="a", seq=0, kind="tool", input={"x": "é"}, output="same",
                            attrs={"chq_tsns": 100, "end_tsns": 500}),
                       dict(id="b", seq=1, kind="tool", output="same",
                            attrs={"chq_tsns": 200, "end_tsns": 250}),
                       dict(id="c", seq=2, kind="llm", output="done",
                            attrs={"chq_tsns": 300, "end_tsns": 400})]
    return TraceView(dict(trace_id="trace", agent="test", events=events), profile=profile or NativeProfile())


def judge(trace, answers=("YES",), **limits):
    iterator = iter(answers)
    def backend(request):
        return dict(model="test", model_version="1", text=json.dumps(dict(
            answer=next(iterator), evidence_refs=[1], short_reason="test")))
    return Judge(trace, JudgeConfig(model="test", model_version="1", **limits), backend)


class StructuralTests(unittest.TestCase):
    def test_selection_keeps_equal_content_occurrences(self):
        r = run()
        self.assertEqual(r.select(kind="tool").refs, ("a", "b"))
        self.assertEqual(r.before("c").after("a").refs, ("b",))
        self.assertIs(r.select().latest(), r.events[-1])
        self.assertIsNone(r.select(kind="absent").latest())
        self.assertEqual(r.select()[1:].refs, ("b", "c"))
        with self.assertRaises(ValueError): r.before("absent")
        with self.assertRaises(ValueError): r.before(replace(r.events[0]))

    def test_ambiguous_occurrence_order_rejected(self):
        for field, value in [("id", "a"), ("id", "run"), ("seq", 0), ("seq", True)]:
            events = [dict(id="a", seq=0, kind="tool"), dict(id="b", seq=1, kind="tool")]
            events[1][field] = value
            with self.assertRaises(ValueError): run(events)

    def test_direct_selection_preserves_local_ordered_immutable_occurrences(self):
        r = run()
        events = list(r.events)
        selected = EventSelection(r, events)
        events.clear()
        self.assertEqual(selected.refs, ("a", "b", "c"))
        for invalid in ((replace(r.events[0]),), (r.events[1], r.events[0]),
                        (r.events[0], r.events[0])):
            with self.assertRaises(ValueError): EventSelection(r, invalid)
        with self.assertRaises(ValueError): selected[::-1]

    def test_sequence_before_does_not_leak_future_result(self):
        r = run()
        self.assertEqual(r.before("c").refs, ("a", "b"))
        available = r.select(kind="tool").available_before("c")
        self.assertEqual(available.events.refs, ("b",))
        self.assertEqual(available.unknown_refs, ())
        self.assertEqual(r.select(kind="tool").available_before("c", field="input").unknown_refs, ("b",))

    def test_equal_or_missing_exact_time_is_unknown(self):
        events = [dict(id="a",seq=0,kind="tool",output="ok",attrs={"end_tsns":300}),
                  dict(id="b",seq=1,kind="tool",output="ok"),
                  dict(id="c",seq=2,kind="llm",attrs={"chq_tsns":300})]
        selected = run(events).select(kind="tool").available_before("c")
        self.assertEqual(selected.unknown_refs, ("a", "b"))
        self.assertEqual(len(selected.events), 0)

    def test_profile_must_declare_availability(self):
        r = run(profile=Profile("plain", "1", "digest"))
        self.assertEqual(r.before("c").available_before("c").unknown_refs, ("a", "b"))
        r = run(profile=Profile("ordered", "1", "digest", "sequence"))
        self.assertEqual(r.before("c").available_before("c").events.refs, ("a", "b"))

    def test_evidence_roles_fields_and_occurrences(self):
        r = run()
        tools = EvidenceSet.from_events(r.select(kind="tool"), fields=("input", "output"), role="state")
        claim = EvidenceSet.from_events(r.select(kind="llm"), fields=("output",), role="claim")
        packet = tools.concat(claim)
        self.assertEqual(packet.refs, ("a", "b", "c"))
        self.assertEqual(packet[0], evidence(r.events[0], "input"))
        self.assertEqual(json.loads(packet.frame), {"evidence_roles":{"state":[1,2,3],"claim":[4]}})
        j = judge(r.trace)
        self.assertTrue(j.decide(proposition="test", evidence=packet, frame=packet.frame).yes)
        self.assertEqual(len(tools.concat(tools)), 6)
        self.assertEqual(packet[-1:].roles, ("claim",))

    def test_evidence_rejects_forgery_and_retains_missing_choice(self):
        r = run()
        self.assertEqual(len(EvidenceSet.from_events([r.events[1]], fields=("input",))), 0)
        items = EvidenceSet.from_events([r.events[1]], fields=("input",), include_missing=True)
        self.assertEqual(items[0].text, "")
        with self.assertRaises(PacketError):
            judge(r.trace).preflight(proposition="test", evidence=[replace(items[0], text="forged")])
        with self.assertRaises(TypeError): EvidenceSet.from_events(r.events, fields="output")

    def test_field_iterator_is_applied_to_every_event(self):
        r = run()
        expected = EvidenceSet.from_events(r.events, fields=("name", "output"))
        actual = EvidenceSet.from_events(iter(r.events), fields=iter(("name", "output")))
        self.assertEqual(actual, expected)
        self.assertEqual(actual.refs, ("a", "b", "c"))

    def test_preflight_exactly_matches_execution_bounds(self):
        r = run()
        items = EvidenceSet.from_events(r.events, fields=("output",))
        for limits in [dict(max_items=1), dict(max_packet_chars=12), dict(max_input_tokens=20)]:
            j = judge(r.trace, **limits)
            result = j.preflight(proposition="test", evidence=items, frame="context")
            self.assertFalse(result.fits)
            self.assertEqual(j.receipts, [])
            with self.assertRaises(PacketError): j.decide(proposition="test", evidence=items, frame="context")
        j = judge(r.trace)
        result = j.preflight(proposition="test", evidence=items)
        self.assertEqual(result.packet_chars, 16)
        self.assertTrue(result.fits)
        self.assertTrue(j.decide(proposition="test", evidence=items).yes)

    def test_decisions_are_not_boolean(self):
        for v in Verdict:
            d = Decision(v, (), "", {})
            with self.assertRaises(TypeError): bool(d)
            self.assertEqual((d.yes,d.no,d.uncertain), (v==Verdict.YES,v==Verdict.NO,v==Verdict.UNCERTAIN))

    def test_all_truth_tables_and_incomplete_candidates(self):
        for a,b in itertools.product(Verdict, repeat=2):
            ds = [Decision(v, (str(i),), "", {"receipt_id": str(i)}) for i,v in enumerate((a,b))]
            both = all_of(*ds).verdict
            either = any_of(*ds).verdict
            self.assertEqual(both, Verdict.NO if Verdict.NO in (a,b) else Verdict.YES if a==b==Verdict.YES else Verdict.UNCERTAIN)
            self.assertEqual(either, Verdict.YES if Verdict.YES in (a,b) else Verdict.NO if a==b==Verdict.NO else Verdict.UNCERTAIN)
        no = Decision(Verdict.NO, (), "", {"receipt_id":"a"})
        self.assertTrue(any_of(no, complete=False).uncertain)
        self.assertTrue(any_of(complete=False).uncertain)
        self.assertTrue(any_of().no)
        self.assertTrue(all_of().yes)

    def test_receipt_proof_discharge_and_unassessed_uncertainty(self):
        r = run(); j = judge(r.trace, ("UNCERTAIN","NO","UNCERTAIN"))
        items = EvidenceSet.from_events(r.events, fields=("output",))
        a = j.decide(proposition="a",evidence=items)
        b = j.decide(proposition="b",evidence=items)
        rec = Recorder("test",r.trace)
        record_decision(rec, all_of(a,b), items, subject="combined")
        self.assertFalse(unresolved_uncertainty(rec.records,j.receipts))
        j.decide(proposition="c",evidence=items)
        self.assertTrue(unresolved_uncertainty(rec.records,j.receipts))
        rec.records[1]["verdict"] = "YES"
        with self.assertRaises(PacketError): unresolved_uncertainty(rec.records,j.receipts)

    def test_unknown_keeps_legacy_wire(self):
        r=run(); a=Recorder("test",r.trace); b=Recorder("test",r.trace)
        a.unknown("missing",["a"]); b.error("missing",["a"])
        self.assertEqual(a.records,b.records)

    def test_plan_v2_individual_obligations_and_executable_linkage(self):
        clauses=tuple(ContractClause("unknown_policy","policy","behavior","text","evaluate",n,"question") for n in ("missing","uncertain"))
        site=JudgeSite("question","Is it true?","all events",implementation_anchor="evaluate")
        plan=CompilePlan(clauses,(site,),2)
        plan.validate_source('def evaluate(run, recorder, judge):\n judge.decide(proposition="Is it true?", evidence=[])\n')
        with self.assertRaises(ValueError): plan.validate_source('def evaluate(run, recorder, judge):\n pass\n')
        with self.assertRaises(ValueError): CompilePlan(clauses,(site,),1)
        with self.assertRaises(ValueError): CompilePlan((clauses[0],clauses[0]),(site,),2)


if __name__ == "__main__":
    unittest.main()
