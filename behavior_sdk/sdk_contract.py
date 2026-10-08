# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Small compiler contract plus an executable, authored API example."""

SDK_DOCUMENTATION = """Author against the exact digest-addressed SDK artifact.
Inspect the real Python modules and their docstrings under behavior_sdk/: public
exports in __init__.py, traces in trace.py, recording in recorder.py, bounded JEV
in jev.py, evidence helpers in runtime.py, profile semantics in native_profile.py,
and CompilePlan in contract.py with its generated schemas/compile-plan.json.
These installed source files are authoritative; this workflow is not an API mirror.

Fetch the deployed artifact and pin its SDK, profile and host runtime identities.
Compile one source/plan candidate, then test it on real teaching trace IDs using
the normal deployed native trace and sandbox/JEV path. Inspect evidence, Recorder
output and JEV receipts; revise when needed, then explicitly accept the immutable
teaching receipt. Population execution requires that accepted DiagnosticVersion.
Compilation can omit authored fixtures; production teaching tests are required
before acceptance. Example source is runnable plumbing, never semantic certification.

Use the supplied profile's semantics. Missing material evidence is UNKNOWN.
Never turn UNCERTAIN into a definite answer.
Do not reconstruct or reserialize evidence.
Select candidates mechanically; never use keywords to gate a semantic claim.
Copy every material condition and exclusion into the program's bounded questions.
Do not catch exceptions in diagnostics; runtime owns ERROR propagation and receipts.
A profile transport outcome is not proof of business completion.
The native profile covers selected window objects, not a complete conversation.
Do not infer trace-wide absence or terminal completion from that window alone.

Native event start/end are float seconds; exact integer nanoseconds remain in
attrs.chq_tsns and attrs.end_tsns. Use raw integers for exact chronology/durations.
Avoid JavaScript number round-tripping of real epoch nanoseconds. This authored
fixture uses small exact JSON integers and matching float seconds. Production
test hydration retains native integer bytes and adapts them inside the runtime.
```json
{"trace":{"trace_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","agent":"authored",
"attrs":{"projection":"native-spans-v1","coverage_scope":"selected-window-objects","conversation_complete":false},
"events":[{"id":"aaaaaaaaaaaaaaaa","parent_id":null,"kind":"llm","name":"authored span",
"start":1.0,"end":1.25,"seq":0,
"attrs":{"id":"aaaaaaaaaaaaaaaa","trace_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
"chq_tsns":1000000000,"end_tsns":1250000000,"openinference_span_kind":"LLM",
"name":"authored span","output_value":"Could you clarify the date?"},
"input":null,"output":"Could you clarify the date?","error":null}]}}
```
"""

SDK_EXAMPLE_SOURCE = '''from behavior_sdk.runtime import evidence, source_refs, record_decision

def evaluate(run, recorder, judge):
    if run.coverage_gaps:
        recorder.error("Missing material telemetry", [g.ref for g in run.coverage_gaps])
        return
    messages = [e for e in run.events if e.kind == "llm"]
    if not messages:
        recorder.visit([e.id for e in run.events], "no message trigger")
        return
    items = [evidence(messages[-1], "output")]
    decision = judge.decide(
        proposition="Does the message ask the reader for clarification?",
        evidence=items, subject_refs=source_refs(items))
    record_decision(recorder, decision, items, subject="clarification request")
'''
