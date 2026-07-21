# DP Optimizations

## Topic-Specific Variants

- Fake D&C decision monotonicity: a long prefix where `opt[i]` moves right by `0/1`, then one row or block where the true best split jumps left, then a suffix that becomes friendly again.
- Fake D&C bounded-window monotonicity: the solver scans only `[last_opt-B, last_opt+B]`; make the true split drift outside the window without looking suspicious until the first missed row.
- Fake D&C local-spot-check monotonicity: all sampled rows are monotone, but an unsampled dense block has `opt` sequence like `0, 1, 2, 1, 3, 4`.
- Fake D&C tie monotonicity: many splits have equal value, and the solver's tie policy chooses a monotone representative while the continuation requires the other representative.
- Knuth / quadrangle inequality false positive: one interval layer violates `opt[l][r-1] <= opt[l][r] <= opt[l+1][r]` while adjacent lengths look valid.
- Knuth short-exact, long-window failure: `len <= B` passes full scan, but the first `len = B+1` interval has its best split just outside the widened Knuth window.
- Local anti-Monge block: a `2x2` or `3x3` cost rectangle violates the quadrangle inequality, embedded inside a cost matrix that otherwise looks Monge.
- Interval-cost boundary mismatch: the intended theorem holds for a redefined cost with illegal states set to `INF`, but the adversarial solution applies it to the raw cost.
- CHT sorted-slope failure: the queue hull assumes inserted slopes are monotone; insert one late line whose slope belongs in the middle and wins at a later query.
- CHT sorted-query failure: queries are mostly increasing, then jump back across an old intersection so a popped line should become optimal again.
- CHT convexity/order failure: line intersection order flips because the recurrence is not actually convex; the deque deletes the future winner.
- CHT equal-slope and near-parallel failure: same slope with different intercepts, or slope difference `1` with huge intercepts, exposes bad domination and `double` intersection comparisons.
- CHT tie-boundary failure: query exactly at an intersection where the chosen line affects a secondary state such as count, lexicographic witness, or next transition.
- Aliens trick / WQS convexity failure: the value as a function of item count is not convex/concave, so binary search on penalty returns the wrong side of a gap.
- Aliens trick count-monotonicity failure: `count(lambda)` has a plateau or tie-dependent jump; two runs with different tie policy produce the same penalized value and different counts.
- Aliens trick recovery failure: the final `ans + lambda*k` formula is applied at a critical layer where no solution with exactly `k` is represented by the chosen penalized optimum.
- Sparse-transition shortcut failure: random data has few reachable transitions, but a dense block makes every state transition to many future states.
- Bitset DP shortcut failure: bitset shifts/ORs are treated as near-constant; force dense words, word-boundary churn around `63/64/65`, and many independent bitsets.
- Hard-to-hack TLE brute force: a correct `O(n^2)` / `O(n^3)` DP with good pruning and cache locality survives normal tests; require full-size cases where pruning never fires, not only one huge random case.
- Cutoff brute force: exact for `n <= K`, heuristic or optimized branch for larger cases; attack `K+1` with the smallest instance that enters the risky branch.

## Strong Adversarial Solutions

- Solutions that apply an optimization theorem from empirical monotonicity.
- Solutions whose cost function looks Monge/convex but is only "almost" valid or only valid on random data.
- Solutions that narrow the search interval by previous optima that are not actually bounding.
- Hull implementations that rely on sorted slopes or sorted queries that the problem does not provide.
- Solutions that assume equal values, equal slopes, repeated queries, or repeated counts are rare and harmless.
- Recurrences that are correct, but whose optimizer keeps the wrong witness under ties.
- Solutions that use a theorem true only in a restricted regime, then cross the regime boundary.
- Local-check optimizers that only validate short intervals, prefixes, random samples, or suspicious-looking rows.
- Solutions with a slow exact fallback whose generator never hits the route that should trigger it.
- Bitset, sparse-state, or small-constant brute force solutions assumed to be fast enough under the official limits.

## Killer Constructions

