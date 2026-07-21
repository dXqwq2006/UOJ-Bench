# Checkers

Checkers judge contestant output semantics.

Use checker mode:

```cpp
registerTestlibCmd(argc, argv);
```

The standard file order is:

```text
checker input.txt output.txt answer.txt
```

Meaning:

- `inf` reads official input;
- `ouf` reads contestant output;
- `ans` reads jury answer.

Upstream `testlib` also skips a UTF-8 BOM at the start of contestant output.

## 1. Checker Contract

A checker should:

- parse contestant output from `ouf`;
- parse jury data from `ans` if needed;
- accept any legal contestant output, not only one canonical witness;
- reject malformed contestant output cleanly;
- report organizer-side inconsistencies as `_fail`;
- consume enough of `ouf` that trailing junk does not silently pass.

It should not:

- enforce one specific construction when many are valid;
- hide scoring policy in ad hoc string comparisons;
- blame the contestant for a broken jury answer.

## 2. Checker Skeleton

```cpp
#include "testlib.h"

int main(int argc, char* argv[]) {
    setName("compare one signed integer");
    registerTestlibCmd(argc, argv);

    int ja = ans.readInt();
    int pa = ouf.readInt();

    if (ja != pa) expectedButFound(_wa, ja, pa);
    quitf(_ok, "answer is %d", ja);
}
```

For anything nontrivial, separate parsing from judging:

```cpp
Answer readAnswer(InStream& in, const Input& input);
bool isValid(const Input& input, const Answer& out, std::string& why);
Score score(const Input& input, const Answer& out);
```

## 3. Verdict Discipline

Use:

- `_wa` for contestant-side semantic failure;
- `_pe` only if the platform meaningfully distinguishes presentation or format errors;
- `_fail` for organizer-side corruption or impossible states.

Examples:

- contestant prints duplicate vertex in a permutation witness -> `_wa`
- contestant prints non-integer token where an integer is required -> usually `_pe` or `_wa` depending on path and platform
- jury answer file is malformed or suboptimal when optimality is assumed -> `_fail`

Modern upstream nuance:

- range or semantic violations on `ouf.readInt(min, max)` now behave as contestant wrong answer rather than organizer failure;
- lexical parse failures still remain formatting-side errors.

Do not design correctness around that distinction. Just keep organizer and contestant blame separate.

## 4. Shared Parsing Pattern

This is the default pattern for constructive outputs.

```cpp
struct Input {
    int n;
};

struct Output {
    std::vector<int> p;
};

Output readOutput(InStream& in, const Input& input, bool jury_side) {
    Output out;
    out.p = in.readInts(input.n, 1, input.n, "p");
    if (!in.seekEof()) in.quitf(jury_side ? _fail : _pe, "extra tokens after permutation");
    return out;
}

void validatePermutation(const Output& out, const Input& input, InStream& in, bool jury_side) {
    std::vector<int> seen(input.n + 1, 0);
    for (int x : out.p) {
        if (++seen[x] != 1) in.quitf(jury_side ? _fail : _wa, "not a permutation");
    }
}
```

Refine the verdict mapping:

- jury-side invalidity -> `_fail`
- contestant-side invalidity -> `_wa`

## 5. Legality Before Scoring

For optimization, approximation, or constructive outputs:

1. parse contestant output;
2. validate witness legality;
3. compute objective;
4. compare or score.

Never award points to illegal output just because some numerical score looks plausible.

## 6. Partial Scoring APIs

Modern upstream `testlib` supports:

```cpp
quitp(points, "message");
quitpi(points_info, "message");
quitf(_pc(score), "message");
```

Recommendations:

- prefer `quitp(...)` or `quitpi(...)` when the judge expects an explicit points value;
- verify target-platform semantics before relying on `_pc(...)`;
- keep points bounded and meaningful.

Example:

```cpp
double score = compute_score(...);
quitp(score, "score=%.6f", score);
```

## 7. Floating-Point Checkers

Use absolute, relative, or combined tolerance deliberately.

Helpful upstream helpers:

- `doubleCompare(a, b, eps)`
- `doubleDelta(a, b)`

Built-in examples in upstream:

- `acmp.cpp`, `rcmp.cpp`: single float, absolute tolerance
- `dcmp.cpp`: absolute or relative tolerance
- `rcmp4.cpp`, `rcmp6.cpp`, `rcmp9.cpp`: sequence float comparisons at fixed tolerances
- `rncmp.cpp`: absolute-tolerance float sequences

Do not invent a tolerance policy casually. State it in the checker name and message.

## 8. Built-In Checker Catalog

The official repository already ships several ready-made checkers. Prefer them when the task truly matches.

| File | Use when | Caveat |
| --- | --- | --- |
| `wcmp.cpp` | compare token sequences | ignores line structure |
| `fcmp.cpp` | compare exact lines | line-based; extra trailing contestant lines are not rejected |
| `lcmp.cpp` | compare token sequences per line | line grouping matters; extra trailing contestant lines are not rejected |
| `ncmp.cpp` | compare ordered integer sequences | whitespace-insensitive |
| `uncmp.cpp` | compare unordered integer multisets | sorts before compare |
| `icmp.cpp` | compare one integer | trivial exact single-value; extra trailing contestant output is accepted |
| `hcmp.cpp` | compare huge integers as strings | one token only; extra trailing contestant output is accepted |
| `yesno.cpp` | one YES/NO answer, case-insensitive | one token only; extra trailing contestant output is accepted |
| `nyesno.cpp` | many YES/NO tokens | sequence form |
| `caseicmp.cpp` | `Case i:` single integer style outputs | case-labelled |
| `casencmp.cpp` | `Case i:` integer sequences | case-labelled |
| `casewcmp.cpp` | `Case i:` token sequences | case-labelled |
| `acmp.cpp`, `rcmp.cpp` | one float with tolerance | fixed EPS; extra trailing contestant output is accepted |
| `dcmp.cpp` | one float with abs/rel tolerance | fixed EPS; extra trailing contestant output is accepted |
| `rcmp4.cpp`, `rcmp6.cpp`, `rcmp9.cpp`, `rncmp.cpp` | float sequences | fixed EPS families; extra trailing contestant output is not rejected |
| `pointscmp.cpp` | scored checker example | example only; adapt to real score semantics |
| `pointsinfo.cpp` | points-info example | platform-specific usefulness |

Prefer a custom checker instead of a built-in one when:

- many witnesses are valid;
- legality needs structural checks;
- objective value must be recomputed;
- score is not a direct token comparison;
- line or protocol rules are problem-specific;
- strict EOF matters but the stock checker only reads a prefix.

## 9. Common Custom Patterns

### Exact value

Use the skeleton above.

### Any valid witness

Parse the contestant witness from `ouf`, validate it structurally, compute its objective, and compare against the optimal jury objective or threshold.

### Multiple-answer constructive task

```cpp
auto pa = readOutput(ouf, input, false);
validate(pa, input, ouf);

int jury_opt = ans.readInt();
int got = objective(pa, input);
if (got != jury_opt) quitf(_wa, "expected objective %d, found %d", jury_opt, got);
quitf(_ok, "objective %d", got);
```

### Optimization with witness + value

If contestant prints both a claimed score and a witness, recompute the score from the witness and ignore the claim unless the statement requires checking consistency.

## 10. Checker Red Flags

Reject drafts with any of these:

- parsing contestant output with raw `cin` instead of `ouf`;
- assuming the jury witness is the only valid witness;
- scoring before legality;
- no explicit handling of extra contestant tokens;
- `_wa` on broken jury data;
- hidden dependence on whitespace trivia that the statement does not care about.
