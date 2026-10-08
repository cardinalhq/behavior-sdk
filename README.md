# Cardinal Behavior SDK

Canonical Python source for authoring Behavioral Programs against the same public
ABI used by deployed Cardinal runtimes. This repository owns `TraceView`,
`Outcome`, `Recorder`, `Evidence`, bounded JEV decisions, public helpers, telemetry
profiles, and CompilePlan review metadata. It has no third-party runtime dependencies.

The execution host supplies the sandbox, JEV provider and credentials, persistence,
and workers. Those private components are maintained by LakeRunner. The SDK does
not include a local semantic evaluator, compiler service, or deployment launcher.

## Authoring

Read [AUTHORING.md](AUTHORING.md), the real [public Python exports](behavior_sdk/__init__.py),
and the profile source. [The example](examples/clarification.py) is an executable
`evaluate(run, recorder, judge)` function for the host runtime. Its CompilePlan
schema is [generated from the public dataclasses](schemas/compile-plan.json).

Use the installed Cardinal plugin to fetch the deployed artifact, compile your
program, test real teaching trace IDs, inspect witnesses and JEV receipts, and
explicitly accept the immutable DiagnosticVersion before population execution.
The runtime checks SDK, profile, and host identities; a newer SDK checkout does
not replace the version your deployed runtime requires.

## Artifact and downstream pin

`behavior_sdk/` is the only maintained implementation. The authoring artifact
contains those exact Python bytes plus generated documentation, schema, and an
example. Its canonical JSON SHA-256 is `sdk_runtime_sha256`. Generated files must
not be edited independently:

```sh
python3 scripts/build_artifact.py
python3 scripts/build_artifact.py --check --output dist
python3 -m unittest discover -s tests -v
```

Tagged releases attach `behavior-sdk-<sha256>.json` and `SHA256SUMS`. Consumers pin
an immutable source commit and artifact digest. LakeRunner consumes that exact immutable release artifact, verifies its digest,
and materializes its files only as an ignored build/development dependency. It
keeps no separately maintained or tracked SDK source copy. Changes originate here;
compiler and execution workers use the same pinned artifact.

For a Python development environment, install an immutable commit with
`pip install "git+https://github.com/cardinalhq/behavior-sdk.git@<full-commit-sha>"`.
Installing the package does not provision a runtime or JEV credentials.

## License

Copyright CardinalHQ. Licensed under [Apache-2.0](LICENSE).