- Generate the smallest violating Monge rectangle, then pad it with regular rows/columns.
- Put the first monotonicity violation just after a clean prefix.
- For CHT, force an intersection-order inversion under the actual query order.
- Compare against unoptimized DP on structured violation seeds, not only random seeds.
- Log and compare the exact `opt` trace, not just the final DP value; many wrong optimizers return the right value until the next layer uses the wrong witness.
- Plant a benign shell plus malignant core: most rows/intervals satisfy the theorem, while one short dense core violates it.
- Use plateaus deliberately: all-equal costs, alternating close weights, mirrored prefix/suffix, and one small asymmetry.
- Place the bad row at a cutoff boundary: first row after a D&C warmup, first interval longer than the exact branch, or first query after a monotone batch.
- Duplicate the bad gadget several times if one violation can be masked by a lucky tie or fallback.
- For Knuth/QI, shrink to the first interval where either the inequality fails or the chosen split leaves `[opt[l][r-1], opt[l+1][r]]`.
- For CHT, combine equal slopes, query backtracking, and integer-boundary intersections; use large coordinates to expose precision and overflow in comparisons.
- For Aliens/WQS, build independent blocks with nearly equal penalty thresholds so many counts switch at the same `lambda`.
- For bitset DP, alternate shifts by `63, 64, 65, 127, 128, 129`, keep the bitsets dense, and touch many separate bitsets in round-robin order.
- For sparse-transition DP, start with a sparse warmup, then add a compact dense burst that creates many reachable transitions before cleanup can happen.
- For TLE brute forces, maximize the number of live candidates per state, avoid early impossibility checks, and repeat expensive near-tie rows instead of relying on one maximal case.

**Fake D&C Monotonicity**

- Base: choose a cost family where exact `opt[i]` is monotone for a long prefix.
- Violation: add one local bonus/penalty that makes `opt[t] < opt[t-1]` or makes the best split jump farther than the solver's buffer.
- Mask: restore the original cost family after `t` so random suffix checks look normal.
- Tie version: make splits `a` and `b` equal at row `t`, but only one split allows the next row to stay optimal.
- Useful target traces: `0, 1, 2, 1, 3, 4`, `5, 6, 7, 2, 8`, or a long flat plateau followed by one backward move.

**Knuth / Quadrangle Inequality Failures**

- Search tiny arrays or interval costs for one `a <= b <= c <= d` with `w[a][c] + w[b][d] > w[a][d] + w[b][c]`.
- Keep neighboring rectangles valid so a local random checker has a low chance of sampling the bad one.
- Add many equal best split points; force the wrong implementation to choose the endpoint that narrows the next window incorrectly.
- Put the first bad interval at `len = SMALL_EXACT + 1` if the solution scans short lengths exactly.
- Include boundary intervals where the raw cost differs from the theorem-friendly normalized cost.

**CHT Order And Convexity Failures**

- Equal slopes: insert multiple lines with the same slope and different intercepts; query on and near the tie point.
- Query backtrack: run increasing queries until the deque pops old lines, then jump back to an `x` where a popped line wins.
- Slope backtrack: insert mostly sorted slopes, then one line with a middle slope after the hull is already built.
- Near-parallel: use close slopes and huge intercepts so the true intersection is large and `double` comparisons disagree with integer cross-products.
- Convexity break: make the best line alternate between old and new candidates instead of moving through the hull once.

**Aliens Trick / WQS Failures**

- Build several independent gadgets with two choices: one uses one more item/segment and has slightly better raw value.
- Tune the gadgets so their penalty breakpoints nearly coincide; `count(lambda)` becomes a thick critical layer.
- Include equal penalized values with different counts; wrong tie-breaking sends binary search in the wrong direction.
- After binary search, require exactly `k` where neither endpoint's selected witness represents the true constrained optimum.
- Compare the full curve of `(lambda, value, count)`, not only the final recovered answer.

**Bitset And Sparse-Transition Shortcuts**

- Keep reachability dense so every shift/OR touches almost every word.
- Use misaligned deltas around word boundaries: `63, 64, 65` and `127, 128, 129`.
- Alternate many independent bitsets so cache locality is bad and no single active-word set remains small.
- Convert sparse random transitions into a dense burst after a warmup; the adversarial solver's active set grows before it can prune.
- Pair sparse and dense mirrors with similar answer shape so answer-only tests miss that the transition trace exploded.

**Pruning-Resistant TLE Brute Forces**

- Identify the pruning predicate first: upper bound, dominance, reachable-state count, early break, sorted order, or small value range.
- Make every candidate nearly tied so dominance checks cannot delete much.
- Delay contradictions until the deepest layer, after the brute force has already enumerated the expensive prefix.
- Use `n = K+1` for exact-cutoff branches and full maximum sizes for "good constant" `O(n^2)` or `O(n^3)` DP.
- Prefer many medium-heavy cases over one obvious monster when the solution has per-case warm caches or adaptive buffers.
- Measure internal work units such as scanned transitions, active states, touched words, and fallback calls; wall time alone is noisy.
