# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Mechanical span projection for ordinary LakeRunner ReadTrace populations.

Each retained native span is one event. No application-specific inferred
messages, tool completions, or semantic labels are manufactured.
"""
import hashlib
from pathlib import Path
import re
import struct
import math
from .openinference_read_trace import _cell
from .otlp_behavior_trace import BehaviorTraceCoverageGap
from .profiles import Profile
from .trace import Call, Outcome, ResultState, ResultView


def adapter_digest():
    return hashlib.sha256(Path(__file__).read_bytes() + Path(__file__).with_name('openinference_read_trace.py').read_bytes()).hexdigest()


class NativeProfile(Profile):
    def __init__(self):
        super().__init__('lakerunner-native-spans', 'v1', adapter_digest(), 'span-ns')

    def calls(self, run):
        for event in run.events:
            if event.kind != 'tool':
                continue
            status = event.attrs.get('status_code')
            outcome = Outcome.FAILURE if status == 'STATUS_CODE_ERROR' else Outcome.SUCCESS if status == 'STATUS_CODE_OK' else Outcome.UNKNOWN
            result = ResultView(ResultState.PRESENT, (event.id,), event.output) if event.output is not None else ResultView(ResultState.MISSING)
            yield Call(event.id, event.id, event.name, {'raw': event.input}, result,
                       outcome, (str(status),) if status else (), (event.id,), (event.id,), event.seq)


def adapt(read, service_name):
    trace_id = read.get('traceId')
    if not isinstance(trace_id, str) or not re.fullmatch('[a-f0-9]{32}', trace_id):
        raise BehaviorTraceCoverageGap('invalid native trace identity')
    if not isinstance(read.get('rows'), list) or not read['rows'] or not read.get('receipt'):
        raise BehaviorTraceCoverageGap('complete native trace receipt is required')
    events = []
    seen = set()
    for row in read['rows']:
        sid = _cell(row, 'id')
        if _cell(row, 'trace_id') != trace_id or not sid or sid in seen:
            raise BehaviorTraceCoverageGap('invalid or duplicate native span identity')
        seen.add(sid)
        attrs = {}
        for column in row.get('columns', []):
            name = column.get('name')
            if not name or name in attrs:
                raise BehaviorTraceCoverageGap('invalid or duplicate native column', sid)
            dtype, value = column.get('dtype'), column.get('valueHex')
            if value is None:
                attrs[name] = None
                continue
            try:
                raw = bytes.fromhex(value)
                if dtype == 0:
                    attrs[name] = raw.decode('utf-8')
                elif dtype in (1, 2, 3) and len(raw) == 8:
                    attrs[name] = struct.unpack({1:'<q', 2:'<Q', 3:'<d'}[dtype], raw)[0]
                    if dtype == 3 and not math.isfinite(attrs[name]):
                        raise ValueError('nonfinite scalar')
                elif dtype in (4, 5):
                    attrs[name] = {'dtype': dtype, 'hex': raw.hex()}
                else:
                    raise ValueError('unsupported native scalar')
            except (TypeError, ValueError, UnicodeError):
                raise BehaviorTraceCoverageGap('native scalar cannot be projected losslessly', sid) from None
        start, end = attrs.get('chq_tsns'), attrs.get('end_tsns')
        if type(start) is not int or type(end) is not int or end < start:
            raise BehaviorTraceCoverageGap('native span time unavailable', sid)
        kind = {'TOOL':'tool', 'LLM':'llm'}.get(attrs.get('openinference_span_kind'), 'other')
        events.append({'id':sid, 'parent_id':attrs.get('parent_span_id'), 'kind':kind,
                       'name':attrs.get('tool_name') or attrs.get('name') or attrs.get('span_name') or '',
                       'start':start / 1e9, 'end':end / 1e9, 'attrs':attrs,
                       'input':attrs.get('input_value'), 'output':attrs.get('output_value'),
                       'error':attrs.get('status_message') if attrs.get('status_code') == 'STATUS_CODE_ERROR' else None})
    events.sort(key=lambda e: (e['attrs']['chq_tsns'], e['id']))
    for seq, event in enumerate(events):
        event['seq'] = seq
    return {'trace_id':trace_id, 'agent':service_name, 'attrs':{'projection':'native-spans-v1', 'coverage_scope':'selected-window-objects', 'conversation_complete':False}, 'events':events}
