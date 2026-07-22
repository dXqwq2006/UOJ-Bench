"""Zero-mount Docker scheduler for the ICPC Light reference agent."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from ipaddress import IPv4Address, IPv4Network
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from typing import Any, Sequence

from .runtime import _sha256_file, _tree_sha256, _write_json


MODEL = "gpt-5.6-sol"
REASONING_EFFORT = "xhigh"
PLACEHOLDER_TOKEN = "skill-eval-placeholder-token"
UPSTREAM_BASE_URL = "https://maas.tatucloud.com/deployer/coding_tatu/v1"
RELAY_URL = "http://credential-relay:8080/v1"
DEFAULT_RELAY_CONTAINER = "icpc-light-v33-relay"
LIGHTCPVERIFIER_URL = "http://lightcpverifier:8081"
LIGHTCPVERIFIER_BUILD_ID = (
    "sha256:134bd322502f762dee2c2da5abf0b9d6c64e3b3d4055fde8ff4eb250c46db603"
)
CPIDEAS_PLUS_COMMIT = "778c619799affe3c52ecd23e2984ce7d9545fed5"
BASE_AGENT_IMAGE_ID = "sha256:d922d6dfe1e11d5a7570b1108abcf18bfe2fec59703b54440363ae8eac002169"
XHIGH_WRAPPER_SHA256 = "7d517ae8652a3dd176dd6a597f37678f8da2c0faeddbec0fea113c1f28bbaf65"
MAX_DOCKER_OUTPUT_BYTES = 16 * 1024 * 1024
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
OBJECT_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
SAFE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_STOP_REQUESTED = False


class SchedulerError(RuntimeError):
    """The physical production boundary could not be established."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _request_stop(_signum: int, _frame: Any) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


