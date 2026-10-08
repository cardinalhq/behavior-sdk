from behavior_sdk.runtime import evidence, source_refs, record_decision

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
