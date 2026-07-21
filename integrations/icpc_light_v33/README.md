# ICPC Light v3.3 bridge

This integration connects the vendored ICPC Light v3.3 skill bundle to
UOJ-Bench's typed solver API without changing the benchmark dataset, prompts,
or UOJ evaluator.

- Solver name: `icpc_light_v33_bridge`
- Model contract: exact `gpt-5.6-sol`, reasoning effort `ultra`
- Supported tasks: one-shot Generation, Hacking, and Fault Exposure
- Unsupported: Repair, feedback rounds, Fault Coverage

The benchmark runner launches the configured bridge executable over one JSON object
on stdin/stdout. The bridge creates one private workspace, copies the frozen
skills, runs one configured task agent, verifies that the public surface and
skills were not modified, and exports exactly one typed candidate.

The vendored bundle is pinned by [`SKILL_BUNDLE.lock.json`](SKILL_BUNDLE.lock.json).
The bridge configuration must repeat its exact `tree_sha256`; a mismatch fails
before the task agent starts.

The lock preserves the original source manifest and `RELEASE.json` hashes. The
published copy redacts one host-specific Docker socket path, records that
redaction explicitly, and pins its regenerated vendored manifest separately.
[`MANIFEST.sha256`](MANIFEST.sha256) pins every other file in this integration;
verify it from this directory with `sha256sum -c MANIFEST.sha256` on Linux
(`shasum -a 256 -c MANIFEST.sha256` on macOS).

Documentation:

- [Integration and smoke guide](../../docs/ICPC_LIGHT_V33_BRIDGE.zh-CN.md)
- [Zero-mount server handoff](../../docs/ICPC_LIGHT_V33_ZERO_MOUNT_HANDOFF.zh-CN.md)

The deterministic smoke uses injected test workers. It calls the real v3.3
sweep/review scripts, UOJ's native Hacking rollout runner, and a TestCase-Eval
Task 2 Fault Exposure job, but it calls neither a model nor UOJ and is not a
benchmark score.