def _docker(
    arguments: Sequence[str],
    *,
    timeout: float = 60,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["docker", *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=timeout,
    )
    if len(completed.stdout.encode("utf-8", errors="replace")) > MAX_DOCKER_OUTPUT_BYTES:
        raise SchedulerError("Docker stdout exceeded the scheduler limit")
    if len(completed.stderr.encode("utf-8", errors="replace")) > MAX_DOCKER_OUTPUT_BYTES:
        raise SchedulerError("Docker stderr exceeded the scheduler limit")
    if check and completed.returncode != 0:
        diagnostic = (completed.stderr or completed.stdout)[-2000:].strip()
        raise SchedulerError(
            f"Docker command failed with exit {completed.returncode}"
            + (f": {diagnostic}" if diagnostic else "")
        )
    return completed


def _json_object(arguments: Sequence[str], label: str) -> dict[str, Any]:
    completed = _docker(arguments)
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SchedulerError(f"{label} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise SchedulerError(f"{label} did not return a JSON object")
    return value


def _image(image_id: str) -> dict[str, Any]:
    if IMAGE_ID_RE.fullmatch(image_id) is None:
        raise SchedulerError("agent image must be an immutable sha256 ID")
    completed = _docker(["image", "inspect", image_id])
    try:
        values = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SchedulerError("agent image inspect returned invalid JSON") from exc
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise SchedulerError("agent image inspect returned an ambiguous identity")
    value = values[0]
    config = value.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if (
        value.get("Id") != image_id
        or not isinstance(config, dict)
        or config.get("Volumes") not in (None, {})
        or not isinstance(labels, dict)
        or labels.get("org.cpideas.skill-eval.component") != "agent"
        or labels.get("org.cpideas.skill-eval.contract") != "zero-mount-v3"
        or labels.get("org.cpideas.skill-eval.reasoning-effort") != REASONING_EFFORT
        or labels.get("org.cpideas.skill-eval.base-agent-image-id") != BASE_AGENT_IMAGE_ID
        or labels.get("org.cpideas.skill-eval.codex-wrapper-sha256") != XHIGH_WRAPPER_SHA256
        or labels.get("org.cpideas.skill-eval.cpideas-plus-commit") != CPIDEAS_PLUS_COMMIT
        or labels.get("org.cpideas.skill-eval.skill-bundle-version") != "3.3.0"
    ):
        raise SchedulerError("agent image identity or xhigh contract is invalid")
    return value


def _relay(container: str, image_id: str) -> dict[str, Any]:
    if SAFE_NAME_RE.fullmatch(container) is None:
        raise SchedulerError("credential relay name is unsafe")
    if IMAGE_ID_RE.fullmatch(image_id) is None:
        raise SchedulerError("credential relay image must be an immutable sha256 ID")
    value = _json_object(["inspect", container, "--format", "{{json .}}"], "relay inspect")
    config = value.get("Config")
    host = value.get("HostConfig")
    state = value.get("State")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if (
        value.get("Image") != image_id
        or value.get("Mounts") != []
        or not isinstance(config, dict)
        or config.get("Volumes") not in (None, {})
        or not isinstance(labels, dict)
        or labels.get("org.cpideas.skill-eval.component") != "credential-relay"
        or labels.get("org.cpideas.skill-eval.contract") != "zero-mount-stdin-v1"
        or not isinstance(host, dict)
        or host.get("Mounts") not in (None, [])
        or host.get("ReadonlyRootfs") is not True
        or not isinstance(state, dict)
        or state.get("Running") is not True
    ):
        raise SchedulerError("dedicated credential relay attestation failed")
    return value


def _lightcpverifier(container: str, image_id: str) -> dict[str, Any]:
    if SAFE_NAME_RE.fullmatch(container) is None:
        raise SchedulerError("LightCPVerifier container name is unsafe")
    if IMAGE_ID_RE.fullmatch(image_id) is None:
        raise SchedulerError("LightCPVerifier image must be an immutable sha256 ID")
    value = _json_object(
        ["inspect", container, "--format", "{{json .}}"],
        "LightCPVerifier inspect",
    )
    config = value.get("Config")
    host = value.get("HostConfig")
    state = value.get("State")
    labels = config.get("Labels") if isinstance(config, dict) else None
    environment = {}
    if isinstance(config, dict) and isinstance(config.get("Env"), list):
        environment = dict(
            item.split("=", 1)
            for item in config["Env"]
            if isinstance(item, str) and "=" in item
        )
    if (
        value.get("Image") != image_id
        or value.get("Mounts") != []
        or not isinstance(config, dict)
        or config.get("Volumes") not in (None, {})
        or not isinstance(labels, dict)
        or labels.get("org.cpideas.lightcpverifier.contract") != "zero-mount-v3"
        or labels.get("org.cpideas.lightcpverifier.build-id")
        != LIGHTCPVERIFIER_BUILD_ID
        or environment.get("LIGHTCPVERIFIER_BUILD_ID") != LIGHTCPVERIFIER_BUILD_ID
        or environment.get("LIGHTCPVERIFIER_IMAGE_ID") != image_id
        or not isinstance(host, dict)
        or host.get("Mounts") not in (None, [])
        or not isinstance(state, dict)
        or state.get("Running") is not True
    ):
        raise SchedulerError("dedicated LightCPVerifier attestation failed")
    return value


def _container_create_argv(
    *,
    name: str,
    network_id: str,
    image_id: str,
    user: str,
    task: str,
) -> list[str]:
    result = [
        "create",
        "--name",
        name,
        "--label",
        "icpc-light-v33.zero-mount=true",
        "--restart",
        "no",
        "--network",
        network_id,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "512",
        "--memory",
        "4g",
        "--cpus",
        "2",
        "--user",
        user,
        "--ulimit",
        "fsize=536870912:536870912",
        "--workdir",
        "/work/cell",
        "--env",
        "PYTHONPATH=/opt:/opt/cpideas",
        "--env",
        "HOME=/work/cell/home",
        "--env",
        f"OPENAI_BASE_URL={RELAY_URL}",
        "--env",
        f"OPENAI_API_KEY={PLACEHOLDER_TOKEN}",
        "--env",
        f"CODEX_API_KEY={PLACEHOLDER_TOKEN}",
        "--env",
        f"SKILL_EVAL_UPSTREAM_BASE_URL={UPSTREAM_BASE_URL}",
        "--env",
        f"SKILL_EVAL_MODEL={MODEL}",
        "--env",
        f"SKILL_EVAL_REASONING_EFFORT={REASONING_EFFORT}",
        "--entrypoint",
        "/usr/local/bin/python3",
        image_id,
        "-m",
        "uoj_skill_bridge.codex_agent",
        "--task",
        task,
        "--workspace",
        "/work/cell",
    ]
    if task == "test_package":
        entrypoint = result.index("--entrypoint")
        result[entrypoint:entrypoint] = [
            "--env",
            f"ICPC_LIGHT_LIGHTCPVERIFIER_URL={LIGHTCPVERIFIER_URL}",
        ]
    return result


def _job_subnet(suffix: str) -> str:
    """Choose one small internal subnet outside Docker's exhausted defaults."""

    if re.fullmatch(r"[0-9a-f]{20}", suffix) is None:
        raise SchedulerError("job suffix is malformed")
    pool = IPv4Network("10.240.0.0/12")
    slots = pool.num_addresses // 8
    start = int(pool.network_address) + (int(suffix, 16) % slots) * 8
    return str(IPv4Network((IPv4Address(start), 29)))


def _attest_container(
    reference: str,
    *,
    image_id: str,
    name: str,
    allowed_states: set[str],
) -> dict[str, Any]:
    value = _json_object(["inspect", reference, "--format", "{{json .}}"], "agent inspect")
    state = value.get("State")
    config = value.get("Config")
    host = value.get("HostConfig")
    if (
        value.get("Id") != reference
        or value.get("Name") != f"/{name}"
        or value.get("Image") != image_id
        or value.get("Mounts") != []
        or not isinstance(config, dict)
        or config.get("Volumes") not in (None, {})
        or not isinstance(host, dict)
        or host.get("Mounts") not in (None, [])
        or host.get("Privileged") is not False
        or not isinstance(state, dict)
        or state.get("Status") not in allowed_states
    ):
        raise SchedulerError("agent container zero-mount attestation failed")
    return value


def _copy_into(
    container_id: str,
    source: Path,
    destination: str,
    *,
    contents_only: bool = False,
) -> None:
    source_argument = str(source) + "/." if contents_only else str(source)
    completed = _docker(
        ["cp", "--archive", source_argument, f"{container_id}:{destination}"],
        timeout=120,
    )
    if completed.returncode != 0:
        raise SchedulerError("Docker input copy failed")


def _run_container(container_id: str, timeout: float) -> tuple[int, bytes, bytes]:
    started = time.monotonic()
    process = subprocess.Popen(
        ["docker", "start", "--attach", container_id],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    while process.poll() is None:
        if _STOP_REQUESTED or time.monotonic() - started >= timeout:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            _docker(["kill", container_id], check=False)
            raise SchedulerError("agent container was stopped or exceeded its deadline")
        time.sleep(0.1)
    stdout, stderr = process.communicate()
    if len(stdout) > MAX_DOCKER_OUTPUT_BYTES or len(stderr) > MAX_DOCKER_OUTPUT_BYTES:
        raise SchedulerError("attached agent output exceeded the scheduler limit")
    return int(process.returncode or 0), stdout, stderr


def _regular_tree(path: Path, label: str, *, allow_empty: bool = False) -> str:
    try:
        digest, _, _ = _tree_sha256(path, allow_empty=allow_empty)
    except Exception as exc:
        raise SchedulerError(f"{label} is not a safe regular tree: {exc}") from exc
    return digest


def _attest_integration(expected_manifest_sha256: str) -> tuple[Path, str]:
    if re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256) is None:
        raise SchedulerError("integration manifest SHA-256 is malformed")
    root = Path(__file__).resolve().parents[2]
    manifest = root / "MANIFEST.sha256"
    if manifest.is_symlink() or not manifest.is_file():
        raise SchedulerError("integration manifest is missing or unsafe")
    actual_manifest_sha256 = _sha256_file(manifest)
    if actual_manifest_sha256 != expected_manifest_sha256:
        raise SchedulerError("integration manifest identity changed")
    entries: dict[str, str] = {}
    for line in manifest.read_text(encoding="ascii").splitlines():
        try:
            digest, raw_relative = line.split("  ", 1)
        except ValueError as exc:
            raise SchedulerError("integration manifest line is malformed") from exc
        relative = PurePosixPath(raw_relative[2:])
        if (
            re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or not raw_relative.startswith("./")
            or relative.is_absolute()
            or ".." in relative.parts
            or raw_relative in entries
        ):
            raise SchedulerError("integration manifest entry is unsafe")
        path = root.joinpath(*relative.parts)
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or _sha256_file(path) != digest
        ):
            raise SchedulerError(f"integration manifest mismatch: {raw_relative}")
        entries[raw_relative] = digest
    actual = {
        "./" + path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path != manifest
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    }
    if set(entries) != actual:
        raise SchedulerError("integration manifest file set changed")
    return root, actual_manifest_sha256


def _ephemeral_directory(prefix: str) -> Path:
    # ntfs3 can strand recursive deletion; use the host temporary filesystem.
    return Path(tempfile.mkdtemp(prefix=prefix))


def _stage_agent_package(integration_root: Path, workspace: Path) -> Path:
    source = integration_root / "src" / "uoj_skill_bridge"
    staging_parent = _ephemeral_directory(".icpc-agent-package-")
    try:
        package = staging_parent / "uoj_skill_bridge"
        package.mkdir(mode=0o700)
        for path in sorted(source.glob("*.py")):
            metadata = path.lstat()
            if (
                path.is_symlink()
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
            ):
                raise SchedulerError("agent package source is unsafe")
            shutil.copyfile(path, package / path.name, follow_symlinks=False)
        if not (package / "codex_agent.py").is_file() or not (
            package / "runtime.py"
        ).is_file():
            raise SchedulerError("agent package is incomplete")
        return package
    except BaseException:
        shutil.rmtree(staging_parent, ignore_errors=True)
        raise


def _install_returned_trees(
    returned: Path, workspace: Path, names: Sequence[str]
) -> dict[str, str]:
    staged: list[tuple[Path, Path, str, str]] = []
    installed: dict[str, str] = {}
    try:
        for name in names:
            source = returned / name
            digest = _regular_tree(source, f"returned {name}")
            destination = workspace / name
            if os.path.lexists(destination):
                raise SchedulerError(f"private result destination already exists: {name}")
            temporary = workspace / f".{name}-returned-{secrets.token_hex(6)}"
            shutil.copytree(source, temporary, copy_function=shutil.copy2)
            if _regular_tree(temporary, f"staged {name}") != digest:
                raise SchedulerError(f"private result copy changed: {name}")
            staged.append((temporary, destination, name, digest))

        for temporary, destination, name, digest in staged:
            os.replace(temporary, destination)
            installed[f"{name}_sha256"] = digest
        return installed
    finally:
        for temporary, _, _, _ in staged:
            shutil.rmtree(temporary, ignore_errors=True)


def _export_result(
    container_id: str, workspace: Path, *, task: str
) -> dict[str, str]:
    quarantine = _ephemeral_directory(".icpc-returned-")
    returned = quarantine / "cell"
    try:
        _docker(["cp", f"{container_id}:/work/cell", str(returned)], timeout=120)
        if _regular_tree(returned / "surface", "returned public surface") != _regular_tree(
            workspace / "surface", "host public surface"
        ):
            raise SchedulerError("agent changed the public surface")
        if _regular_tree(returned / "skills", "returned skill bundle") != _regular_tree(
            workspace / "skills", "host skill bundle"
        ):
            raise SchedulerError("agent changed the skill bundle")
        output_sha = _regular_tree(
            returned / "output", "returned output", allow_empty=True
        )
        result = returned / "control" / "agent-result.json"
        metadata = result.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise SchedulerError("returned agent result is unsafe")

        private_hashes = (
            _install_returned_trees(returned, workspace, ("audit", "package"))
            if task == "test_package"
            else {}
        )

        host_output = workspace / "output"
        staged_output = workspace / f".output-returned-{secrets.token_hex(6)}"
        shutil.copytree(returned / "output", staged_output, copy_function=shutil.copy2)
        shutil.rmtree(host_output)
        os.replace(staged_output, host_output)
        for name in ("agent-result.json", "codex-events.jsonl", "codex-stderr.log"):
            source = returned / "control" / name
            if not source.exists():
                continue
            metadata = source.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise SchedulerError(f"returned control file is unsafe: {name}")
            destination = workspace / "control" / name
            temporary = destination.with_name(f".{name}.{secrets.token_hex(6)}.tmp")
            shutil.copyfile(source, temporary, follow_symlinks=False)
            os.replace(temporary, destination)
        return {
            "output_sha256": output_sha,
            "agent_result_sha256": _sha256_file(workspace / "control" / "agent-result.json"),
            **private_hashes,
        }
    finally:
        shutil.rmtree(quarantine, ignore_errors=True)


def run(
    *,
    task: str,
    workspace: Path,
    agent_image_id: str,
    relay_container: str,
    relay_image_id: str,
    lightcpverifier_container: str | None,
    lightcpverifier_image_id: str | None,
    integration_manifest_sha256: str,
    timeout: float,
) -> dict[str, Any]:
    workspace = workspace.resolve(strict=True)
    metadata = workspace.lstat()
    if (
        workspace.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise SchedulerError("workspace must be a private bridge-owned directory")
    integration_root, manifest_sha256 = _attest_integration(
        integration_manifest_sha256
    )
    _image(agent_image_id)
    relay = _relay(relay_container, relay_image_id)
    relay_id = relay.get("Id")
    if not isinstance(relay_id, str) or OBJECT_ID_RE.fullmatch(relay_id) is None:
        raise SchedulerError("credential relay has a non-canonical container ID")

    lightcpverifier_id = ""
    if task == "test_package":
        if lightcpverifier_container is None or lightcpverifier_image_id is None:
            raise SchedulerError(
                "test_package requires an attested LightCPVerifier container"
            )
        service = _lightcpverifier(
            lightcpverifier_container, lightcpverifier_image_id
        )
        candidate_id = service.get("Id")
        if (
            not isinstance(candidate_id, str)
            or OBJECT_ID_RE.fullmatch(candidate_id) is None
        ):
            raise SchedulerError("LightCPVerifier has a non-canonical container ID")
        lightcpverifier_id = candidate_id
    elif (
        lightcpverifier_container is not None
        or lightcpverifier_image_id is not None
    ):
        raise SchedulerError(
            "LightCPVerifier may only be attached to test_package jobs"
        )

    suffix = secrets.token_hex(10)
    network_name = f"icpc-light-v33-net-{suffix}"
    network_subnet = _job_subnet(suffix)
    container_name = f"icpc-light-v33-agent-{suffix}"
    network_id = ""
    container_id = ""
    connected = False
    lightcpverifier_connected = False
    started_at = _now()
    package = _stage_agent_package(integration_root, workspace)
    try:
        created_network = _docker(
            [
                "network",
                "create",
                "--internal",
                "--driver",
                "bridge",
                "--subnet",
                network_subnet,
                "--label",
                "icpc-light-v33.isolated-job=true",
                network_name,
            ]
        )
        network_id = created_network.stdout.strip()
        if OBJECT_ID_RE.fullmatch(network_id) is None:
            raise SchedulerError("isolated network returned a non-canonical ID")
        _docker(
            [
                "network",
                "connect",
                "--alias",
                "credential-relay",
                network_id,
                relay_id,
            ]
        )
        connected = True
        if lightcpverifier_id:
            _docker(
                [
                    "network",
                    "connect",
                    "--alias",
                    "lightcpverifier",
                    network_id,
                    lightcpverifier_id,
                ]
            )
            lightcpverifier_connected = True
        user = f"{metadata.st_uid}:{metadata.st_gid}"
        created = _docker(
            _container_create_argv(
                name=container_name,
                network_id=network_id,
                image_id=agent_image_id,
                user=user,
                task=task,
            )
        )
        container_id = created.stdout.strip()
        if OBJECT_ID_RE.fullmatch(container_id) is None:
            raise SchedulerError("agent create returned a non-canonical ID")
        _attest_container(
            container_id,
            image_id=agent_image_id,
            name=container_name,
            allowed_states={"created"},
        )
        _copy_into(container_id, workspace, "/work/cell", contents_only=True)
        _copy_into(container_id, package, "/opt/uoj_skill_bridge")
        _attest_container(
            container_id,
            image_id=agent_image_id,
            name=container_name,
            allowed_states={"created"},
        )
        returncode, attached_stdout, attached_stderr = _run_container(
            container_id, timeout
        )
        terminal = _attest_container(
            container_id,
            image_id=agent_image_id,
            name=container_name,
            allowed_states={"exited"},
        )
        state = terminal["State"]
        if returncode != 0 or state.get("ExitCode") != 0:
            diagnostic = (attached_stderr or attached_stdout)[-2000:].decode(
                "utf-8", errors="replace"
            )
            raise SchedulerError(
                "agent container exited nonzero"
                + (f": {diagnostic}" if diagnostic else "")
            )
        exported = _export_result(container_id, workspace, task=task)
        return {
            "schema_version": 1,
            "status": "completed",
            "started_at": started_at,
            "finished_at": _now(),
            "task": task,
            "model": MODEL,
            "reasoning_effort": REASONING_EFFORT,
            "agent_image_id": agent_image_id,
            "relay_image_id": relay_image_id,
            "container_id": container_id,
            "network_id": network_id,
            "network_subnet": network_subnet,
            "mounts": [],
            "workspace_transfer": "docker-cp",
            "integration_manifest_sha256": manifest_sha256,
            **(
                {
                    "lightcpverifier_container_id": lightcpverifier_id,
                    "lightcpverifier_image_id": lightcpverifier_image_id,
                }
                if lightcpverifier_id
                else {}
            ),
            **exported,
        }
    finally:
        if container_id:
            _docker(["container", "rm", "--force", container_id], check=False)
        if connected and network_id:
            _docker(["network", "disconnect", "--force", network_id, relay_id], check=False)
        if lightcpverifier_connected and network_id:
            _docker(
                [
                    "network",
                    "disconnect",
                    "--force",
                    network_id,
                    lightcpverifier_id,
                ],
                check=False,
            )
        if network_id:
            _docker(["network", "rm", network_id], check=False)
        shutil.rmtree(package.parent, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--task",
        choices=(
            "generation",
            "hacking",
            "fault_coverage",
            "fault_exposure",
            "test_package",
        ),
        required=True,
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--agent-image-id", required=True)
    parser.add_argument("--relay-container", default=DEFAULT_RELAY_CONTAINER)
    parser.add_argument("--relay-image-id", required=True)
    parser.add_argument("--lightcpverifier-container")
    parser.add_argument("--lightcpverifier-image-id")
    parser.add_argument("--integration-manifest-sha256", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=21_000)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout_seconds <= 21_000:
        parser.error("--timeout-seconds must be in [1, 21000]")
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
    for signum in previous:
        signal.signal(signum, _request_stop)
    try:
        receipt = run(
            task=args.task,
            workspace=args.workspace,
            agent_image_id=args.agent_image_id,
            relay_container=args.relay_container,
            relay_image_id=args.relay_image_id,
            lightcpverifier_container=args.lightcpverifier_container,
            lightcpverifier_image_id=args.lightcpverifier_image_id,
            integration_manifest_sha256=args.integration_manifest_sha256,
            timeout=args.timeout_seconds,
        )
        _write_json(args.workspace / "control" / "zero-mount-scheduler.json", receipt)
        return 0
    except Exception as exc:
        print(f"zero-mount scheduler failed: {type(exc).__name__}: {exc}", file=os.sys.stderr)
        return 1
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
