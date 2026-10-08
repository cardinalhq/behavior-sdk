Author against the exact digest-addressed SDK artifact.
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

Structural substrate v0.2 (ordinary Python; broad JEV remains supported):
run.select(kind=..., name=...) selects occurrences mechanically. Selections
support before(ref), after(ref), earliest(), latest(), slicing, and refs.
before/after compare canonical sequence only; latest/earliest return None when
empty and never imply authority, completeness, or business finality.
selection.available_before(ref, field="output", anchor="start") returns events
and unknown_refs using the profile's explicit availability_policy. Native span
outputs become available at exact end_tsns, not at span start; equal/missing
timestamps remain unresolved. This describes recorded time, not agent knowledge.
Profiles with availability_policy="sequence" guarantee fields are available at
their unique occurrence seq. Other profiles default to unknown availability.
Keep distinct occurrence IDs even if payloads match. Profiles own normalization;
never deduplicate events by text, tool name, arguments, or output equality.

EvidenceSet.from_events(events, fields=("input", "output"), role="context")
selects exact original fields and skips nulls by default. Use include_missing=True
only deliberately. concat combines EvidenceSets in order; refs returns real IDs;
frame is an optional structural role-to-item map, passed explicitly to JEV.
There is no implicit Call-to-evidence conversion; select origin/result events and
fields. Existing evidence(event, field), source_refs, and Evidence remain available.
judge.preflight(proposition=..., evidence=..., subject_refs=..., frame=...)
returns fits, reasons, item_count, packet_chars, request_bytes. It calls no model,
never truncates, uses decide's exact checks, and rejects invalid provenance.
It does not reserve or check remaining semantic-call budget.

Decision.yes/no/uncertain are explicit predicates; bool(decision) raises.
Branch on uncertain separately; not decision.yes includes uncertainty.
all_of(*decisions, complete=True) and any_of(*decisions, complete=True) compose
receipted judgments deterministically. NO disproves a conjunction, YES proves an
existential. Otherwise UNCERTAIN or complete=False prevents a definite result.
Empty complete all_of is YES; empty complete any_of is NO. Only set complete=True
when the relevant candidate/premise set has actually been assessed.
Record the terminal behavioral decision with record_decision, not intermediate
positive relation judgments. Compositions retain leaf receipt proofs; a v0.2 host
validates these before discharging irrelevant uncertainty. Unconsumed uncertain
receipts still produce UNKNOWN. Recorder.unknown(reason, events) is the explicit
semantic/missing-evidence API; it retains the legacy error wire record. Exceptions
are execution ERROR and must propagate. A decisive independent match survives
semantic uncertainty elsewhere, but operational failure still yields ERROR.

CompilePlan v1 remains supported. V2 allows multiple clauses per kind, requires
unique obligation_id values, optional judge_site links, and an implementation_anchor
on each JudgeSite. validate_source checks function anchors and literal propositions
at judge.decide calls (literal strings or module constants). It does not prove
semantic equivalence. Represent each material exclusion and unknown branch as an
individual source-linked obligation; plan prose is not executable policy.

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
