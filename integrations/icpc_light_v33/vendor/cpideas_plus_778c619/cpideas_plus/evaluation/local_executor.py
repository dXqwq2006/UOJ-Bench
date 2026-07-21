"""Trusted-only Linux subprocess backend for generated C++ execution."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from .execution import CustomTestResult
from .local_runtime import (
    EXECUTION_CATEGORY_UNSPECIFIED,
    STDERR_CAPTURE_LIMIT_BYTES,
    execution_memory_limit_mb,
    execution_time_limit_ms,
)

EXECUTION_BACKEND_LOCAL = "local"
_COMPILE_OUTPUT_LIMIT_BYTES = 1024 * 1024
_FILE_SIZE_LIMIT_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class _ProcessResult:
    exit_code: int
    stdout: str
    stderr: str
    time_ms: int
    timed_out: bool = False
    output_limit_exceeded: bool = False


@dataclass(frozen=True)
class _CompileRecord:
    executable: Path | None
    result: _ProcessResult


class LocalCustomTestRunner:
    """Compile and run trusted generated C++ directly on a Linux host.

    This backend provides resource limits and process-group cleanup, not a security
    sandbox. Code can still access the host filesystem, network, and allowed syscalls.
    """

    backend = EXECUTION_BACKEND_LOCAL

    def __init__(self, compiler: str | None = None):
        if platform.system() != "Linux":
            raise RuntimeError("local verifier backend currently supports Linux only")
        compiler_name = compiler or os.getenv("CXX") or "g++"
        self.compiler = shutil.which(compiler_name)
        if not self.compiler:
            raise RuntimeError("local verifier backend requires GNU g++")
        self.prlimit = shutil.which("prlimit")
        if not self.prlimit:
            raise RuntimeError("local verifier backend requires the Linux prlimit tool")
        self._workspace = tempfile.TemporaryDirectory(prefix="cpideas-local-verifier-")
        self._root = Path(self._workspace.name)
        self._compile_cache: dict[str, _CompileRecord] = {}
        self._compiler_identity = self._read_compiler_identity()

    def close(self) -> None:
        workspace = getattr(self, "_workspace", None)
        if workspace is not None:
            workspace.cleanup()
            self._workspace = None

    def __del__(self) -> None:
        self.close()

    def custom_test(
        self,
        language: str,
        code: str,
        stdin: str,
        time_limit_ms: int = 2000,
        memory_limit_mb: int = 256,
        max_output_bytes: int = 1024 * 1024,
        execution_category: str = EXECUTION_CATEGORY_UNSPECIFIED,
        argv: Sequence[str] | None = None,
        copy_in_files: Mapping[str, str] | None = None,
        compile_copy_in_files: Mapping[str, str] | None = None,
        compile_only: bool = False,
        source_name: str | None = None,
    ) -> CustomTestResult:
        if language.lower() not in {"c++", "cpp", "cxx", "g++", "cpp17", "gnu++17"}:
            raise ValueError("local verifier backend currently supports C++17 only")
        requested_time_limit_ms = int(time_limit_ms)
        requested_memory_limit_mb = int(memory_limit_mb)
        effective_time_limit_ms = execution_time_limit_ms(requested_time_limit_ms)
        effective_memory_limit_mb = execution_memory_limit_mb(requested_memory_limit_mb)
        output_limit = max(1024, int(max_output_bytes))
        source_name = source_name or "main.cpp"
        _validate_relative_path(source_name)
        compile_files = dict(compile_copy_in_files or {})
        runtime_files = dict(copy_in_files or {})
        _validate_copy_in_files(compile_files)
        _validate_copy_in_files(runtime_files)

        key = self._compile_key(code, source_name, compile_files)
        cached = key in self._compile_cache
        compile_record = self._compile_cache.get(key)
        if compile_record is None:
            compile_record = self._compile(
                key,
                code,
                source_name,
                compile_files,
                effective_time_limit_ms,
                effective_memory_limit_mb,
            )
            self._compile_cache[key] = compile_record

        if compile_record.executable is None or compile_only:
            result = compile_record.result
            ok = compile_record.executable is not None
            status = "compiled" if ok else "compile_error"
            return self._custom_result(
                status=status,
                ok=ok,
                result=result,
                execution_category=execution_category,
                effective_time_limit_ms=effective_time_limit_ms,
                effective_memory_limit_mb=effective_memory_limit_mb,
                output_limit=output_limit,
                requested_time_limit_ms=requested_time_limit_ms,
                requested_memory_limit_mb=requested_memory_limit_mb,
                cached=cached,
            )

        with tempfile.TemporaryDirectory(dir=self._root, prefix="run-") as directory:
            run_dir = Path(directory)
            executable = run_dir / "program"
            shutil.copy2(compile_record.executable, executable)
            executable.chmod(0o700)
            _write_copy_in_files(run_dir, runtime_files)
            result = _run_process(
                [str(executable), *(str(item) for item in (argv or ()))],
                cwd=run_dir,
                stdin=stdin,
                time_limit_ms=effective_time_limit_ms,
                memory_limit_mb=effective_memory_limit_mb,
                max_output_bytes=output_limit,
                prlimit=self.prlimit,
            )

        memory_exceeded = _looks_like_memory_limit(result)
        if result.timed_out or result.exit_code == -signal.SIGXCPU:
            status = "time_limit_exceeded"
        elif result.output_limit_exceeded:
            status = "output_limit_exceeded"
        elif memory_exceeded:
            status = "memory_limit_exceeded"
        elif result.exit_code == 0:
            status = "exited"
        else:
            status = "nonzero_exit"
        return self._custom_result(
            status=status,
            ok=status == "exited",
            result=result,
            execution_category=execution_category,
            effective_time_limit_ms=effective_time_limit_ms,
            effective_memory_limit_mb=effective_memory_limit_mb,
            output_limit=output_limit,
            requested_time_limit_ms=requested_time_limit_ms,
            requested_memory_limit_mb=requested_memory_limit_mb,
            cached=cached,
        )

    def _compile(
        self,
        key: str,
        code: str,
        source_name: str,
        compile_files: dict[str, str],
        time_limit_ms: int,
        memory_limit_mb: int,
    ) -> _CompileRecord:
        work = self._root / f"compile-{key}"
        work.mkdir()
        _write_copy_in_files(work, compile_files)
        source = _resolve_under(work, source_name)
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(code, encoding="utf-8")
        executable = work / "program"
        result = _run_process(
            [
                self.compiler,
                str(source.relative_to(work)),
                "-O2",
                "-pipe",
                "-std=gnu++17",
                "-o",
                executable.name,
            ],
            cwd=work,
            stdin="",
            time_limit_ms=time_limit_ms,
            memory_limit_mb=memory_limit_mb,
            max_output_bytes=_COMPILE_OUTPUT_LIMIT_BYTES,
            prlimit=self.prlimit,
        )
        if result.exit_code != 0 or not executable.is_file():
            return _CompileRecord(None, result)
        executable.chmod(0o700)
        return _CompileRecord(executable, result)

    def _compile_key(
        self, code: str, source_name: str, compile_files: dict[str, str]
    ) -> str:
        payload = {
            "compiler": self._compiler_identity,
            "profile": "-O2 -pipe -std=gnu++17",
            "source_name": source_name,
            "code": code,
            "files": sorted(compile_files.items()),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _read_compiler_identity(self) -> str:
        result = subprocess.run(
            [self.compiler, "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            check=False,
        )
        first_line = result.stdout.splitlines()[0] if result.stdout else "unknown"
        return f"{Path(self.compiler).resolve()}:{first_line}"

    @staticmethod
    def _custom_result(
        *,
        status: str,
        ok: bool,
        result: _ProcessResult,
        execution_category: str,
        effective_time_limit_ms: int,
        effective_memory_limit_mb: int,
        output_limit: int,
        requested_time_limit_ms: int,
        requested_memory_limit_mb: int,
        cached: bool,
    ) -> CustomTestResult:
        signal_name = None
        if result.exit_code < 0:
            try:
                signal_name = signal.Signals(-result.exit_code).name
            except ValueError:
                signal_name = str(-result.exit_code)
        payload = {
            "status": status,
            "ok": ok,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exitStatus": result.exit_code,
            "signal": signal_name,
            "timeMs": result.time_ms,
            "memoryBytes": 0,
            "cached": cached,
            "backend": EXECUTION_BACKEND_LOCAL,
        }
        return CustomTestResult(
            status=status,
            ok=ok,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_status=result.exit_code,
            signal=signal_name,
            time_ms=result.time_ms,
            memory_bytes=0,
            payload=payload,
            execution_category=execution_category,
            time_limit_ms=effective_time_limit_ms,
            memory_limit_mb=effective_memory_limit_mb,
            max_output_bytes=output_limit,
            requested_time_limit_ms=requested_time_limit_ms,
            requested_memory_limit_mb=requested_memory_limit_mb,
            execution_backend=EXECUTION_BACKEND_LOCAL,
            execution_backend_deprecated=False,
        )


def _run_process(
    command: list[str],
    *,
    cwd: Path,
    stdin: str,
    time_limit_ms: int,
    memory_limit_mb: int,
    max_output_bytes: int,
    prlimit: str,
) -> _ProcessResult:
    cpu_seconds = max(1, math.ceil(time_limit_ms / 1000))
    memory_bytes = max(16, memory_limit_mb) * (1 << 20)
    limited_command = [
        prlimit,
        f"--as={memory_bytes}",
        f"--cpu={cpu_seconds}:{cpu_seconds + 1}",
        f"--fsize={_FILE_SIZE_LIMIT_BYTES}",
        "--core=0",
        "--",
        *command,
    ]
    stdin_path = cwd / ".cpideas-stdin"
    stdin_path.write_text(stdin, encoding="utf-8")
    started = time.monotonic()
    with stdin_path.open("rb") as stdin_handle:
        process = subprocess.Popen(
            limited_command,
            cwd=cwd,
            stdin=stdin_handle,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout is not None and process.stderr is not None
        stdout = bytearray()
        stderr = bytearray()
        timed_out = False
        output_limit_exceeded = False
        deadline = started + time_limit_ms / 1000
        selector = selectors.DefaultSelector()
        try:
            os.set_blocking(process.stdout.fileno(), False)
            os.set_blocking(process.stderr.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map() or process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    _kill_process_group(process)
                    break
                if selector.get_map():
                    events = selector.select(min(0.05, remaining))
                else:
                    time.sleep(min(0.05, remaining))
                    events = []
                for key, _ in events:
                    try:
                        chunk = os.read(key.fileobj.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    target = stdout if key.data == "stdout" else stderr
                    limit = max_output_bytes
                    if key.data == "stderr":
                        limit = min(limit, STDERR_CAPTURE_LIMIT_BYTES)
                    remaining_bytes = max(0, limit - len(target))
                    target.extend(chunk[:remaining_bytes])
                    if key.data == "stdout" and len(chunk) > remaining_bytes:
                        output_limit_exceeded = True
                        _kill_process_group(process)
                        break
                if output_limit_exceeded:
                    break

            _kill_process_group(process)
            try:
                exit_code = process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                _kill_process_group(process)
                exit_code = process.wait()
        finally:
            selector.close()
            _kill_process_group(process)
            if process.poll() is None:
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    _kill_process_group(process)
                    process.wait()
            process.stdout.close()
            process.stderr.close()
    stdin_path.unlink(missing_ok=True)
    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    return _ProcessResult(
        exit_code=exit_code,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        time_ms=elapsed_ms,
        timed_out=timed_out,
        output_limit_exceeded=output_limit_exceeded,
    )


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _validate_copy_in_files(files: Mapping[str, str]) -> None:
    for name, content in files.items():
        _validate_relative_path(name)
        if not isinstance(content, str):
            raise ValueError(f"copy-in file must contain text: {name}")


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe local verifier path: {value!r}")


def _resolve_under(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if root.resolve() not in path.parents:
        raise ValueError(f"local verifier path escapes workspace: {relative!r}")
    return path


def _write_copy_in_files(root: Path, files: Mapping[str, str]) -> None:
    for name, content in files.items():
        path = _resolve_under(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _looks_like_memory_limit(result: _ProcessResult) -> bool:
    if result.timed_out or result.output_limit_exceeded or result.exit_code == 0:
        return False
    stderr = result.stderr.lower()
    return "std::bad_alloc" in stderr or "cannot allocate memory" in stderr
