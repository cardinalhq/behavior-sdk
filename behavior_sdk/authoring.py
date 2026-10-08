# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Digest-addressed public source artifact, built from the installed worker SDK."""
from dataclasses import fields, MISSING
import hashlib
import inspect
import json
from pathlib import Path

from . import __version__
from .contract import CompilePlan, ContractClause, JudgeSite, CLAUSE_KINDS
from .sdk_contract import SDK_DOCUMENTATION, SDK_EXAMPLE_SOURCE


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def compile_plan_schema():
    """Derive the authoring schema from the same dataclasses the compiler uses."""
    def schema(cls):
        properties = {}
        required = []
        for field in fields(cls):
            if field.name == 'clauses':
                item = {'type': 'array', 'items': schema(ContractClause), 'minItems': 1}
            elif field.name == 'judge_sites':
                item = {'type': 'array', 'items': schema(JudgeSite)}
            elif field.type is int:
                item = {'type': 'integer'}
            else:
                item = {'type': 'string'}
            if field.name == 'kind': item['enum'] = sorted(CLAUSE_KINDS)
            if field.name == 'selection_kind': item['const'] = 'mechanical'
            if field.name == 'schema_version': item['const'] = 1
            if field.default is MISSING: required.append(field.name)
            else: item['default'] = field.default
            properties[field.name] = item
        return {'description': inspect.cleandoc(cls.__doc__ or ''), 'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', **schema(CompilePlan)}


def authoring_artifact():
    """Return real UTF-8 source, profiles, generated schema and runnable example."""
    root = Path(__file__).parent
    source = {'behavior_sdk/' + path.name: path.read_text() for path in sorted(root.glob('*.py'))}
    source['LICENSE'] = (root / 'LICENSE').read_text()
    source['behavior_sdk/LICENSE'] = source['LICENSE']
    source['schemas/compile-plan.json'] = canonical(compile_plan_schema()).decode()
    source['examples/clarification.py'] = SDK_EXAMPLE_SOURCE
    source['AUTHORING.md'] = SDK_DOCUMENTATION
    return {'format': 'behavior-sdk-authoring-v1', 'version': __version__, 'files': source}


def artifact_digest():
    return hashlib.sha256(canonical(authoring_artifact())).hexdigest()
