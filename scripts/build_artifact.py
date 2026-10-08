#!/usr/bin/env python3
# Copyright (c) 2025-2026 CardinalHQ, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Generate authoring files and the immutable artifact from canonical SDK code."""
import argparse
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from behavior_sdk.authoring import authoring_artifact, artifact_digest, canonical


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Reject stale generated authoring files')
    parser.add_argument('--output', type=Path, help='Write digest-addressed artifact to this directory')
    args = parser.parse_args()
    artifact = authoring_artifact()
    for name, content in artifact['files'].items():
        if name.startswith('behavior_sdk/'):
            continue
        path = ROOT / name
        if args.check:
            if not path.is_file() or path.read_text() != content:
                raise SystemExit(f'Generated file differs from canonical SDK: {name}')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    body = canonical(artifact)
    digest = artifact_digest()
    assert hashlib.sha256(body).hexdigest() == digest
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        name = f'behavior-sdk-{digest}.json'
        (args.output / name).write_bytes(body)
        (args.output / 'SHA256SUMS').write_text(f'{digest}  {name}\n')
    print(digest)


if __name__ == '__main__':
    main()
