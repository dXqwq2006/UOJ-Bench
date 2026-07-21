# Generators

Generators create candidate tests. They do not certify legality by themselves.

Use generator mode:

```cpp
registerGen(argc, argv, 1);
```

For new code, prefer version `1`.

## 1. Generator Skeleton

```cpp
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int n = opt<int>("n");
    int max_a = opt<int>("max-a", 1000000000);

    println(n);
    for (int i = 0; i < n; ++i) {
        if (i) std::cout << ' ';
        std::cout << rnd.next(0, max_a);
    }
    std::cout << '\n';
}
```

Generator rules:

1. all randomness must come from `rnd`;
2. all parameters must come from command-line options or fixed constants;
3. every promoted generated test must be reproducible;
4. validator still runs after generation.

## 2. Option APIs

Named options:

```cpp
int n = opt<int>("n");
int m = opt<int>("m", 0);
bool dense = has_opt("dense");
std::string mode = opt<std::string>("mode", "random");
```

Positional options:

```cpp
int n = opt<int>(1);
int bias = opt<int>(2);
```

Important notes:

- `opt<T>(key, default)` and `has_opt(key)` can trigger automatic unused-option checks;
- if you intentionally accept extra options, call `suppressEnsureNoUnusedOpts()`, but only with a clear reason;
- prefer named options for reusable package generators.

## 3. Random API Inventory

These are the common high-value generators.

### Uniform primitives

```cpp
rnd.next();            // double in [0, 1)
rnd.next(100);         // [0, 100)
rnd.next(1, 100);      // [1, 100]
rnd.next(1.0, 2.0);    // [1.0, 2.0)
rnd.next("[a-z]{1,20}");
```

### Biased randomness

```cpp
rnd.wnext(1, 100, bias);
```

Interpretation:

- positive `bias` favors larger values;
- negative `bias` favors smaller values;
- `0` is unbiased.

Use `wnext` when the family deliberately targets extremes or near-extremes.

### Ready-made combinators

```cpp
auto p = rnd.perm(n, 1);
auto xs = rnd.distinct(k, 1, n);
auto parts = rnd.partition(groups, sum, min_part);
int x = rnd.any(vec);
int y = rnd.wany(vec, bias);
shuffle(v.begin(), v.end());
```

These are much better than writing ad hoc random loops badly.

## 4. Common Generator Patterns

### Single random scalar

```cpp
println(rnd.next(1, 1000000));
```

### Random array

```cpp
int n = opt<int>("n");
println(n);
for (int i = 0; i < n; ++i) {
    if (i) std::cout << ' ';
    std::cout << rnd.next(-1000, 1000);
}
std::cout << '\n';
```

### Random permutation

```cpp
int n = opt<int>("n");
println(n);
println(rnd.perm(n, 1));
```

### Random tree

```cpp
int n = opt<int>("n");
int bias = opt<int>("bias", 0);

std::vector<int> parent(n);
for (int i = 1; i < n; ++i) parent[i] = rnd.wnext(i, bias);

std::vector<int> perm = rnd.perm(n);
std::vector<std::pair<int, int>> edges;
for (int i = 1; i < n; ++i) {
    edges.push_back({perm[i], perm[parent[i]]});
}
shuffle(edges.begin(), edges.end());

println(n);
for (auto [u, v] : edges) println(u + 1, v + 1);
```

### Multi-case array pack

```cpp
int t = opt<int>("test-count");
int sum_n = opt<int>("sum-n");
int min_n = opt<int>("min-n", 1);

std::vector<int> ns = rnd.partition(t, sum_n, min_n);

println(t);
for (int n : ns) {
    println(n);
    std::vector<int> a(n);
    for (int& x : a) x = rnd.next(1, 1000000000);
    println(a);
}
```

This mirrors the official upstream sample style.

Remember:

- `rnd.partition(...)` returns an unsorted partition;
- impossible requests such as `min_n * t > sum_n` fail immediately.

## 5. Multi-File Generators

`testlib` also supports multi-file generators via `startTest(test_id)`.

Example:

```cpp
void writeTest(int tc) {
    startTest(tc);
    println(rnd.next(1, tc * tc), rnd.next(1, tc * tc));
}

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    for (int tc = 1; tc <= 10; ++tc) writeTest(tc);
}
```

This is supported in Polygon, but stdout-style generators are usually easier to reason about and script.

## 6. Family Design Rules

Prefer:

- one generator per genuinely different family;
- one parameterized generator when only sizes or numeric knobs change;
- explicit family names and modes;
- deterministic promotion of important cases.

Do not write one giant generator with undocumented branches if several smaller binaries are clearer.

## 7. Reproducibility Rules

Every important generated family should have:

- a generator name;
- a parameter list;
- a seed or deterministic procedure;
- a validator pass;
- a stated job such as `max`, `pathological-small`, `dense`, `duplicate-heavy`, `anti-greedy`.

When a stress loop finds a valuable case:

- minimize it;
- save the minimized testcase itself if possible;
- optionally also save the generating command and seed.

## 8. Generator Red Flags

Reject drafts with any of these:

- `rand`, `srand`, or `random_shuffle`;
- hidden dependence on system time;
- no validator pass after generation;
- undocumented option names;
- unused options accidentally ignored;
- generator code trying to act as validator;
- giant random mode with no family naming.

## 9. Recommended Local Loop

Use this loop during development:

```bash
./gen -n 100 -m 200 17 > test.in
./validator < test.in
./model < test.in > answer.txt
./wrong < test.in > out.txt || true
```

Promote only cases you can reproduce.

Important nuance:

- upstream `testlib` does not provide a built-in `-seed` flag;
- reproducibility comes from the whole command line hash;
- if you want an explicit `seed` option, define and document it in your generator instead of assuming it exists.
