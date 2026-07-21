# Arrays

## Topic-Specific Variants

- Same multiset, different order: answer changes only because positions matter.
- Same rank pattern, different gaps: answer changes only because magnitudes matter.
- Clean global pattern with one defect: monotone, periodic, or blockwise arrays where one local break flips the proof condition.
- Large tie block with one future-distinguishing element, not merely "duplicates exist".

## Strong Adversarial Solutions

- The solver sorts or buckets values even though position-dependent structure survives.
- The solver keeps only rank/compressed value when absolute gaps affect transitions.
- A two-pointer or monotone-frontier proof is used where one constructed defect reverses the frontier.
- Equal local choices are treated as interchangeable but lead to different suffix behavior.

## Killer Constructions

- Pair cases with identical multiset and different answers.
- Pair cases with identical ranks and different answers.
- Hide a one-element defect inside long regular blocks.
- Make the decisive element locally tied with many decoys, then distinguish it later.

- Exact-on-small plus greedy/two-pointer on large: put the first failure just above the exact cutoff.
- Suspicious-case fallbacks for all-equal or sorted arrays: use almost-sorted blocks with one late inversion, not fully sorted data.
- Portfolio tie rules: make every local rule see the same prefix score, then separate them by a suffix-only resource.
- Coordinate-compressed shortcuts: preserve compressed ranks while changing actual gaps that affect costs.

- Quadratic pair scans with early break: use long plateaus where every prefix looks promising and the contradiction is at the last element.
- Divide-and-check bruteforces with pruning by current best: make many candidates tie the incumbent until a final suffix.
- Mo/sqrt shortcuts with tiny constants: put all queries on block boundaries and alternate between far-left and far-right endpoints.
- Meet-in-the-middle with lossy dedup: make many half summaries equal under the kept key but different under the hidden continuation.

- **Same multiset, different answer pair**: choose two blocks `A` and `B` with the same values; emit `A B` and `interleave(A, B)`. Put the decisive query/range across the boundary so sorting-based or multiset-only logic cannot distinguish them.
- **Rank-equal gap trap**: generate ranks `1..n` twice. Map one case to values `i`, the other to `10^9 - 2^i mod M` or to alternating huge/small gaps. Use when transition cost depends on absolute differences.
- **Late inversion monotone breaker**: start with `1,2,...,n`, replace the middle by a three-value plateau `x,x,x`, then swap the last two elements. Local monotonicity checks pass on most windows; global two-pointer/frontier assumptions fail.
- **Boundary-query sqrt killer**: for block size `B`, create queries `[kB, kB+B-1]`, `[kB+1, (k+2)B]`, then alternate update/query touching both adjacent blocks. This forces rebuild/push work without looking random.
