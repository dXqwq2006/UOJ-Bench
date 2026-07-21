"""Native package generation and verification flow.

This module hosts the layout-neutral generate/verify flow used by native CPIdeas
packages. Package schema parsing lives in ``evaluation/native_package.py``;
low-level compile/run/status/verdict primitives live in
``evaluation/local_runtime.py``.

Key concepts maintained here:

* **Exit code 67 = UNSUPPORTED.** A scale-limited but logically correct solution may
  ``exit(67)`` before printing on inputs it does not support.
  ``local_runtime.status_from_run_result`` translates that into a ``UNSUPPORTED``
  TestPointResult, and ``local_runtime.overall_verdict``
  upgrades the aggregate to ``PARTIAL_AC`` when a candidate's only non-AC points are
  ``UNSUPPORTED``. See ``docs/RUN_ARTIFACTS.md`` for the protocol's user-facing
  description.
* **Execution categories and limits.** Every generated-code execution records a
  semantic category (generator, validator, brute_force, candidate, solution, ...)
  while the current effective limit is uniform: 5 seconds and 1024 MB.
* **Output limits.** Solution stdout is capped at ``SOLUTION_OUTPUT_LIMIT_BYTES`` (64 MB)
  to keep a runaway program from filling the disk. Generator output is allowed up to
  ``GENERATED_TEST_OUTPUT_LIMIT_BYTES`` (256 MB) because some adversarial tests are
  legitimately huge.
* **Local memory limits.** Locally run generated executables are capped at the
  package memory limit when the platform supports ``RLIMIT_AS``. Exceeding it is
  reported as ``MLE`` instead of letting a generated binary consume host memory.

For per-function notes see ``docs/PIPELINE_INTERNALS.md``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .local_runtime import (
    EXECUTION_BACKEND_LIGHTCPVERIFIER,
    EXECUTION_CATEGORY_BRUTE_FORCE,
    EXECUTION_CATEGORY_CANDIDATE,
    EXECUTION_CATEGORY_CHECKER,
    EXECUTION_CATEGORY_COMPILE,
    EXECUTION_CATEGORY_GENERATOR,
    EXECUTION_CATEGORY_SOLUTION,
    EXECUTION_CATEGORY_VALIDATOR,
    GENERATED_TEST_OUTPUT_LIMIT_BYTES,
    SOLUTION_OUTPUT_LIMIT_BYTES,
    UNSUPPORTED_INPUT_EXIT_CODE,
    CommandResult,
    TestPointResult,
    compile_error_detail,
    execution_memory_limit_bytes,
    first_failed_test,
    first_failure_detail,
    normalize_lightcp_status,
    overall_verdict,
    status_from_run_result,
    test_point_result,
    timeout,
)
from .election import (
    _bruteforce_vote_summary,
    _claimed_complexity_for_spec,
    _elect_best,
    _expected_verdict_for_tag,
    _is_bruteforce_tag,
    _majority_vote_truth,
    _normalize_extra_report,
    _normalize_tokens,
    _package_with_tests,
    _preview_output,
    _qualify_candidates,
    _write_disagreement_report,
)
from .execution import CustomTestResult, CustomTestRunner, create_custom_test_runner
from .sandbox import LightCPVerifierHTTPError
from .spec import PackageSolutionSpec, PackageSpec, PackageTestSpec, SolutionResult


class LocalPackageAdapter:
    """Generate and verify a parsed local package.

    Three public methods are exposed:

    * ``inspect()`` parses package metadata into a ``PackageSpec`` (no side effects).
    * ``generate(work_dir)`` materializes every declared test into ``work_dir/tests/``
      and runs the validator on each input. Returns the report dict and also writes it
      to ``<generate_report_name>``.
    * ``verify(work_dir, runner=...)`` runs the Phase-4 verifier election pipeline:
      brute-force majority vote, AC candidate election, answer writing, and solution
      judging. Writes ``<verify_report_name>``.
    """

    def __init__(self, package_dir: Path):
        self.package_dir = package_dir.resolve()

    @property
    def generate_report_name(self) -> str:
        return "package_generate_report.json"

    @property
    def verify_report_name(self) -> str:
        return "package_report.json"

    def inspect(self) -> PackageSpec:
        raise NotImplementedError("subclasses must parse a package into PackageSpec")

    def generate(
        self,
        output_dir: Path,
        runner: str = "lightcpverifier",
        url: str = "http://127.0.0.1:8081",
    ) -> dict[str, object]:
        """Compile-check generator/validator and materialize tests into ``output_dir``.

        Behaviour:

        * Generated code is compiled and run by the selected execution backend.
        * Manual tests are copied (or written from inline ``manual_input``) to
          ``output_dir/<test.input_path>``.
        * Generated tests run the compiled generator with the declared argv list and
          capture stdout into the same path. Output is capped at
          ``GENERATED_TEST_OUTPUT_LIMIT_BYTES``.
        * If a validator exists, it is fed each test input and its result is recorded.
        * The aggregated report is written to
          ``output_dir/<generate_report_name>`` and also returned.

        Raises ``ValueError`` for unrecoverable shape mismatches (e.g. a generated test
        exists but the package declared no generator).
        """
        package = self.inspect()
        work = output_dir.resolve()
        tests_dir = work / "tests"
        bin_dir = work / "bin"
        tests_dir.mkdir(parents=True, exist_ok=True)
        bin_dir.mkdir(parents=True, exist_ok=True)
        executor = create_custom_test_runner(runner, lightcpverifier_url=url)
        package_files = _package_copy_in_files(package)

        # Compile every declared generator. Multiple-generator support is the Phase 1.5
        # change: each generator draft from artifact synthesis lands as its own binary,
        # so an oracle / adversarial / random generator can each contribute tests with
        # their own argv shape.
        generator_sources_ok: dict[str, Path] = {}
        validator_source: Path | None = None
        compile_results: dict[str, object] = {}
        generator_sources = package.resolved_generators()
        default_generator_name = package.default_generator_name()
        for name, source in generator_sources.items():
            source_path = package.root / source
            result = _compile_cpp(
                executor,
                source_path,
                package_files,
            )
            # Use a "generator" key for the default binary so existing report consumers
            # that inspect compile_results["generator"] keep working. Other binaries
            # land under their own key.
            report_key = (
                "generator" if name == default_generator_name else f"generator:{name}"
            )
            compile_results[report_key] = result.to_dict()
            if result.exit_code == 0:
                generator_sources_ok[name] = source_path
        if package.validator_source:
            candidate_validator_source = package.root / package.validator_source
            validator_compile = _compile_cpp(
                executor,
                candidate_validator_source,
                package_files,
            )
            compile_results["validator"] = validator_compile.to_dict()
            if validator_compile.exit_code == 0:
                validator_source = candidate_validator_source

        generated: list[dict[str, object]] = []
        generation_failures: list[dict[str, object]] = []
        if (
            any(spec.method == "generated" for spec in package.tests)
            and generator_sources
            and not generator_sources_ok
        ):
            _remove_generated_test_artifacts(work, package)
            report = self._write_generate_report(
                work, package, compile_results, generated
            )
            failures = ", ".join(
                f"{name}: {compile_error_detail(compile_results.get('generator' if name == default_generator_name else f'generator:{name}'))}"
                for name in generator_sources
            )
            raise ValueError(
                f"All generators failed to compile. See {report['report_path']}: {failures}"
            )
        if package.validator_source and validator_source is None:
            _remove_generated_test_artifacts(work, package)
            report = self._write_generate_report(
                work, package, compile_results, generated
            )
            raise ValueError(
                f"Validator failed to compile. See {report['report_path']}: {compile_error_detail(compile_results.get('validator'))}"
            )

        for spec in package.tests:
            destination = work / spec.input_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            source_manual = package.root / spec.input_path
            if spec.method == "manual":
                if spec.manual_input is not None:
                    destination.write_text(spec.manual_input, encoding="utf-8")
                elif source_manual.exists():
                    shutil.copyfile(source_manual, destination)
                else:
                    raise FileNotFoundError(f"Manual test not found: {source_manual}")
                command_result = None
            elif spec.method == "generated":
                _remove_test_artifacts(work, spec)
                requested_name = spec.generator or default_generator_name
                if requested_name is None:
                    raise ValueError(
                        "Generated tests exist but no generator source was found"
                    )
                generator_source = generator_sources_ok.get(requested_name)
                if generator_source is None:
                    # The named generator failed to compile. Surface a deterministic
                    # CommandResult so the report shows the breakage on every test that
                    # depended on it, rather than failing the whole pipeline silently.
                    report_key = (
                        "generator"
                        if requested_name == default_generator_name
                        else f"generator:{requested_name}"
                    )
                    compile_detail = compile_error_detail(
                        compile_results.get(report_key)
                    )
                    command_result = CommandResult(
                        command=[
                            executor.backend,
                            "custom-test",
                            str(package.root / generator_sources.get(requested_name, requested_name)),
                            *_generator_args_for_spec(spec),
                        ],
                        exit_code=-1,
                        stdout="",
                        stderr=f"generator {requested_name!r} unavailable: {compile_detail}",
                        time_ms=0,
                        execution_category=EXECUTION_CATEGORY_GENERATOR,
                        execution_backend=executor.backend,
                    ).to_dict()
                else:
                    command_result_obj = _run_cpp_to_file(
                        executor,
                        generator_source,
                        destination,
                        package_files,
                        argv=_generator_args_for_spec(spec),
                        time_limit_ms=30000,
                        max_output_bytes=GENERATED_TEST_OUTPUT_LIMIT_BYTES,
                        memory_limit_bytes=package.memory_limit_bytes,
                        execution_category=EXECUTION_CATEGORY_GENERATOR,
                    )
                    command_result = command_result_obj.to_dict()
                if int(command_result.get("exit_code", 0)) != 0:
                    generation_failures.append(
                        {
                            "input_path": spec.input_path,
                            "generator": requested_name,
                            "exit_code": command_result.get("exit_code"),
                            "stderr": command_result.get("stderr", ""),
                            "materialized": destination.exists(),
                        }
                    )
            else:
                raise ValueError(f"Unsupported package test method: {spec.method}")

            validation_result = None
            materialized = destination.exists()
            if validator_source is not None and materialized:
                validation_result = _run_cpp(
                    executor,
                    validator_source,
                    package_files,
                    stdin=destination.read_text(encoding="utf-8"),
                    time_limit_ms=10000,
                    max_output_bytes=SOLUTION_OUTPUT_LIMIT_BYTES,
                    memory_limit_bytes=package.memory_limit_bytes,
                    execution_category=EXECUTION_CATEGORY_VALIDATOR,
                ).to_dict()
            elif validator_source is not None and not materialized:
                validation_result = {
                    "status": "SKIPPED",
                    "reason": "input file was not materialized",
                }

            generated.append(
                {
                    "index": spec.index,
                    "method": spec.method,
                    "sample": spec.sample,
                    "cmd": spec.cmd,
                    "generator": spec.generator,
                    "input_path": spec.input_path,
                    "answer_path": spec.answer_path,
                    "materialized": materialized,
                    "command": command_result,
                    "validation": validation_result,
                }
            )

        report = self._write_generate_report(work, package, compile_results, generated)
        if generation_failures:
            first = generation_failures[0]
            raise ValueError(
                "Package test generation failed for "
                f"{len(generation_failures)} test(s). See {report['report_path']}. "
                f"First failure: {first['input_path']} generator={first['generator']} "
                f"exit_code={first['exit_code']} materialized={first['materialized']}"
            )
        return report

    def _write_generate_report(
        self,
        work: Path,
        package: PackageSpec,
        compile_results: dict[str, object],
        generated: list[dict[str, object]],
    ) -> dict[str, object]:
        report = {
            "package": package.to_dict(),
            "work_dir": str(work),
            "compile": compile_results,
            "tests": generated,
            "report_path": str(work / self.generate_report_name),
        }
        (work / self.generate_report_name).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return report

    def verify(
        self,
        work_dir: Path,
        runner: str = "lightcpverifier",
        url: str = "http://127.0.0.1:8081",
    ) -> dict[str, object]:
        """Compile every solution and judge them against the package's tests.

        Args:
            work_dir: Where ``generate`` already wrote (or will be triggered to write)
                ``tests/`` and ``bin/``. The verify report lands here too.
            runner: ``"lightcpverifier"`` or the trusted-only Linux ``"local"``
                backend.
            url: LightCPVerifier base URL.

        Packages must be eligible for the Phase-4 verifier election pipeline: at
        least two brute-force solutions and at least one expected-AC non-brute-force
        candidate. The historical ``tag=="main"`` fallback is no longer supported.
        """
        package = self.inspect()
        work_dir = work_dir.resolve()
        if not (work_dir / self.generate_report_name).exists():
            self.generate(work_dir, runner=runner, url=url)

        bin_dir = work_dir / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        executor = create_custom_test_runner(runner, lightcpverifier_url=url)
        checker_source, checker_compile = self._compile_checker_or_none(package, executor)

        if not self._election_eligible(package):
            raise ValueError(
                "Package verification requires at least two brute-force solutions "
                "and at least one expected-AC non-brute-force solution; legacy "
                "tag==main verification is no longer supported."
            )
        return self._verify_with_election(
            package,
            work_dir,
            bin_dir,
            checker_source,
            checker_compile,
            executor,
        )

    def _compile_checker_or_none(
        self, package: PackageSpec, executor: CustomTestRunner
    ) -> tuple[Path | None, CommandResult | None]:
        """Compile-check the package's checker; return (source_path, result) tuple.

        ``source_path`` is ``None`` when the package declares no checker or the
        checker failed to compile; callers must surface ``CHECKER_CE`` when the
        source exists but compilation failed.
        """
        if not package.checker_source:
            return None, None
        checker_source = package.root / package.checker_source
        compile_result = _compile_cpp(
            executor,
            checker_source,
            _package_copy_in_files(package),
        )
        if compile_result.exit_code != 0:
            return None, compile_result
        return checker_source, compile_result

    def _election_eligible(self, package: PackageSpec) -> bool:
        """Decide whether the Phase-4 election pipeline applies to this package.

        Triggers when at least two brute-force solutions exist (so majority voting
        is meaningful) and at least one expected-AC non-brute-force candidate
        exists (so there is something to elect).
        """
        brute_count = sum(1 for s in package.solutions if _is_bruteforce_tag(s.tag))
        ac_non_brute = sum(
            1
            for s in package.solutions
            if not _is_bruteforce_tag(s.tag)
            and (s.expected or _expected_verdict_for_tag(s.tag)) == "AC"
        )
        return brute_count >= 2 and ac_non_brute >= 1

    def _verify_with_election(
        self,
        package: PackageSpec,
        work_dir: Path,
        bin_dir: Path,
        checker_source: Path | None,
        checker_compile: CommandResult | None,
        executor: CustomTestRunner,
    ) -> dict[str, object]:
        """Phase-4 verify pipeline: brute-force majority vote + model election.

        High-level steps (see ``docs/FLOW.zh-CN.md`` for the user-facing picture):

        1. **Pre-Phase A.** Compile and run all brute-force solutions on every test.
           For each test, collect outputs from brute forces that did not ``exit(67)``
           and majority-vote to derive ``truth``. If any test has all participating
           brute forces disagree pairwise, the run aborts and writes
           ``verification/bruteforce_disagreement.json``.
        2. **Phase B (qualification).** Compile every expected-AC non-brute-force
           candidate. Run each on the tests where Pre-Phase A produced a truth and
           drop any candidate that disagrees.
        3. **Phase C (election).** Among the qualified candidates, pick the one
           covering the largest number of tests without ``exit(67)`` (proxy for
           "best complexity"), tie-broken by smallest ``max_time_ms``.
        4. **Phase D (answer generation).** Run the elected model on every test and
           write its stdout into ``tests/*.ans``. On tests where the model itself
           ``exit(67)``-s, fall back to the brute-force ``truth`` for the answer; if
           neither source has output, the test is dropped from the effective set.
        5. **Phase E (judging).** Run every solution (model, other AC candidates,
           brute forces, wrong candidates) against the materialized answers via the
           usual ``_verify_solution`` path. The election metadata is attached to the
           report as ``election`` so downstream tooling can display it.
        """
        report_extra: dict[str, object] = {}
        if package.checker_source and checker_source is None:
            # CHECKER_CE aborts before election starts.
            detail = (
                checker_compile.stderr
                if checker_compile
                else "Checker source exists but checker did not compile"
            )
            main_like = next(
                (s for s in package.solutions if s.tag == "main"), package.solutions[0]
            )
            result = SolutionResult(
                main_like.source_path,
                main_like.tag,
                "AC",
                "CHECKER_CE",
                checker_compile or CommandResult([], -1, "", detail, 0),
                detail=detail,
            )
            return self._write_verify_report(
                work_dir, package, checker_compile, [result], extra=report_extra
            )

        brute_forces = [s for s in package.solutions if _is_bruteforce_tag(s.tag)]
        ac_candidates = [
            s
            for s in package.solutions
            if not _is_bruteforce_tag(s.tag)
            and (s.expected or _expected_verdict_for_tag(s.tag)) == "AC"
        ]

        # ----- Pre-Phase A: brute-force majority vote ---------------------------
        brute_runs, brute_compile_results = self._collect_bruteforce_runs(
            package, brute_forces, work_dir, executor
        )
        truth, disagreements = _majority_vote_truth(package.tests, brute_runs)
        report_extra["bruteforce_compile"] = brute_compile_results
        report_extra["bruteforce_votes"] = _bruteforce_vote_summary(
            package.tests, brute_runs, truth
        )

        if disagreements:
            _write_disagreement_report(work_dir, disagreements, brute_runs)
            report_extra["election"] = {
                "aborted": True,
                "reason": "bruteforce_disagreement",
                "disagreement_count": len(disagreements),
                "disagreement_report": "bruteforce_disagreement.json",
                "disagreement_tests": [d["input_path"] for d in disagreements[:10]],
            }
            # Still record what we know about every solution so the human reviewer
            # can see compile / partial info; no test verdicts beyond raw runs.
            partial = [
                SolutionResult(
                    bf.source_path,
                    bf.tag,
                    bf.expected or "AC",
                    "ABORTED",
                    brute_compile_results.get(
                        bf.source_path, CommandResult([], 0, "", "", 0)
                    ),
                    detail="Run aborted: brute-force outputs disagreed; see bruteforce_disagreement.json",
                )
                for bf in brute_forces
            ]
            return self._write_verify_report(
                work_dir, package, checker_compile, partial, extra=report_extra
            )

        # ----- Phase B + C: qualify and elect the model -------------------------
        candidate_runs, candidate_compile_results = self._collect_candidate_runs(
            package, ac_candidates, work_dir, executor
        )
        output_matcher = _checker_output_matcher(
            executor,
            package,
            work_dir,
            checker_source,
        )
        qualified, disqualified = _qualify_candidates(
            ac_candidates,
            candidate_runs,
            truth,
            output_matcher,
        )
        report_extra["candidate_compile"] = candidate_compile_results
        report_extra["candidate_qualification"] = {
            "qualified": [c["source_path"] for c in qualified],
            "disqualified": disqualified,
        }

        if not qualified:
            report_extra["election"] = {
                "aborted": True,
                "reason": "no_qualified_ac_candidate",
                "qualified": [],
                "disqualified": disqualified,
            }
            partial = []
            for bf in brute_forces:
                compile_result = brute_compile_results.get(
                    bf.source_path, CommandResult([], -1, "", "no compile attempted", 0)
                )
                tests: list[TestPointResult] = []
                if compile_result.exit_code == 0:
                    for spec in package.tests:
                        record = brute_runs.get(bf.source_path, {}).get(spec.input_path)
                        if record is None:
                            continue
                        status = str(record.get("status") or "RE")
                        tests.append(
                            TestPointResult(
                                spec.index,
                                spec.input_path,
                                spec.answer_path,
                                status,
                                int(record.get("time_ms") or 0),
                                record.get("memory_kb"),
                                record.get("exit_code"),
                                ""
                                if status == "AC"
                                else "Recorded during brute-force majority vote",
                                str(
                                    record.get("execution_category")
                                    or EXECUTION_CATEGORY_BRUTE_FORCE
                                ),
                                record.get("time_limit_sec"),
                                record.get("memory_limit_bytes"),
                                str(
                                    record.get("execution_backend")
                                    or EXECUTION_BACKEND_LIGHTCPVERIFIER
                                ),
                                bool(
                                    record.get(
                                        "execution_backend_deprecated", False
                                    )
                                ),
                            )
                        )
                expected = bf.expected or "AC"
                if compile_result.exit_code != 0:
                    verdict = "REJECTED" if expected == "REJECTED" else "CE"
                else:
                    verdict = overall_verdict(expected, tests)
                partial.append(
                    SolutionResult(
                        bf.source_path,
                        bf.tag,
                        expected,
                        verdict,
                        compile_result,
                        first_failed_test(tests),
                        first_failure_detail(tests),
                        tests,
                    )
                )
            for ac in ac_candidates:
                compile_result = candidate_compile_results.get(
                    ac.source_path, CommandResult([], -1, "", "no compile attempted", 0)
                )
                partial.append(
                    SolutionResult(
                        ac.source_path,
                        ac.tag,
                        ac.expected or "AC",
                        "NO_QUALIFIED_MODEL",
                        compile_result,
                        detail="No AC candidate matched the bruteforce-majority truth on every covered test.",
                    )
                )
            return self._write_verify_report(
                work_dir, package, checker_compile, partial, extra=report_extra
            )

        model_entry = _elect_best(qualified)
        model = next(
            s for s in ac_candidates if s.source_path == model_entry["source_path"]
        )
        report_extra["election"] = {
            "aborted": False,
            "model_source_path": model.source_path,
            "model_tag": model.tag,
            "qualified": qualified,
            "disqualified": disqualified,
            "ranking_criteria": "max non-UNSUPPORTED test count, then min worst-case time",
        }

        # ----- Phase D: write final answers ------------------------------------
        model_runs = candidate_runs[model.source_path]
        dropped_tests: list[str] = []
        effective_tests: list[PackageTestSpec] = []
        for spec in package.tests:
            input_path = work_dir / spec.input_path
            answer_path = work_dir / spec.answer_path
            answer_path.parent.mkdir(parents=True, exist_ok=True)
            model_record = model_runs.get(spec.input_path)
            wrote_answer = False
            if model_record is not None and model_record["status"] == "AC":
                answer_path.write_text(model_record["output"], encoding="utf-8")
                wrote_answer = True
            elif spec.input_path in truth:
                # Model exit-67ed (or otherwise produced no output): fall back to the
                # majority-vote brute-force output for the answer file. This keeps the
                # test usable for solutions that can actually handle that size.
                answer_path.write_text(
                    truth[spec.input_path]["output"], encoding="utf-8"
                )
                wrote_answer = True
            if wrote_answer:
                effective_tests.append(spec)
            else:
                dropped_tests.append(spec.input_path)
                if answer_path.exists():
                    answer_path.unlink()

        report_extra["dropped_tests"] = dropped_tests

        # ----- Phase E: judge every solution against the materialized answers --
        # Reuse the existing _verify_solution path so the per-test status / report
        # shape stays uniform. We re-run every candidate even when we
        # already have its output recorded above — this keeps the report self-
        # consistent and lets the checker run uniformly on every solution.
        scoped_package = package
        if dropped_tests:
            scoped_package = _package_with_tests(package, effective_tests)

        solution_results: list[SolutionResult] = []
        # Inject the model first so the answer-source solution leads the report list.
        model_result = self._verify_solution(
            scoped_package,
            model,
            work_dir,
            bin_dir,
            checker_source,
            model.expected or "AC",
            executor,
        )
        solution_results.append(model_result)
        for solution in package.solutions:
            if solution.source_path == model.source_path:
                continue
            expected = solution.expected or _expected_verdict_for_tag(solution.tag)
            result = self._verify_solution(
                scoped_package,
                solution,
                work_dir,
                bin_dir,
                checker_source,
                expected,
                executor,
            )
            solution_results.append(result)

        return self._write_verify_report(
            work_dir, package, checker_compile, solution_results, extra=report_extra
        )

    def _collect_bruteforce_runs(
        self,
        package: PackageSpec,
        brute_forces: list[PackageSolutionSpec],
        work_dir: Path,
        executor: CustomTestRunner,
    ) -> tuple[dict[str, dict[str, dict[str, object]]], dict[str, CommandResult]]:
        """Compile and run every brute-force on every test. Returns per-bf outputs.

        Return shape::

            outputs[brute.source_path][test.input_path] = {
                "status": "AC" | "UNSUPPORTED" | "TLE" | ...,
                "output": "...",            # stdout (empty when not AC)
                "time_ms": int,
                "memory_kb": int | None,
                "exit_code": int | None,
            }
        """
        runs: dict[str, dict[str, dict[str, object]]] = {}
        compiles: dict[str, CommandResult] = {}
        package_files = _package_copy_in_files(package)
        for bf in brute_forces:
            bf_source = package.root / bf.source_path
            compile_result = _compile_cpp(executor, bf_source, package_files)
            compiles[bf.source_path] = compile_result
            bf_runs: dict[str, dict[str, object]] = {}
            if compile_result.exit_code != 0:
                runs[bf.source_path] = bf_runs
                continue
            for spec in package.tests:
                input_path = work_dir / spec.input_path
                run_result = _run_cpp(
                    executor,
                    bf_source,
                    package_files,
                    stdin=input_path.read_text(encoding="utf-8"),
                    time_limit_ms=timeout(package) * 1000,
                    max_output_bytes=SOLUTION_OUTPUT_LIMIT_BYTES,
                    memory_limit_bytes=package.memory_limit_bytes,
                    execution_category=EXECUTION_CATEGORY_BRUTE_FORCE,
                )
                status, _ = status_from_run_result(run_result)
                output_text = run_result.stdout if status == "AC" else ""
                bf_runs[spec.input_path] = {
                    "status": status,
                    "output": output_text,
                    "time_ms": run_result.time_ms,
                    "memory_kb": run_result.memory_kb,
                    "exit_code": run_result.exit_code,
                    "execution_category": run_result.execution_category,
                    "time_limit_sec": run_result.time_limit_sec,
                    "memory_limit_bytes": run_result.memory_limit_bytes,
                    "execution_backend": run_result.execution_backend,
                    "execution_backend_deprecated": run_result.execution_backend_deprecated,
                }
            runs[bf.source_path] = bf_runs
        return runs, compiles

    def _collect_candidate_runs(
        self,
        package: PackageSpec,
        candidates: list[PackageSolutionSpec],
        work_dir: Path,
        executor: CustomTestRunner,
    ) -> tuple[dict[str, dict[str, dict[str, object]]], dict[str, CommandResult]]:
        """Compile and run every AC candidate on every test (shape matches brute-forces)."""
        runs: dict[str, dict[str, dict[str, object]]] = {}
        compiles: dict[str, CommandResult] = {}
        package_files = _package_copy_in_files(package)
        for ac in candidates:
            ac_source = package.root / ac.source_path
            compile_result = _compile_cpp(executor, ac_source, package_files)
            compiles[ac.source_path] = compile_result
            ac_runs: dict[str, dict[str, object]] = {}
            if compile_result.exit_code != 0:
                runs[ac.source_path] = ac_runs
                continue
            for spec in package.tests:
                input_path = work_dir / spec.input_path
                run_result = _run_cpp(
                    executor,
                    ac_source,
                    package_files,
                    stdin=input_path.read_text(encoding="utf-8"),
                    time_limit_ms=timeout(package) * 1000,
                    max_output_bytes=SOLUTION_OUTPUT_LIMIT_BYTES,
                    memory_limit_bytes=package.memory_limit_bytes,
                    execution_category=EXECUTION_CATEGORY_CANDIDATE,
                )
                status, _ = status_from_run_result(run_result)
                output_text = run_result.stdout if status == "AC" else ""
                ac_runs[spec.input_path] = {
                    "status": status,
                    "output": output_text,
                    "time_ms": run_result.time_ms,
                    "memory_kb": run_result.memory_kb,
                    "exit_code": run_result.exit_code,
                    "execution_category": run_result.execution_category,
                    "time_limit_sec": run_result.time_limit_sec,
                    "memory_limit_bytes": run_result.memory_limit_bytes,
                    "execution_backend": run_result.execution_backend,
                    "execution_backend_deprecated": run_result.execution_backend_deprecated,
                }
            runs[ac.source_path] = ac_runs
        return runs, compiles

    def _verify_solution(
        self,
        package: PackageSpec,
        solution: PackageSolutionSpec,
        work_dir: Path,
        bin_dir: Path,
        checker_source: Path | None,
        expected: str,
        executor: CustomTestRunner,
    ) -> SolutionResult:
        _ = bin_dir
        package_files = _package_copy_in_files(package)
        solution_source = package.root / solution.source_path
        compile_result = _compile_cpp(executor, solution_source, package_files)
        if compile_result.exit_code != 0:
            verdict = "CE"
            if expected == "REJECTED":
                return SolutionResult(
                    solution.source_path,
                    solution.tag,
                    expected,
                    "REJECTED",
                    compile_result,
                    detail="Compile error",
                )
            return SolutionResult(
                solution.source_path,
                solution.tag,
                expected,
                verdict,
                compile_result,
                detail=compile_result.stderr,
            )
        if expected == "SKIP":
            return SolutionResult(
                solution.source_path,
                solution.tag,
                expected,
                "SKIPPED",
                compile_result,
                detail="Skipped by expected verdict",
            )

        test_results: list[TestPointResult] = []
        scoped_tests = [
            spec
            for spec in package.tests
            if solution.test_scope is None or spec.group == solution.test_scope
        ]
        for spec in scoped_tests:
            input_path = work_dir / spec.input_path
            answer_path = work_dir / spec.answer_path
            run_result = _run_cpp(
                executor,
                solution_source,
                package_files,
                stdin=input_path.read_text(encoding="utf-8"),
                time_limit_ms=timeout(package) * 1000,
                max_output_bytes=SOLUTION_OUTPUT_LIMIT_BYTES,
                memory_limit_bytes=package.memory_limit_bytes,
                execution_category=EXECUTION_CATEGORY_SOLUTION,
            )
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
            status = "AC"
            if not ok:
                status = "WA"
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

        verdict = overall_verdict(expected, test_results)
        return SolutionResult(
            solution.source_path,
            solution.tag,
            expected,
            verdict,
            compile_result,
            first_failed_test(test_results),
            first_failure_detail(test_results),
            test_results,
        )

    def _write_verify_report(
        self,
        work_dir: Path,
        package: PackageSpec,
        checker_compile: CommandResult | None,
        solution_results: list[SolutionResult],
        extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        report: dict[str, object] = {
            "package": package.to_dict(),
            "work_dir": str(work_dir),
            "checker_compile": checker_compile.to_dict() if checker_compile else None,
            "solutions": [result.to_dict() for result in solution_results],
            "report_path": str(work_dir / self.verify_report_name),
        }
        if extra:
            # Phase-4 election metadata: bruteforce_compile, bruteforce_votes,
            # candidate_compile, candidate_qualification, election, dropped_tests.
            # The compile-result entries are serialized to dicts so the report is
            # JSON-friendly without further mutation.
            normalized = _normalize_extra_report(extra)
            report.update(normalized)
        (work_dir / self.verify_report_name).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return report


def _compile_cpp(
    executor: CustomTestRunner,
    source_path: Path,
    copy_in_files: dict[str, str],
) -> CommandResult:
    return _run_cpp(
        executor,
        source_path,
        copy_in_files,
        stdin="",
        argv=[],
        time_limit_ms=30000,
        max_output_bytes=1024 * 1024,
        memory_limit_bytes=None,
        execution_category=EXECUTION_CATEGORY_COMPILE,
        compile_only=True,
        keep_stdout=False,
    )


def _run_cpp_to_file(
    executor: CustomTestRunner,
    source_path: Path,
    output_path: Path,
    copy_in_files: dict[str, str],
    *,
    argv: list[str],
    time_limit_ms: int,
    max_output_bytes: int,
    memory_limit_bytes: int | None,
    execution_category: str,
) -> CommandResult:
    result = _run_cpp(
        executor,
        source_path,
        copy_in_files,
        stdin="",
        argv=argv,
        time_limit_ms=time_limit_ms,
        max_output_bytes=max_output_bytes,
        memory_limit_bytes=memory_limit_bytes,
        execution_category=execution_category,
    )
    status, _ = status_from_run_result(result)
    if status == "AC":
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result.stdout, encoding="utf-8")
    elif output_path.exists():
        output_path.unlink()
    return _command_result_without_stdout(result)


def _run_cpp(
    executor: CustomTestRunner,
    source_path: Path,
    copy_in_files: dict[str, str],
    *,
    stdin: str,
    argv: list[str] | None = None,
    time_limit_ms: int,
    max_output_bytes: int,
    memory_limit_bytes: int | None,
    execution_category: str,
    compile_only: bool = False,
    keep_stdout: bool = True,
    run_copy_in_files: dict[str, str] | None = None,
) -> CommandResult:
    argv = list(argv or [])
    run_copy_in_files = dict(run_copy_in_files or {})
    requested_memory_limit_bytes = memory_limit_bytes
    requested_memory_limit_mb = _requested_memory_limit_mb(memory_limit_bytes)
    command = [executor.backend, "custom-test", str(source_path)]
    if compile_only:
        command.append("--compile-only")
    command.extend(argv)
    try:
        run = executor.custom_test(
            language="cpp",
            code=source_path.read_text(encoding="utf-8"),
            stdin=stdin,
            time_limit_ms=time_limit_ms,
            memory_limit_mb=requested_memory_limit_mb,
            max_output_bytes=max_output_bytes,
            execution_category=execution_category,
            argv=argv,
            copy_in_files=run_copy_in_files,
            compile_copy_in_files=copy_in_files,
            compile_only=compile_only,
            source_name=source_path.name,
        )
    except LightCPVerifierHTTPError as exc:
        if exc.status_code != 500:
            raise
        run = _custom_test_result_from_http_500(
            exc,
            execution_category=execution_category,
            compile_only=compile_only,
            time_limit_ms=time_limit_ms,
            memory_limit_mb=requested_memory_limit_mb,
            max_output_bytes=max_output_bytes,
        )
    return _command_result_from_custom_test(
        command,
        run,
        requested_time_limit_ms=time_limit_ms,
        requested_memory_limit_bytes=requested_memory_limit_bytes,
        stdout=run.stdout if keep_stdout else "",
    )


def _check_output(
    executor: CustomTestRunner,
    package: PackageSpec,
    checker_source: Path | None,
    input_text: str,
    output_text: str,
    answer_text: str,
) -> tuple[bool, str]:
    if checker_source is None:
        return output_text.split() == answer_text.split(), "Token comparison failed"

    result = _run_cpp(
        executor,
        checker_source,
        _package_copy_in_files(package),
        stdin="",
        argv=["input.txt", "output.txt", "answer.txt"],
        time_limit_ms=10000,
        max_output_bytes=1024 * 1024,
        memory_limit_bytes=package.memory_limit_bytes,
        execution_category=EXECUTION_CATEGORY_CHECKER,
        run_copy_in_files={
            "input.txt": input_text,
            "output.txt": output_text,
            "answer.txt": answer_text,
        },
    )
    status, detail = status_from_run_result(result)
    return status == "AC", result.stdout + result.stderr + ("" if status == "AC" else detail)


def _checker_output_matcher(
    executor: CustomTestRunner,
    package: PackageSpec,
    work_dir: Path,
    checker_source: Path | None,
):
    if checker_source is None:
        return None

    def match(input_path_rel: str, candidate_text: str, reference_text: str) -> bool:
        ok, _ = _check_output(
            executor,
            package,
            checker_source,
            (work_dir / input_path_rel).read_text(encoding="utf-8"),
            candidate_text,
            reference_text,
        )
        return ok

    return match


def _command_result_from_custom_test(
    command: list[str],
    run: CustomTestResult,
    *,
    requested_time_limit_ms: int,
    requested_memory_limit_bytes: int | None,
    stdout: str,
) -> CommandResult:
    normalized_status = normalize_lightcp_status(run.status)
    timed_out = normalized_status == "TLE"
    output_limit_exceeded = normalized_status == "OLE"
    memory_limit_exceeded = normalized_status == "MLE"
    exit_code = run.exit_status
    if timed_out:
        exit_code = 124
    elif output_limit_exceeded:
        exit_code = 153
    elif memory_limit_exceeded and exit_code is None:
        exit_code = 137
    elif exit_code is None:
        exit_code = 0 if run.ok else 1
    stderr = run.stderr
    if not run.ok and not stderr:
        stderr = run.status
    effective_memory_limit_bytes = (
        None
        if run.memory_limit_mb is None
        else run.memory_limit_mb * (1 << 20)
    )
    return CommandResult(
        command=command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        time_ms=run.time_ms,
        memory_kb=int(run.memory_bytes / 1024) if run.memory_bytes else None,
        timed_out=timed_out,
        output_limit_exceeded=output_limit_exceeded,
        memory_limit_exceeded=memory_limit_exceeded,
        memory_limit_bytes=effective_memory_limit_bytes,
        execution_category=run.execution_category,
        time_limit_sec=None if run.time_limit_ms is None else int(run.time_limit_ms / 1000),
        requested_time_limit_sec=int(requested_time_limit_ms / 1000),
        requested_memory_limit_bytes=requested_memory_limit_bytes,
        execution_backend=run.execution_backend,
        execution_backend_deprecated=run.execution_backend_deprecated,
    )


def _custom_test_result_from_http_500(
    exc: LightCPVerifierHTTPError,
    *,
    execution_category: str,
    compile_only: bool,
    time_limit_ms: int,
    memory_limit_mb: int,
    max_output_bytes: int,
) -> CustomTestResult:
    message = str(exc.payload.get("message") or exc.payload.get("error") or exc)
    return CustomTestResult(
        status="compile_error" if compile_only else "runtime_error",
        ok=False,
        stdout="",
        stderr=message,
        exit_status=None,
        signal=None,
        time_ms=0,
        memory_bytes=0,
        payload=exc.payload,
        execution_category=execution_category,
        time_limit_ms=time_limit_ms,
        memory_limit_mb=memory_limit_mb,
        max_output_bytes=max_output_bytes,
        requested_time_limit_ms=time_limit_ms,
        requested_memory_limit_mb=memory_limit_mb,
        execution_backend=EXECUTION_BACKEND_LIGHTCPVERIFIER,
        execution_backend_deprecated=False,
    )


def _command_result_without_stdout(result: CommandResult) -> CommandResult:
    return CommandResult(
        command=result.command,
        exit_code=result.exit_code,
        stdout="",
        stderr=result.stderr,
        time_ms=result.time_ms,
        memory_kb=result.memory_kb,
        timed_out=result.timed_out,
        output_limit_exceeded=result.output_limit_exceeded,
        memory_limit_exceeded=result.memory_limit_exceeded,
        memory_limit_bytes=result.memory_limit_bytes,
        execution_category=result.execution_category,
        time_limit_sec=result.time_limit_sec,
        requested_time_limit_sec=result.requested_time_limit_sec,
        requested_memory_limit_bytes=result.requested_memory_limit_bytes,
        execution_backend=result.execution_backend,
        execution_backend_deprecated=result.execution_backend_deprecated,
    )


def _requested_memory_limit_mb(memory_limit_bytes: int | None) -> int:
    if memory_limit_bytes is None:
        return max(16, execution_memory_limit_bytes(None) // (1 << 20))
    return max(16, int(memory_limit_bytes / (1 << 20)))


def _remove_generated_test_artifacts(work_dir: Path, package: PackageSpec) -> None:
    for spec in package.tests:
        if spec.method == "generated":
            _remove_test_artifacts(work_dir, spec)


def _remove_test_artifacts(work_dir: Path, spec: PackageTestSpec) -> None:
    for relative in (spec.input_path, spec.answer_path):
        path = work_dir / relative
        if path.exists():
            path.unlink()


def _package_copy_in_files(package: PackageSpec) -> dict[str, str]:
    files_dir = package.root / "files"
    if not files_dir.exists():
        return {}
    copy_in: dict[str, str] = {}
    for path in sorted(files_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(files_dir).as_posix()
        if not relative or relative.startswith("/") or ".." in relative.split("/"):
            continue
        copy_in[relative] = path.read_text(encoding="utf-8")
    return copy_in


def _generator_args_for_spec(spec: PackageTestSpec) -> list[str]:
    if spec.generator_args is not None:
        return list(spec.generator_args)
    return _generator_command(Path("generator"), spec.cmd)[1:]


def _generator_binary_stem(name: str) -> str:
    """Return the filename stem under ``bin/`` for a named generator.

    Keeps the historical ``bin/generator`` path for the default generator so existing
    consumers and report formats continue to work, while extra generators land under
    ``bin/generator_<slug>``.
    """
    if name == "generator" or not name:
        return "generator"
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in name)
    return f"generator_{safe}" if not safe.startswith("generator") else safe


def _generator_command(generator_bin: Path, cmd: str | None) -> list[str]:
    if not cmd:
        return [str(generator_bin)]
    parts = cmd.split()
    if not parts:
        return [str(generator_bin)]
    if Path(parts[0]).name == "generator":
        return [str(generator_bin), *parts[1:]]
    return [str(generator_bin), *parts]
