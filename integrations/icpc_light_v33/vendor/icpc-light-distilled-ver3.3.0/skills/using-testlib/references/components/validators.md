# Validators

Validators define the legal input world.

Their job is narrow and strict:

- read exactly the promised input format;
- enforce all hidden structural promises that official tests rely on;
- reject malformed separators, bad counts, bad bounds, and trailing garbage;
- never assign contestant verdicts.

Use validator mode:

```cpp
registerValidation(argc, argv);
```

## 1. Validator Skeleton

```cpp
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    int n = inf.readInt(1, 200000, "n");
    inf.readSpace();
    int m = inf.readInt(0, 200000, "m");
    inf.readEoln();

    std::vector<int> a = inf.readInts(n, -1000000000, 1000000000, "a");
    inf.readEoln();

    ensuref(m <= n - 1, "m must be at most n - 1");

    inf.readEof();
}
```

Keep this shape:

1. register;
2. read exact format with names and bounds;
3. enforce cross-field constraints with `ensuref`;
4. end with `inf.readEof()`.

## 2. Strict Parsing Rules

Validator mode is strict. That matters.

- `readSpace()` means one literal ASCII space.
- `readEoln()` means an actual end-of-line marker.
- `readEof()` means exact file end at the current position.
- bulk reads such as `readInts(...)` still obey strict separator handling in validators.

Use strict reads when the statement promises strict structure.

Examples:

```cpp
int n = inf.readInt(1, 100, "n");
inf.readEoln();
std::string s = inf.readToken("[a-z]{1,100}", "s");
inf.readEoln();
inf.readEof();
```

Do not parse loosely with `cin`, `getline`, `stringstream`, or `stoi` inside validators unless the format truly demands a custom parse that `testlib` cannot express cleanly.

## 3. Common Read Patterns

### One token

```cpp
inf.readToken("[a-z]{1,20}", "s");
inf.readEoln();
inf.readEof();
```

### One line with several scalars

```cpp
int n = inf.readInt(1, 200000, "n");
inf.readSpace();
int q = inf.readInt(1, 200000, "q");
inf.readEoln();
```

### One array line

```cpp
int n = inf.readInt(1, 200000, "n");
inf.readEoln();
std::vector<int> a = inf.readInts(n, 0, 1000000000, "a");
inf.readEoln();
```

### Multiple testcases

```cpp
int t = inf.readInt(1, 10000, "t");
inf.readEoln();

long long sum_n = 0;
for (int tc = 1; tc <= t; ++tc) {
    setTestCase(tc);

    int n = inf.readInt(1, 200000, "n");
    inf.readEoln();
    sum_n += n;
    ensuref(sum_n <= 200000, "sum of n exceeds limit");

    inf.readInts(n, -1000, 1000, "a");
    inf.readEoln();
}

inf.readEof();
```

Use `setTestCase(tc)` in multi-case validators when debugging value.

## 4. Structural Checks

Range checks belong in read calls. Structural checks belong in `ensuref`.

Examples:

### Permutation

```cpp
std::vector<int> p = inf.readInts(n, 1, n, "p");
inf.readEoln();

std::vector<int> seen(n + 1, 0);
for (int x : p) {
    ensuref(++seen[x] == 1, "p must be a permutation");
}
```

### Simple undirected graph

```cpp
std::set<std::pair<int, int>> edges;
for (int i = 0; i < m; ++i) {
    int u = inf.readInt(1, n, "u_i");
    inf.readSpace();
    int v = inf.readInt(1, n, "v_i");
    inf.readEoln();

    ensuref(u != v, "graph must not contain loops");
    ensuref(!edges.count({u, v}), "graph must not contain duplicate edges");

    edges.insert({u, v});
    edges.insert({v, u});
}
```

### Tree

Read edges, reject loops and duplicates, and check acyclicity / connectivity with DSU.

### Grid

```cpp
int h = inf.readInt(1, 2000, "h");
inf.readSpace();
int w = inf.readInt(1, 2000, "w");
inf.readEoln();

for (int i = 0; i < h; ++i) {
    std::string row = inf.readToken("[.#]{" + vtos(w) + "}", "row");
    inf.readEoln();
}
inf.readEof();
```

Use regex patterns when the shape is lexical. Use `ensuref` for semantic conditions across rows.

## 5. Floating-Point Validators

If input contains floating literals and the lexical form matters, use strict reads:

```cpp
double x = inf.readStrictDouble(-1e9, 1e9, 1, 6, "x");
```

Use strict float reads when:

- the statement fixes digits after the decimal point;
- scientific notation is forbidden;
- lexical normalization matters.

If only numeric range matters and several lexical forms are acceptable, use `readDouble` or `readReal`.

## 6. Group And Testset Awareness

Modern upstream `testlib` supports:

```cpp
std::string g = validator.group();
std::string s = validator.testset();
```

Typical use:

```cpp
int n;
if (validator.testset() == "pretests") {
    n = inf.readInt(1, 1000, "n");
} else {
    n = inf.readInt(1, 200000, "n");
}

if (validator.group() == "even-only") {
    ensuref(n % 2 == 0, "n must be even in this group");
}
```

Use this only for explicit package configuration, not hidden behavior.

## 7. Useful Optional Validator Features

Modern upstream `testlib` also supports:

- `--testOverviewLogFileName`
- `--testMarkupFileName`
- `--testCase`
- `--testCaseFileName`

These are mainly useful in Polygon-like workflows and internal validation runs. They are optional, not core validator logic.

## 8. Validator Red Flags

Reject drafts with any of these:

- missing `inf.readEof()`;
- unnamed important fields;
- loose whitespace parsing when the statement is line-sensitive;
- range checks implemented only with plain `if` and no `testlib` diagnostics;
- hidden structural promises left unchecked;
- generator assumptions treated as proof that the input is legal.

## 9. Minimal Examples

### Scalar validator

```cpp
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);
    inf.readInt(1, 100, "n");
    inf.readEoln();
    inf.readEof();
}
```

### String validator

```cpp
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);
    inf.readToken("[a-z]{1,100}", "s");
    inf.readEoln();
    inf.readEof();
}
```

### Graph validator

Use the graph example above and add DSU if acyclicity or connectivity matters.
