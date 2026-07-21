# Core API

This file is the shared `testlib` surface that later agents should know before opening any component-specific guide.

All code below assumes:

```cpp
#include "testlib.h"
```

Include `testlib.h` before other headers when practical. The upstream header explicitly warns against mixing in `rand`, `srand`, or `random_shuffle`; use `rnd` and `shuffle` instead.

## 1. Registration Functions

Choose exactly one registration entry point per executable.

| File type | Entry point | Main streams / roles |
| --- | --- | --- |
| validator | `registerValidation(argc, argv);` | `inf` reads official input |
| generator | `registerGen(argc, argv, 1);` | `rnd`, `opt<T>`, stdout or `startTest()` |
| checker | `registerTestlibCmd(argc, argv);` | `inf`, `ans`, `ouf` |
| interactor | `registerInteraction(argc, argv);` | `inf`, `ouf`, `tout`, stdout to contestant |

Rules:

- Prefer `registerGen(argc, argv, 1)` for new generators.
- Use the `argc, argv` variants for validators and checkers if you want `validator.group()`, `validator.testset()`, `checker.group()`, or `checker.testset()`.
- Do not mix registration styles inside one executable.

## 2. Stream Roles

The core streams are:

| Stream | Meaning |
| --- | --- |
| `inf` | official input file or hidden testcase |
| `ans` | jury answer file |
| `ouf` | contestant output stream |
| `tout` | interactor-owned output file for later checking or handoff |

Practical meaning:

- validator: only `inf` matters;
- checker: parse the official testcase from `inf`, jury data from `ans`, and contestant data from `ouf`;
- interactor: read hidden test data from `inf`, read contestant replies from `ouf`, talk to the contestant through standard output, and optionally write transcript or handoff data to `tout`.

## 3. Verdict Helpers

Common result codes:

- `_ok`: accepted
- `_wa`: contestant wrong answer
- `_pe`: presentation error or formatting-type error where the platform distinguishes it
- `_fail`: organizer-side or checker-side failure
- `_points`: points-returning result
- `_pc(x)`: partially-correct result code helper; platform semantics vary

Common quit helpers:

```cpp
quitf(_ok, "message");
quitf(_wa, "expected %d, found %d", ja, pa);
quitif(bad, _wa, "bad contestant output");
expectedButFound(_wa, expected, found);
quitp(points, "score explanation");
quitpi(points_info, "message");
```

Use these rules:

- contestant-side lexical or formatting parse failures on `ouf` -> library-level `_pe`;
- contestant-side semantic or range failures on `ouf` -> usually library-level `_wa`;
- jury answer corruption, impossible hidden state, checker bug, or broken testcase -> `_fail`;
- partial scoring -> prefer `quitp(...)` or `quitpi(...)`, and verify platform expectations before relying on `_pc(...)`.

Important asymmetry:

- parsing failures on `ouf` are contestant-facing;
- parsing failures on `ans` or inconsistent official data are organizer-facing.
- some judges collapse `_pe` into WA in final verdict reporting, but that is a platform choice after `testlib` has already distinguished the cases.

## 4. Named Checks And Assertions

Use the built-in assertion helpers instead of raw `assert`.

```cpp
ensure(x >= 0);
ensuref(l <= r, "l=%d must not exceed r=%d", l, r);
```

Why:

- they integrate with `testlib` verdict discipline;
- they produce readable diagnostics;
- global `ensure` / `ensuref` fail as organizer-side `_fail`;
- stream-specific helpers such as `ouf.ensuref(...)` or `in.quitf(...)` should be used when the verdict must follow the stream.

## 5. Core Read APIs

Most work uses a small set of `InStream` methods.

### Scalar tokens

```cpp
int x = inf.readInt();
int x2 = inf.readInt(1, 1000000, "x");
long long y = inf.readLong(-1'000'000'000LL, 1'000'000'000LL, "y");
unsigned long long z = inf.readUnsignedLong(0ULL, 100ULL, "z");
double a = inf.readReal();
double b = inf.readDouble();
double c = inf.readStrictDouble(0.0, 1.0, 1, 6, "prob");
string s = inf.readToken("[a-z]{1,20}", "s");
string line = inf.readString();
string raw = inf.readLine();
```

Notes:

- `readToken` / `readWord` read one token.
- `readString` and `readLine` read one whole line; in practice the upstream API treats them as aliases.
- `readStrictReal` / `readStrictDouble` are for validators when the exact lexical float format matters, not just numeric range.

### Bulk reads

```cpp
vector<int> a = inf.readInts(n, 0, 1'000'000'000, "a");
vector<long long> b = inf.readLongs(m, -10, 10, "b");
vector<string> rows = inf.readStrings(h, "[.#]{1,2000}", "row");
```

