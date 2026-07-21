# Templates

These are starter scaffolds, not mandatory final forms. Copy the smallest template that matches the current file type, then specialize it.

## 1. Minimal Validator

```cpp
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    int n = inf.readInt(1, 200000, "n");
    inf.readEoln();

    std::vector<int> a = inf.readInts(n, -1000000000, 1000000000, "a");
    inf.readEoln();

    inf.readEof();
}
```

## 2. Multi-Test Validator

```cpp
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    int t = inf.readInt(1, 10000, "t");
    inf.readEoln();

    long long sum_n = 0;
    for (int tc = 1; tc <= t; ++tc) {
        setTestCase(tc);

        int n = inf.readInt(1, 200000, "n");
        inf.readEoln();
        sum_n += n;
        ensuref(sum_n <= 200000, "sum of n exceeds limit");

        inf.readInts(n, 0, 1000000000, "a");
        inf.readEoln();
    }

    inf.readEof();
}
```

## 3. Minimal Generator

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

## 4. Family Generator With Bias

```cpp
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int n = opt<int>("n");
    int bias = opt<int>("bias", 0);

    println(n);
    for (int i = 0; i < n; ++i) {
        if (i) std::cout << ' ';
        std::cout << rnd.wnext(1, 1000000000, bias);
    }
    std::cout << '\n';
}
```

## 5. Minimal Exact Checker

```cpp
#include "testlib.h"

int main(int argc, char* argv[]) {
    setName("compare one integer");
    registerTestlibCmd(argc, argv);

    int ja = ans.readInt();
    int pa = ouf.readInt();

    if (!ouf.seekEof()) quitf(_pe, "extra tokens in contestant output");
    if (ja != pa) expectedButFound(_wa, ja, pa);

    quitf(_ok, "answer is %d", ja);
}
```

## 6. Custom Checker With Shared Parser

```cpp
#include "testlib.h"
#include <vector>

struct Input {
    int n;
};

struct Output {
    std::vector<int> p;
};

Input readInput() {
    Input in;
    in.n = inf.readInt();
    return in;
}

Output readOutput(InStream& in, const Input& input, TResult bad) {
    Output out;
    out.p = in.readInts(input.n, 1, input.n, "p");
    if (!in.seekEof()) in.quitf(bad == _fail ? _fail : _pe, "extra tokens after permutation");

    std::vector<int> seen(input.n + 1, 0);
    for (int x : out.p) {
        if (++seen[x] != 1) in.quitf(bad, "not a permutation");
    }
    return out;
}

int objective(const Output& out) {
    int score = 0;
    for (int x : out.p) score += x;
    return score;
}

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);

    Input input = readInput();
    int jury_best = ans.readInt();
    if (!ans.seekEof()) ans.quitf(_fail, "extra tokens in jury answer");
    Output participant = readOutput(ouf, input, _wa);

    int got = objective(participant);
    if (got != jury_best) quitf(_wa, "expected %d, found %d", jury_best, got);
    quitf(_ok, "objective %d", got);
}
```

## 7. Scored Checker

```cpp
#include "testlib.h"
#include <algorithm>

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);

    double jury = ans.readDouble();
    double part = ouf.readDouble();
    if (!ouf.seekEof()) quitf(_pe, "extra output");

    double score = std::max(0.0, 100.0 - std::abs(jury - part));
    quitp(score, "jury=%.6f participant=%.6f", jury, part);
}
```

Adapt the score scale to the target judge.

## 8. Minimal Interactor

```cpp
#include "testlib.h"
#include <iostream>

int main(int argc, char* argv[]) {
    setName("minimal interactor");
    registerInteraction(argc, argv);

    int n = inf.readInt();
    std::cout << n << std::endl;

    int reply = ouf.readInt();
    tout << reply << '\n';

    quitf(_ok, "received one reply");
}
```

## 9. Budgeted Interactive State Machine

```cpp
#include "testlib.h"
#include <iostream>

int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);

    const int n = inf.readInt();
    const int secret = inf.readInt();
    const int LIMIT = 25;

    int used = 0;
    std::cout << n << std::endl;

    while (true) {
        std::string cmd = ouf.readToken();

        if (cmd == "?") {
            ++used;
            if (used > LIMIT) quitf(_wa, "query limit exceeded");

            int x = ouf.readInt(1, n, "x");
            int answ = (x <= secret ? 1 : 0);
            tout << "? " << x << " -> " << answ << '\n';
            std::cout << answ << std::endl;
        } else if (cmd == "!") {
            int guess = ouf.readInt(1, n, "guess");
            if (guess == secret) quitf(_ok, "correct in %d queries", used);
            quitf(_wa, "wrong final answer");
        } else {
            quitf(_wa, "unknown command '%s'", compress(cmd).c_str());
        }
    }
}
```

## 10. Public Header + Stub + Grader

### `grader.h`

```cpp
#pragma once
#include <vector>

int solve_instance(int n, const std::vector<int>& a);
```

### contestant stub

```cpp
#include "grader.h"

int solve_instance(int n, const std::vector<int>& a) {
    return 0;
}
```

### hidden grader

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

    cout << solve_instance(n, a) << '\n';
}
```
