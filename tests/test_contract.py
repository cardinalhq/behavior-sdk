# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Public ABI regressions using authored fixtures; no production evaluator."""
import hashlib
import inspect
import json
from pathlib import Path
import runpy
import struct
import subprocess
import sys
import tempfile
import unittest

from behavior_sdk import Evidence, JEV, Judge, JudgeConfig, Recorder, TraceView, Verdict
from behavior_sdk.authoring import authoring_artifact, artifact_digest, canonical, compile_plan_schema
from behavior_sdk.contract import CompilePlan, ContractClause
from behavior_sdk.jev import PacketError
from behavior_sdk.native_profile import NativeProfile, adapt
from behavior_sdk.runtime import evidence

ROOT = Path(__file__).resolve().parents[1]


def example_trace():
    return {'trace_id': 'b' * 32, 'agent': 'authored', 'events': [
        {'id': 'a' * 16, 'kind': 'llm', 'seq': 0, 'output': 'Could you clarify the date?'}]}


class ContractTests(unittest.TestCase):
    def test_artifact_contains_exact_source_and_license(self):
        artifact = authoring_artifact()
        self.assertEqual(artifact_digest(), hashlib.sha256(canonical(artifact)).hexdigest())
        for path in (ROOT / 'behavior_sdk').glob('*.py'):
            self.assertEqual(artifact['files']['behavior_sdk/' + path.name], path.read_text())
        self.assertEqual(artifact['files']['LICENSE'], (ROOT / 'LICENSE').read_text())
        self.assertIn('Apache License', artifact['files']['LICENSE'])
        self.assertEqual(artifact['files']['behavior_sdk/LICENSE'], artifact['files']['LICENSE'])
        from behavior_sdk import __version__
        self.assertEqual(artifact['version'], __version__)
        self.assertEqual(artifact['files']['schemas/compile-plan.json'], canonical(compile_plan_schema()).decode())
        self.assertFalse(any('behavior_runtime' in name for name in artifact['files']))

    def test_materialized_release_artifact_reproduces_runtime_identity(self):
        artifact = authoring_artifact()
        with tempfile.TemporaryDirectory() as directory:
            for name, content in artifact['files'].items():
                path = Path(directory) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            result = subprocess.run([sys.executable, '-c',
                'from behavior_sdk.authoring import artifact_digest; print(artifact_digest())'],
                cwd=directory, check=True, text=True, capture_output=True)
        self.assertEqual(result.stdout.strip(), artifact_digest())

    def test_schema_follows_contract_types_and_defaults(self):
        schema = compile_plan_schema()
        self.assertEqual(schema['properties']['schema_version']['type'], 'integer')
        self.assertEqual(schema['properties']['schema_version']['const'], 1)
        self.assertFalse(schema['additionalProperties'])
        clauses = schema['properties']['clauses']['items']
        self.assertEqual(set(clauses['required']), set(ContractClause.__dataclass_fields__))
        self.assertEqual(schema['properties']['judge_sites']['default'], CompilePlan.__dataclass_fields__['judge_sites'].default)
        with self.assertRaises(ValueError):
            CompilePlan(clauses=())

    def test_canonical_jev_and_example_execute_with_source_backed_evidence(self):
        self.assertIs(Judge.decide, JEV.decide)
        self.assertEqual(inspect.signature(Judge.decide), inspect.signature(JEV.decide))
        run = TraceView(example_trace(), profile=NativeProfile())
        recorder = Recorder('authored-example', run.trace)
        requests = []
        def backend(request):
            # Deterministic provider-wire fixture tests SDK plumbing, not semantics.
            requests.append(request)
            return {'model': 'test', 'model_version': 'fixture',
                    'text': json.dumps({'answer': 'YES', 'evidence_refs': [1], 'short_reason': 'Authored fixture'}),
                    'usage': {'input_tokens': 10, 'output_tokens': 10}}
        judge = Judge(run.trace, JudgeConfig(model='test', model_version='fixture'), backend)
        namespace = runpy.run_path(str(ROOT / 'examples/clarification.py'))
        namespace['evaluate'](run, recorder, judge)
        self.assertEqual(len(requests), 1)
        self.assertEqual(judge.receipts[0]['decision'], Verdict.YES.value)
        self.assertEqual(recorder.records[-1]['op'], 'violation')
        self.assertEqual(recorder.records[-1]['witness'], ['a' * 16])
        authentic = evidence(run.events[0], 'output')
        forged = Evidence(authentic.ref, authentic.field, 'fabricated')
        with self.assertRaises(PacketError):
            judge.decide(proposition='Does this request clarification?', evidence=[forged])
        self.assertEqual(len(requests), 1)
        with self.assertRaises(ValueError):
            recorder.violation(witness=['absent'], reason='invalid witness')

    def test_native_profile_preserves_exact_nanoseconds_and_unknown_transport(self):
        start = 1791414212123456789
        values = {'id': 'a' * 16, 'trace_id': 'b' * 32, 'service_name': 'authored',
                  'chq_tsns': start, 'end_tsns': start + 250000001,
                  'openinference_span_kind': 'TOOL', 'name': 'submit', 'output_value': 'pending'}
        columns = [{'name': name, 'dtype': 1 if type(value) is int else 0,
                    'valueHex': (struct.pack('<q', value) if type(value) is int else value.encode()).hex()}
                   for name, value in values.items()]
        normalized = adapt({'traceId': 'b' * 32, 'rows': [{'columns': columns}],
                            'receipt': {'complete': True}}, 'authored')
        run = TraceView(normalized, profile=NativeProfile())
        self.assertEqual(run.events[0].attrs['chq_tsns'], start)
        self.assertEqual(run.events[0].attrs['end_tsns'] - start, 250000001)
        self.assertFalse(run.trace.attrs['conversation_complete'])
        self.assertEqual(run.calls()[0].outcome.value, 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
