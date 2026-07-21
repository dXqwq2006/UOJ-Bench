# Local Run And Debug

This file is the operational companion to the component guides. Use it whenever the task is not just "write code" but also "compile it, run it, and prove the boundary works".

Assume:

- `testlib.h` is available in the include path;
- binaries are built in the current directory;
- commands are examples, not mandatory platform scripts.

## 1. Canonical Compile Pattern

Typical compile command:

```bash
g++ -std=c++17 -O2 -Wall -Wextra -Wshadow -I/path/to/testlib artifact.cpp -o artifact
```

If you are compiling against a repo-local copy:

```bash
g++ -std=c++17 -O2 -Wall -Wextra -Wshadow -I. validator.cpp -o validator
```

Keep one canonical compile command per artifact in the task notes or package scripts.

## 2. Validator Commands

Run a validator on one testcase:

```bash
./validator < test.in
```

Good workflow:

```bash
./gen -n 100 -m 200 > test.in
./validator < test.in
```

Negative probes:

```bash
printf '0\n' | ./validator
printf '3 4 extra\n' | ./validator
```

If multi-test diagnostics matter, use `setTestCase(tc)` in the validator code and keep at least one malformed case per slot.

## 3. Generator Commands

Typical generator invocation:

```bash
./gen -n 100 -m 200 17 > test.in
```

Or positional:

```bash
./gen 100 3 > test.in
```

Always follow generation with validation:

```bash
./gen -n 100 -m 200 > test.in
./validator < test.in
```

For promoted tests, record the exact command line and seed or save the minimized testcase itself.

## 4. Checker Commands

`registerTestlibCmd` checkers expect:

```bash
./checker input.txt contestant.out jury.ans
```

Operationally:

- arg1 -> `inf`
- arg2 -> `ouf` or `ans`? No. For `registerTestlibCmd`, the standard order is:
  - `argv[1]` = input file
  - `argv[2]` = contestant output file
  - `argv[3]` = jury answer file

The common human mnemonic is:

```bash
./checker input.txt output.txt answer.txt
```

Be consistent in scripts and comments. Do not casually swap `output` and `answer`.

Good loop:

```bash
./solver < input.txt > output.txt
./checker input.txt output.txt answer.txt
```

For strict-output probes, keep cases with:

- extra tokens;
- truncated output;
- invalid witness shape;
- legal but non-canonical witness;
- organizer-side bad answer file.

## 5. Interactor Commands

For direct non-piped smoke tests, remember the contract:

```text
interactor input-file tout-file [answer-file ...]
```

The interactor:

- reads hidden input from `inf`;
- reads contestant replies from stdin via `ouf`;
- writes to the contestant through stdout;
- writes transcript or handoff data to `tout`.

For real local testing, use a process runner that connects the contestant and interactor.

### Python interactive runner

The official repository ships an `interactive_runner.py` pattern. Typical use:

```bash
python3 interactive_runner.py ./interactor test.in transcript.out -- ./solution
```

Interpretation:

- `./interactor test.in transcript.out` is the judge command;
- `./solution` is the contestant command;
- both stderr streams are relayed for debugging.

This is a convenient local harness, not a guarantee of server-identical behavior.

### Cross-run runner

The official upstream tests also demonstrate a cross-run setup using a dedicated helper jar:

```bash
java -jar CrossRun.jar ./interactor test.in transcript.out -- ./solution
```

Use this only if the task actually needs a communication-style runner. Do not introduce it for ordinary interactive tasks.

## 6. Run-Twice / Communication Smoke Tests

For communication or run-twice tasks, keep the handoff explicit.

Minimum local loop:

1. run first phase;
2. inspect the produced `tout` or other handoff artifact;
3. run second phase against that artifact;
4. run the final checker if the platform requires one.

Conceptual shape:

```bash
python3 interactive_runner.py ./phase1_interactor hidden.in handoff.out -- ./solution_role1
# inspect or transform handoff.out as the task requires
./phase2_checker hidden.in contestant_role2.out handoff.out
```

The exact command shape depends on the package. The point is that the handoff owner and format must be written down before this stage.

## 7. Grader And Stub Commands

For function-interface tasks, compile the public stub or contestant file together with the hidden grader.

Example:

```bash
g++ -std=c++17 -O2 -Wall -Wextra -I. grader.cpp contestant.cpp -o run
./run < input.txt > output.txt
```

If the output still needs semantic validation:

```bash
./run < input.txt > output.txt
./checker input.txt output.txt answer.txt
```

Local verification must prove:

- the public header compiles;
- the stub signature matches the grader;
- the grader calls the documented function exactly as promised.

## 8. Stress And Differential Testing

The loop lengths below demonstrate harness mechanics only. Under ICPC Light,
100 seeds are smoke coverage and final acceptance requires exhaustive tiny
cases or the main workflow's 5,000--10,000 consecutive reproducible seeds.
Never substitute this component example for that release gate.

Batch tasks:

```bash
for seed in $(seq 1 1000); do
  ./gen "$seed" > test.in || break
  ./validator < test.in || break
  ./model < test.in > answer.txt || break
  ./candidate < test.in > output.txt || break
  ./checker test.in output.txt answer.txt || break
done
```

If you have a brute-force or slow reference:

```bash
for seed in $(seq 1 1000); do
  ./gen "$seed" > test.in || break
  ./validator < test.in || break
  ./slow < test.in > slow.out || break
  ./fast < test.in > fast.out || break
  diff -u slow.out fast.out || break
done
```

Interactive tasks:

- stress the protocol with a dummy contestant that violates one rule at a time;
- keep replay transcripts for deadlocks and phase mismatches;
- never rely only on the happy-path model solution.

## 9. Harness And Wrapper Contract

If you do create a local harness or wrapper, keep the artifact contract explicit.

Typical harness roles:

- wire an interactor and contestant together;
- replay a transcript against a checker or second phase;
- run a grader plus contestant stub with fixed paths;
- batch a generator / validator / solver / checker loop.

For each harness, write down:

- command-line interface;
- working directory assumptions;
- which child processes it launches;
- who owns stdin/stdout/stderr;
- where transcripts or temporary files go;
- what exit code means success or failure.

Minimal review questions:

- does the wrapper preserve the same file order and stream ownership as the real artifact?
- does it accidentally hide crashes or deadlocks?
- are logs separated from contestant-facing stdout?
- can another agent rerun it from one command without guessing paths?

## 10. Debugging Rules

Use these rules unless the host platform requires something else.

- validator / checker / interactor diagnostics go to stderr or through `quitf`, not to contestant-facing stdout;
- interactor stdout is protocol traffic, so debug logging there is a bug;
- keep one saved failing testcase or transcript per distinct failure mode;
- minimize cases before promoting them into permanent regressions;
- if a checker or interactor hits `_fail`, inspect official data first, not contestant output first.

## 11. High-Value Probe Bank

Keep a tiny bank for every new artifact.

Validator probes:

- smallest legal case;
- largest legal case;
- extra token at EOF;
- wrong line shape;
- bound violation;
- structural violation.

Checker probes:

- exact legal jury-format answer;
- alternate legal contestant answer;
- malformed contestant answer;
- extra contestant token;
- organizer-corrupted answer file.

Interactor probes:

- contestant never responds;
- contestant sends malformed command;
- contestant exceeds budget;
- contestant sends extra output after terminal state;
- judge-side impossible state.

Grader probes:

- minimal public stub compiles;
- signature mismatch is caught;
- repeated calls do not leak stale state;
- invalid return or malformed output is handled cleanly.
