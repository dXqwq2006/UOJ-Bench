# Graders And Public Interfaces

Grader tasks are normal tasks with a narrower contestant-visible API.

The file types here are:

- public header;
- public stub or template;
- hidden grader or driver;
- optional checker after grader output.

## 1. Boundary Rules

Keep these responsibilities separate.

| File | Owns |
| --- | --- |
| public header | function names, types, constants, visible API |
| public stub / template | compileable contestant skeleton |
| hidden grader | official input parsing, contestant function calls, output serialization |
| checker | semantic judgment if the grader's printed output still needs custom validation |

Do not hide intended-solution logic in the public header or stub.

## 2. Minimal Example

### Public header

```cpp
#pragma once
#include <vector>

int solve_instance(int n, const std::vector<int>& a);
```

### Public stub

```cpp
#include "grader.h"

int solve_instance(int n, const std::vector<int>& a) {
    return 0;
}
```

### Hidden grader

```cpp
#include "grader.h"
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);

    int n;
    cin >> n;
    vector<int> a(n);
    for (int i = 0; i < n; ++i) cin >> a[i];

    int answer = solve_instance(n, a);
    cout << answer << '\n';
}
```

If output semantics are nontrivial, follow grader execution with a checker instead of burying the semantics inside the grader.

## 3. What Must Match Exactly

The statement, public header, public stub, and hidden grader must agree on:

- function names;
- argument types;
- return types;
- index base;
- ownership and mutability;
- whether multiple calls occur in one process;
- reset semantics between testcases;
- callback or side-effect rules.

If any one of these differs, the package boundary is wrong even if the model solution still passes locally.

## 4. Local Harness Expectations

A contestant should be able to:

- compile the public package;
- see exactly how the grader calls the API;
- run at least one sample locally.

Typical compile:

```bash
g++ -std=c++17 -O2 -Wall -Wextra -I. grader.cpp contestant.cpp -o run
```

Typical run:

```bash
./run < input.txt > output.txt
```

Then optionally:

```bash
./checker input.txt output.txt answer.txt
```

## 5. Grader-Specific Probe Matrix

At minimum, test:

- repeated initialization in one process;
- multiple testcases in one process if the platform uses that model;
- invalid or edge return values;
- cross-language signature parity if several languages are supported;
- adversarial call order if the interface allows repeated calls.

These are high-value because many grader bugs are invisible on one-shot happy-path samples.

## 6. Grader Red Flags

Reject drafts with any of these:

- public stub differs from actual grader signature;
- grader silently patches contestant mistakes;
- hidden grader depends on undocumented global state;
- checker logic embedded in grader without need;
- no sample compile/run path for contestants.
