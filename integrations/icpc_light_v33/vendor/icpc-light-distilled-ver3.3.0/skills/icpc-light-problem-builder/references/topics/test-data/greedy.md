# Greedy

## Topic-Specific Variants

- Equal local score, different future feasibility.
- Locally worse choice unlocks a suffix unavailable to the locally best choice.
- Several optimal prefixes, only one extendable to a global optimum.
- Neutral padding that hides a small exchange-counterexample gadget.

## Strong Adversarial Solutions

- The exchange argument silently assumes local ties are interchangeable.
- Local repair after a greedy choice is believed to recover any missed optimum.
- The greedy score omits a scarce resource consumed only in a later phase.
- A plausible ordering key is sufficient because witnesses without delayed effects pass.

## Killer Constructions

- Make two first choices locally tied, then attach a suffix that accepts only one.
- Hide a tiny delayed-reward gadget among many neutral choices.
- Compose two independent-looking greedy gadgets that compete for one resource.
- Scale by concatenating neutral blocks, not by repeating the same visible witness.

- Greedy plus local repair: make the wrong early choice require a global swap, not a one- or two-exchange repair.
- Portfolio of sort keys: build ties under all common keys, then distinguish by a hidden resource consumed later.
- Exact fallback for small counterexamples: inflate the minimal gadget with neutral padding and repeated decoys.
- Feasibility self-check: make the heuristic produce a legal but suboptimal answer, not an invalid one.

- Branch-and-bound around greedy incumbent: make the greedy answer close enough that bounds stay loose.
- Try-all-first-choice then greedy: use many locally tied first choices, only one of which is globally extendable.
- Local search over swaps: require a long alternating cycle of swaps before any single move improves the score.

- **Delayed scarce resource**: create items with equal immediate profit; only one preserves a resource needed by a suffix item with huge profit. Neutral items should have the same local score to hide the gadget.
- **Exchange-cycle trap**: build choices `A1,B1,A2,B2,...` where replacing any one choice is not profitable, but replacing the whole alternating cycle is required.
- **Legal-suboptimal witness**: ensure every greedy output satisfies constraints; compare objective against an oracle. This catches patched WAs that added validators or repair.
- **Sort-key portfolio trap**: for keys `(deadline, duration, profit, ratio)`, create groups equal on three keys and misleading on the fourth; permute labels so each portfolio mode picks a different wrong representative.
