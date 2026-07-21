# Isolation status

This document records the trust boundaries of ver3. It describes what the
current code enforces; it is not a claim that every workflow process runs in a
separate physical machine.

## Enforced now

| boundary | current enforcement |
| --- | --- |
| Submitted program runtime | Production uses LightCPVerifier/go-judge. go-judge starts with `-no-fallback`; the supervised container fixes both the Node client and readiness probe to that same local instance at `127.0.0.1:5050`. The service is not ready unless its live `/version` probe succeeds, and loss of go-judge makes the service unhealthy/exits the container. CPU, wall, address-space/memory, process, stack, and output limits are supplied to go-judge per run. |
| Submitted program compilation | Each generator, validator, std, brute, checker, and wrong route is compiled as its own single translation unit. No problem-owned C/C++ source or header is copied into another role's compiler context. The only extra contest header is image-owned `/lib/testlib`. |
| Runtime files | A dataset case receives its executable, stdin, declared argv, and only explicitly supplied per-case files. The problem directory and private wrong-solution/audit trees are not copied into the run sandbox. |
| Limit integrity | Statement TL/ML are range-checked, hash-bound, forwarded unchanged, and checked against the service's effective-limit echo. Missing declarations, clamping, drift, or missing evidence fail closed. |
| Service exposure | The provided Docker/Compose launchers publish the HTTP service only on `127.0.0.1`. Build/image/client hashes detect accidental version drift. |
| Release boundary | Resource, route, accepted-alternative, coverage, and execution evidence stays under private audit artifacts. No new file or directory is added to the generated `package/` contract. The regression gate rejects symlinks, hidden/development/temp files, private-path components, common credential forms, operator-local paths, and private workflow names in metadata-bearing package text; it records findings without echoing matched secret values. |

The verifier container itself is privileged because go-judge needs host kernel
features to create its sandbox. Therefore the Docker host and verifier service
remain trusted infrastructure. Build/image hashes are version binding, not
remote attestation against a malicious host.

## Not physically isolated yet

| boundary | current status | consequence |
| --- | --- | --- |
| Blind LLM lanes | They receive separate staged public workspaces, but the runner records `filesystem_read_isolation: false`. | This is process/workspace separation, not a proof that an agent cannot read other host paths. Blind evidence must continue to label this risk honestly. |
| Non-blind LLM stages | Stage receipts isolate provenance and archive stale outputs. Build ownership includes samples, generators, tests, wrong/accepted solutions, the optional checker, and adversarial-round plans/receipts. The trusted readiness handoff reruns canonical completion rather than accepting a self-reported receipt. Agents still work below one operator-controlled problem root. | A hostile agent process is not contained like submitted code. |
| Verifier control plane | Localhost binding limits network exposure, but the HTTP API has no per-run authentication/tenant boundary. | Do not expose it to an untrusted network or share it between mutually hostile tenants. |
| Kernel/host boundary | go-judge sandboxes share the verifier host kernel, and the service container runs privileged. | This is suitable only when the operator trusts the Docker host and accepts shared-kernel sandbox risk; it is not VM-grade physical isolation. |

## Safe next isolation stages

The next steps should be introduced separately because they change deployment
and agent orchestration, even though they need not change `package/`:

1. Run each blind lane in a disposable container or micro-VM with only a
   read-only public-material mount, a private writable output mount, no problem
   root mount, and restricted network. Then require the runner to attest
   `filesystem_read_isolation: true` from that launcher rather than changing the
   receipt label optimistically.
2. Run non-blind stage agents in role-specific workspaces/mounts and merge only
   declared outputs through the stage runner.
3. Put LightCPVerifier/go-judge on a dedicated worker VM, add authenticated
   per-run capabilities and request namespaces, and keep the API unreachable
   from untrusted contestant processes.
4. Add live fault-injection tests: unavailable namespace/cgroup support,
   go-judge death during a batch, TL/ML echo drift, attempted cross-role include,
   forbidden path reads, and service restart/cache eviction.

Until those stages are implemented and tested, receipts and documentation must
not describe blind agents, stage agents, the Docker host, or a remote verifier
as physically isolated.

The package scan is a boundary and common-leak detector, not a general secret
scanner or archive forensics tool. Non-UTF-8 files are listed as content-scan
skips, and nested archive metadata is not unpacked. Ordinary consumed ICPC
source/input/copy-in files remain subject to the separate UTF-8 transport gate;
operators should review intentionally shipped binary/public attachments.
