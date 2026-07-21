from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .native_package import NativePackageAdapter
from .local_runtime import (
    EXECUTION_CATEGORY_SUBMISSION,
    SOLUTION_OUTPUT_LIMIT_BYTES,
    TestPointResult,
    first_failed_test,
    first_failure_detail,
    overall_verdict,
    status_from_run_result,
    test_point_result,
    timeout,
)
from .package_adapter import (
    _check_output,
    _compile_cpp,
    _package_copy_in_files,
    _run_cpp,
)
from .execution import CustomTestRunner, create_custom_test_runner
from .sandbox import LightCPVerifierHTTPError


def submit_native_package(
    package_dir: Path,
    work_dir: Path,
    code_file: Path,
    out_path: Path,
    *,
    runner: str = "lightcpverifier",
    url: str = "http://127.0.0.1:8081",
) -> dict[str, object]:
    if not code_file.exists():
        raise FileNotFoundError(f"code file not found: {code_file}")

    adapter = NativePackageAdapter(package_dir)
    package = adapter.inspect()
    work = work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    materialized_by_verify = _ensure_materialized_tests(adapter, package, work, runner, url)

    executor = create_custom_test_runner(runner, lightcpverifier_url=url)
    checker_source, checker_compile = adapter._compile_checker_or_none(package, executor)

    compile_result = _compile_cpp(executor, code_file, _package_copy_in_files(package))
    tests: list[TestPointResult] = []
    detail = ""
    verdict = "AC"

    if package.checker_source and checker_source is None:
        verdict = "CHECKER_CE"
        detail = checker_compile.stderr if checker_compile else "Checker source exists but checker did not compile"
    elif compile_result.exit_code != 0:
        verdict = "CE"
        detail = compile_result.stderr
    else:
        tests = _judge_submission(package, work, code_file, checker_source, executor)
        verdict = overall_verdict("AC", tests)
        detail = first_failure_detail(tests)

    report = {
        "schema_version": "cpideas.submission_report.v1",
        "runner": runner,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_path": str(code_file),
        "package": str(package_dir),
        "work_dir": str(work),
        "verdict": verdict,
        "passed": sum(1 for test in tests if test.status == "AC"),
        "total": len(tests),
        "failed_test": first_failed_test(tests),
        "detail": detail,
        "compile": compile_result.to_dict(),
        "checker_compile": checker_compile.to_dict() if checker_compile else None,
        "materialized_by_verify": materialized_by_verify,
        "tests": [test.to_dict() for test in tests],
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _ensure_materialized_tests(
    adapter: NativePackageAdapter,
    package,
    work: Path,
    runner: str,
    url: str,
) -> bool:
    missing_input = any(not (work / spec.input_path).exists() for spec in package.tests)
    if missing_input:
        adapter.generate(work, runner=runner, url=url)

    missing_answer = any(not (work / spec.answer_path).exists() for spec in package.tests)
    if missing_answer:
        adapter.verify(work, runner=runner, url=url)
        return True
    return False


def _judge_submission(
    package,
    work: Path,
    code_file: Path,
    checker_source: Path | None,
    executor: CustomTestRunner,
) -> list[TestPointResult]:
    test_results: list[TestPointResult] = []
    code = code_file.read_text(encoding="utf-8")
    package_files = _package_copy_in_files(package)
    for spec in package.tests:
        input_path = work / spec.input_path
        answer_path = work / spec.answer_path
        try:
            run_result = _run_cpp(
                executor,
                code_file,
                package_files,
                stdin=input_path.read_text(encoding="utf-8"),
                time_limit_ms=timeout(package) * 1000,
                max_output_bytes=SOLUTION_OUTPUT_LIMIT_BYTES,
                memory_limit_bytes=package.memory_limit_bytes,
                execution_category=EXECUTION_CATEGORY_SUBMISSION,
            )
        except LightCPVerifierHTTPError as exc:
            if exc.status_code == 413:
                raise RuntimeError(
                    _lightcp_payload_too_large_message(
                        spec.index,
                        spec.input_path,
                        input_path,
                        code,
                    )
                ) from exc
            raise

        status, detail = status_from_run_result(run_result)
        if status != "AC":
            test_results.append(test_point_result(spec, status, run_result, detail))
            continue
        output_text = run_result.stdout
        run_time_ms = run_result.time_ms
        run_memory_kb = run_result.memory_kb
        exit_code = run_result.exit_code
        run_execution_category = run_result.execution_category
        run_execution_backend = run_result.execution_backend
        run_execution_backend_deprecated = run_result.execution_backend_deprecated
        run_time_limit_sec = run_result.time_limit_sec
        run_memory_limit_bytes = run_result.memory_limit_bytes

        ok, detail = _check_output(
            executor,
            package,
            checker_source,
            input_path.read_text(encoding="utf-8"),
            output_text,
            answer_path.read_text(encoding="utf-8"),
        )
        status = "AC" if ok else "WA"
        test_results.append(
            TestPointResult(
                spec.index,
                spec.input_path,
                spec.answer_path,
                status,
                run_time_ms,
                run_memory_kb,
                exit_code,
                "" if status == "AC" else detail,
                run_execution_category,
                run_time_limit_sec,
                run_memory_limit_bytes,
                run_execution_backend,
                run_execution_backend_deprecated,
            )
        )
    return test_results


def _lightcp_payload_too_large_message(
    test_index: int,
    input_rel_path: str,
    input_path: Path,
    code: str,
) -> str:
    code_bytes = len(code.encode("utf-8"))
    try:
        stdin_bytes = input_path.stat().st_size
    except OSError:
        stdin_bytes = len(input_path.read_text(encoding="utf-8").encode("utf-8"))
    return (
        "LightCPVerifier rejected the custom-test request with HTTP 413 "
        f"while judging test #{test_index} ({input_rel_path}). "
        "The request body includes the submitted source plus the full test input; "
        f"source={code_bytes} bytes, stdin={stdin_bytes} bytes. "
        "Increase/rebuild the LightCPVerifier request body limit."
    )
