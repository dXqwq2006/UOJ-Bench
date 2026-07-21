# Dynamic Programming

## Topic-Specific Variants

- Same compressed state, different continuation answer.
- Equal current DP value, different future feasibility.
- Child/segment merge where the kept summary is commutative but the real transition is not.
- Long neutral prefix that makes an omitted state component look irrelevant until a late suffix.

## Strong Adversarial Solutions

- A dropped state component is recoverable from the remaining summary.
- Greedy tie-breaking inside DP preserves enough witnesses.
- Equal child or segment summaries can be merged without remembering provenance.
- The optimal decision is effectively unique even when the value table has ties.

## Killer Constructions

- Search small cases for two prefixes/subtrees with identical proposed state and different continuations.
- Force many tied DP values, then attach a suffix that selects one tie.
- Use the same components in different merge trees and require different outcomes.
- Scale by adding neutral layers around the state-collision pair.

- Exact small-state fallback: place the state-collision gadget just above the fallback size, then pad with neutral layers.
- Dominance pruning with local score: create two states with equal current score where the locally worse-looking state is the only one extendable.
- Top-`B`/beam DP: make `B+1` tied states at one layer and let only the last by the solver's tie order survive the suffix.
- Memoized brute force: make many prefixes share the same memo key after compression but require different hidden history.

- Exponential search with strong pruning: delay the first contradiction until depth `n-1`.
- `O(n^3)` DP with early minimum break: use plateaus where every candidate remains tied until the final transition.
- Bitmask DP with subset pruning: generate dense compatibility so most subsets remain feasible.
- DAG DP by reachable states only: build a layered DAG where all states are reachable and equal-valued.

- **State-collision pair**: brute force small prefixes until two prefixes have the same stored tuple `(last, count, mask, value)` but different exact future answers. Put a common suffix that accepts only one prefix.
- **Tie ladder**: for layer `i`, create choices `a_i` and `b_i` with equal immediate value. Make the constraint at layer `k` require the exact parity/order of earlier `b` choices. Greedy reconstruction and single-parent DP fail.
- **Non-commutative merge**: create three components `A,B,C` where `(A+B)+C` is feasible but `A+(B+C)` is not, while all pair summaries are identical. Use for tree/interval DP merges that sort or hash children.
- **Impossible-state dust**: include many zero-count or unreachable states with attractive numeric values. Adversarial solvers that do not initialize `-INF`, `0`, or modulo states correctly will use them.
