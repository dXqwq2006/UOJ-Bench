# Review Checklists

Use this file after implementation and before calling a `testlib` artifact done.

## 1. Cross-Cutting Checklist

- [ ] the file type is explicit: validator, generator, checker, interactor, grader, stub, or header
- [ ] the chosen registration function matches the file type
- [ ] the file reads only the streams it is supposed to own
- [ ] contestant mistakes and organizer mistakes go to different verdict paths
- [ ] local compile and run commands exist
- [ ] at least one hostile probe exists for the trust boundary

## 2. Validator Checklist

- [ ] uses `registerValidation(argc, argv)` unless the no-argv form is truly sufficient
- [ ] every important field has a named bounded read
- [ ] structural promises are checked with `ensuref`
- [ ] line and space rules match the statement
- [ ] multi-case inputs use `setTestCase(tc)` if debugging value is high
- [ ] ends with `inf.readEof()`
- [ ] negative probes cover bounds, separators, structure, and trailing garbage

Red flags:

- `cin`-style loose parsing
- missing EOF check
- generator assumptions used instead of executable validation

## 3. Generator Checklist

- [ ] uses `registerGen(argc, argv, 1)`
- [ ] all randomness comes from `rnd`
- [ ] option names and defaults are documented in code comments or task scripts
- [ ] output is validator-clean
- [ ] important families are reproducible from command line plus seed
- [ ] no accidental unused-option typos remain
- [ ] no `rand`, `srand`, or `random_shuffle`

Red flags:

- wall-clock randomness
- giant undocumented mode switch
- no validator pass after generation

## 4. Checker Checklist

- [ ] uses `registerTestlibCmd(argc, argv)`
- [ ] consumes contestant output strictly enough for the contract
- [ ] accepts all legal witnesses, not only the jury witness
- [ ] checks legality before scoring
- [ ] organizer-side corruption yields `_fail`
- [ ] contestant-side malformed or illegal output yields `_wa` or `_pe`
- [ ] built-in stock checker is used only if the contract matches it exactly

Red flags:

- checker judges formatting trivia instead of semantics
- extra contestant output silently passes when it should not
- single-token stock checker used where strict EOF is required

## 5. Interactor Checklist

- [ ] uses `registerInteraction(argc, argv)`
- [ ] protocol is an explicit state machine
- [ ] every judge message that expects a reply flushes
- [ ] invalid command handling is explicit
- [ ] budget enforcement is exact
- [ ] `tout` ownership and format are documented
- [ ] post-terminal behavior is explicit
- [ ] local replay command exists

Red flags:

- debug text on stdout
- no flush
- communication handoff keyed only by testcase position
- assuming checker will catch protocol mistakes the interactor should catch

## 6. Grader / Stub / Header Checklist

- [ ] statement, header, stub, and hidden grader agree on the API
- [ ] hidden grader owns input parsing and contestant function calls
- [ ] public stub is compileable and free of hidden logic
- [ ] repeated-init and multi-test process behavior are checked when relevant
- [ ] checker is used if output semantics are nontrivial
- [ ] contestant can compile and run a local sample package

Red flags:

- signature mismatch between public and hidden code
- hidden grader semantics that should live in a checker
- undocumented global-state assumptions

## 7. Release-Gate Questions

Ask these before handoff:

1. Can I explain exactly what this file reads and writes?
2. If it fails, do I know which side gets blamed and why?
3. Can I rerun the same behavior locally with one command?
4. Does at least one hostile probe exercise the trust boundary?
5. Is there any platform-specific assumption that still lives only in my head?