Use bulk reads for simple sequences. Still add explicit structural checks afterward if the sequence must also satisfy permutation, uniqueness, graph, or ordering constraints.

### Whitespace and file boundaries

```cpp
inf.readSpace();
inf.readChar(',');
inf.readEoln();
inf.readEof();
bool no_more = ouf.seekEof();
```

Rules:

- `readSpace()` means exactly one ASCII space, not arbitrary whitespace.
- `readEoln()` means an actual line ending, and in strict validator mode it is OS-sensitive: upstream expects `\r\n` on Windows and `\n` elsewhere unless compile-time overrides are used.
- `readEof()` is mandatory for validators.
- `seekEof()` is useful when reading variable-length contestant output or sequences until exhaustion.

Validator versus checker nuance:

- validators are strict because `registerValidation*` puts `inf` into strict mode;
- checkers are non-strict by default, so token reads skip leading whitespace and `seekEof()` ignores trailing whitespace;
- that is why trailing spaces are usually harmless in contestant output while extra non-whitespace tokens are not;
- successful checker exits already trigger `testlib`'s dirt check for extra non-whitespace after an accepted answer, so checker authors should think in terms of deliberate stream consumption rather than copying validator-style `readEof()` blindly.

## 6. Testcase, Group, And Testset Helpers

Useful helpers:

```cpp
setTestCase(tc);
unsetTestCase();
validator.group();
validator.testset();
checker.group();
checker.testset();
```

Use them when:

- a validator checks multi-test files and you want better diagnostics per case;
- one validator or checker behaves differently for `pretests` versus hidden tests;
- one package needs group-specific restrictions.

Do not use these to hide undocumented scoring policy. They are for explicit package configuration.

## 7. Generator Option APIs

These are generator-specific, but common enough to remember here.

```cpp
int n = opt<int>("n");
int m = opt<int>("m", 0);
int positional = opt<int>(1);
bool dense = has_opt("dense");
suppressEnsureNoUnusedOpts();
```

Notes:

- `opt<T>(key)` reads named options such as `-key value`, `--key value`, `-key=value`, or `--key=value`.
- `opt<T>(index)` reads positional arguments.
- `has_opt(key)` checks whether the flag exists.
- compact short forms like `-n10` and bare boolean flags are also supported by modern upstream `testlib`.
- generator seeds are derived from command-line arguments, so the same command line reproduces the same testcase;
- once you use `has_opt(...)` or a defaulted `opt(...)`, `testlib` can auto-check unused options at finalization time;
- call `suppressEnsureNoUnusedOpts()` only when intentionally accepting extra options.

## 8. Random APIs

Common generator helpers:

```cpp
rnd.next();                  // double in [0, 1)
rnd.next(100);               // in [0, 100)
rnd.next(1, 100);            // in [1, 100]
rnd.next(3.14);              // in [0, 3.14)
rnd.next(1.0, 2.0);          // in [1.0, 2.0)
rnd.next("[a-z]{1,20}");     // random token matching regex-like pattern
rnd.wnext(1, 100, bias);     // biased toward endpoints depending on bias
rnd.perm(n, 1);              // permutation [1..n]
rnd.distinct(k, 1, n);       // k distinct values
rnd.partition(parts, sum);   // random partition of sum
rnd.any(vec);                // random element
rnd.wany(vec, bias);         // biased random element
shuffle(v.begin(), v.end()); // testlib shuffle
```

Do not use:

- `rand()`
- `srand()`
- `random_shuffle()`

The upstream header explicitly rejects these patterns.

## 9. Finalization Guard

The upstream header has finalize guards for common mistakes:

- checker must end with a quit helper;
- validator must end with `readEof()`.

You can call `disableFinalizeGuard()` but should almost never do so in contest packages.

## 10. Compatibility Notes Worth Remembering

- Upstream `testlib.h` version fetched for this skill reported `VERSION "0.9.45"`.
- `registerGen(argc, argv, 1)` is the preferred mode for new generators.
- `quitp(...)` and `quitpi(...)` exist in modern upstream `testlib`, but exact platform handling of partial points still varies.
- `validator.group()`, `validator.testset()`, `checker.group()`, and `checker.testset()` rely on the `argc, argv` registration forms.
- `registerTestlibCmd(argc, argv)` also accepts optional `--testset` and `--group` parameters in modern upstream `testlib`.
- `--testMarkupFileName`, `--testCase`, and `--testCaseFileName` exist for validators in modern upstream `testlib`; they are mostly useful in Polygon-like environments and internal validation workflows.
