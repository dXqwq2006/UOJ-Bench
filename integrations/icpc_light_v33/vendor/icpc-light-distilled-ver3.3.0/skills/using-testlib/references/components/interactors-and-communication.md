# Interactors And Communication

Interactive and communication tasks are protocol engineering tasks.

Use interactor mode:

```cpp
registerInteraction(argc, argv);
```

In official upstream `testlib`:

- `inf` reads hidden input;
- `ouf` reads contestant replies from the interactor's stdin;
- stdout is how the interactor talks to the contestant;
- `tout` is the output-file path passed as the second command-line argument and is meant for transcript or handoff data.

## 1. Interactor Skeleton

```cpp
#include "testlib.h"
#include <iostream>

int main(int argc, char* argv[]) {
    setName("interactive sample");
    registerInteraction(argc, argv);

    int n = inf.readInt();
    std::cout << n << std::endl;  // endl flushes

    int reply = ouf.readInt();
    tout << reply << '\n';

    quitf(_ok, "received one reply");
}
```

The key point is not the syntax. The key point is:

- every judge message that expects a reply must flush;
- contestant traffic stays on stdout/stdin;
- logging or handoff state belongs in `tout`, not stdout.

## 2. Protocol Rules

Make these explicit in code:

- initial message or lack of one;
- command grammar;
- query budget;
- phase transitions;
- terminal condition;
- invalid-command handling;
- post-terminal behavior.

A good interactor is a state machine, not scattered booleans.

## 3. Flush Discipline

Flush every exchange that expects a contestant response.

Use one of:

```cpp
std::cout << x << ' ' << y << std::endl;
std::cout << x << ' ' << y << '\n' << std::flush;
```

Do not:

- print debug text to stdout;
- rely on platform buffering quirks;
- wait for contestant output before sending the required prompt.

Upstream `testlib` used to disable some buffers automatically for interactive problems, but that behavior was removed long ago. Flush deliberately.

## 4. Verdict Discipline

Use these default meanings:

- malformed contestant command -> `_wa`
- illegal query or budget violation -> `_wa`
- contestant silence / premature EOF -> contestant failure according to platform policy
- organizer-side impossible state or inconsistent hidden input -> `_fail`

Important upstream fact:

- `testlib`'s finalize guard does not enforce interactor end-of-stream discipline the way it does for validators and checkers.

Inference:

- if your interactor must reject extra tokens, missing terminal replies, or post-terminal chatter, you must implement that explicitly.

## 5. `tout` And Run-Twice Handoff

`tout` is the right place for:

- transcript logging;
- first-run state handoff;
- summary data for a later checker.

Do not leave handoff semantics implicit.

For communication or run-twice tasks, document:

- who writes the handoff;
- who reads it;
- exact format;
- whether extra data is forbidden;
- how runs are keyed if testcase order may change.

Treat handoff as a serialized artifact, not as "memory that survives".

## 6. Communication / Run-Twice Rules

If the same submission runs twice:

- assume memory does not persist;
- assume testcase order may differ between runs unless the platform guarantees otherwise;
- make phase markers explicit;
- validate the handoff before consuming it;
- keep final semantic judgment centralized, usually in the checker or final phase.

Do not key handoff data only by testcase position unless the task explicitly guarantees stable ordering.

## 7. Common Interactor Patterns

### Query-response protocol

```cpp
int q = 0;
while (true) {
    std::string cmd = ouf.readToken();
    if (cmd == "?") {
        ++q;
        if (q > LIMIT) quitf(_wa, "too many queries");

        int i = ouf.readInt(1, n, "i");
        int ans = hidden_value(i);
        std::cout << ans << std::endl;
    } else if (cmd == "!") {
        int guess = ouf.readInt();
        if (guess == secret) quitf(_ok, "correct");
        quitf(_wa, "wrong final answer");
    } else {
        quitf(_wa, "unknown command '%s'", compress(cmd).c_str());
    }
}
```

### Transcript logging

```cpp
tout << "query " << i << " -> " << ans << '\n';
```

Use transcript logs when debugging or when a later checker needs structured evidence.

## 8. Local Testing

Use a runner that wires judge and contestant pipes together.

Typical pattern:

```bash
python3 interactive_runner.py ./interactor test.in transcript.out -- ./solution
```

Keep in mind:

- local tools are good for protocol debugging;
- they are not necessarily identical to the target judge;
- do not treat one local happy-path run as release proof.

## 9. Interactor Red Flags

Reject drafts with any of these:

- no flush before expected reply;
- debug output on stdout;
- no explicit budget enforcement;
- no clear terminal state;
- `tout` format undocumented;
- communication task assuming stable testcase order across runs;
- interactor relying on checker to catch protocol mistakes it should reject itself.
