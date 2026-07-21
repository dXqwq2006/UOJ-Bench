- [WA] Drop a state dimension that is usually correlated with the remaining summary: last color, previous digit, open segment count, cooldown age, carry, endpoint, or whether a group has started. The compressed bucket is broken by two histories that look identical until a late transition separates them.

- [WA] Merge states by current objective value, balance class, or another coarse score. Random layers often make one representative genuinely best, but an adversarial suffix can reward a discarded residue, endpoint, mask bit, or leftover budget.

- [WA] Apply a dominance rule that is valid for visible coordinates but ignores one latent future resource. State B has no worse cost/value/count, yet state A alone has the parity, residue, endpoint, or remaining gap needed later.

- [WA] Store only the optimum value when the task also depends on counting, lexicographic tie-breaking, feasibility witnesses, or labeled reconstruction. Equal-value states that are interchangeable for optimization can lead to different counts or valid outputs.

- [WA/TLE] Top-`B` / beam DP frontier: Ranks states by local score, lower bound, or greedy completion estimate and treats the surviving width as if it were a proof of dominance. It passes when the proxy matches final value, then fails or explodes when a delayed resource, parity, endpoint, or witness outside the beam controls the suffix.

- [WA/TLE] Memoized brute force with compressed keys: Replaces the true recursion state with a packed tuple such as coarse position, budget bucket, hash, residue, or normalized profile. It looks like dynamic programming because cache hits rise quickly, but two hidden histories can share the key while requiring different future legality or cost, and widening the key can turn the route back into brute force.

- [WA] Impossible-state dust: Leaves unreachable, zero-count, sentinel, or modulo-invalid states in the table with numerically attractive values. The recurrence can look correct on paths that stay reachable, while a later min/max/count merge accidentally reads dust as a real predecessor.

- [WA] Keep only the locally best few transitions out of each state. A locally expensive transition can unlock a later compatibility, avoid a capacity clash, or create the only useful boundary signature.

- [WA] Use an in-place 0/1 knapsack-style update in the wrong direction. Increasing capacity order lets the current item feed itself and silently changes the model toward unbounded reuse; decreasing order breaks complete-knapsack cases that intentionally need same-item reuse.

- [WA] Roll arrays across layers where same-layer propagation is part of the dependency graph. Dense, repeated, zero-cost, or bidirectional transitions can reuse an item/edge inside one phase even when random positive instances look acyclic.

- [WA] Roll away history that only matters after a delay. A two-layer DP looks right on random data, but cooldowns, minimum gaps, bounded runs, and "used in the last two positions" constraints may require older layers.

- [WA] Understate digit-DP prefix status. Storing only `(pos, tight)` or a coarse automaton state works when leading zeros and empty prefixes behave like normal digits, but hostile bounds expose missing `started`, previous digit, comparison, carry, or "already below" distinctions.

- [WA] Compress automaton DP states by last character, matched length modulo a value, or a broad "bad/alive" flag. Self-overlapping patterns and shared prefixes need exact failure/exit-link behavior, so two coarse states can have different future transitions.

- [WA] Collapse carry, borrow, or comparison chains in arithmetic DP. Long runs of 0, base-1, or equal digits can propagate a hidden carry or unresolved inequality long after the local digit relation looked settled.

- [WA] Treat interval or partition DP as separable across one boundary summary. Crossing pairs, unmatched items, two-sided endpoints, or colors/modes carried through the split can couple subproblems that look independent on near-laminar data.

- [WA] Non-commutative merges in tree, interval, or component DP: Sorts children, hashes summaries, or combines subproblems as if merge order and provenance were irrelevant. Associative-looking scalar summaries can hide orientation, endpoint, label, or budget ownership, so two merge trees with the same totals need not have the same feasible continuations.

- [WA/TLE] Use Knuth, divide-and-conquer, monotone queue, convex hull, or quadrangle-style optimization when the needed monotonicity or convexity is only empirically true. Skipping transitions outside a predicted argmin window gives WA; checking fallback windows broadly enough can erase the intended speedup.

- [WA/TLE] Replace one DP dimension with a canonical order or greedy reconstruction. Sorting events, fixing subset order, or keeping the cheapest representative per count works when the eliminated dimension is nearly determined, but adversarial ties or interleavings require a noncanonical order.

- [WA] Canonicalize subset/profile DP states that are not truly equivalent. Normalized shapes, sorted component sizes, truncated masks, or unlabeled profiles break when later attachment uses exact adjacency, component identity, or orientation.

- [WA] Process equal-key items one by one in an order-dependent DP that really needs simultaneous batches. Repeated coordinates, equal times, or tied values can let an item benefit from another item in the same layer.

- [WA] Treat zero-weight, zero-length, or no-op transitions as harmless edge cases. Zeros can create same-layer closure, duplicate counting, accidental unlimited reuse, or cycles in a recurrence that assumes strictly advancing phases.

- [WA] Reduce symmetric combinatorial DP states without carrying multiplicity. Canonical groups, sorted multisets, or unlabeled components work for a single optimum value, but counting, reconstruction, or labeled attachments need orbit sizes and identities.

- [WA/TLE] Assume tree-DP child contributions combine independently. A clear best child choice passes when each child has its own budget, but shared capacities, matchings, color limits, and global counts require a knapsack-like merge over children; doing that merge naively can become the TLE variant.

- [WA/TLE] Simplify rerooting with one aggregate that cannot exactly exclude the current child. It is correct for invertible or idempotent combines, but leaks a child's contribution back into itself or recomputes all siblings on stars and chains.

- [WA] Run graph DP in a single order on dependencies that are almost acyclic but not guaranteed DAGs. One pass fails on cycles, zero cycles, or SCCs where the recurrence needs condensation or a fixed point.

- [WA] Stop iterative DP, relaxation, probability DP, or value iteration on a weak convergence signal. Smooth instances settle quickly, while slow-mixing SCCs, zero-cost cycles, and near-tie probabilities can flip the chosen action after many more sweeps.

- [WA] Reconstruct a witness after pruning, merging, or beam search using parent pointers from representative states. The objective value may survive, but the representative path can be incompatible with constraints that belonged to a discarded state.

- [TLE] Use pseudo-polynomial DP when the numeric bound can be adversarially large. `O(nS)`, `O(nW)`, or `O(nV)` is plausible for small sums, capacities, values, or times, but worst-case magnitudes make the state count far beyond the input length.

- [MLE] Allocate a full textbook table whose worst-case dimension product is visible in the input model. `O(nS)`, `O(nK)`, `O(2^m n)`, or high-dimensional grids may pass samples and random-medium tests, then exceed memory at maximum bounds before any interesting transition runs.

- [TLE/MLE] Use bitmask or subset DP at the edge of the stated width. `O(m 2^m)`, `O(m^2 2^m)`, and Held-Karp-style tables can look fine for `m <= 18` but fall off sharply when the hidden maximum is a few bits larger.

- [TLE] Enumerate all submasks inside all masks without respecting the real bound. The standard nested mask/submask loop is `O(3^m)`, so friendly sparse masks pass while full-width masks at maximum `m` time out.

- [TLE] Expand bounded/multiple knapsack into individual copies or enumerate every copy count per item. Small multiplicities hide the cost, but large counts turn the transition into `O(W sum k_i)` or worse instead of the intended grouped or monotone treatment.

- [TLE] Scan all previous states in a transition that is sparse on friendly layers but quadratic on dense ones. Partition, DAG, LIS-like, edit, and interval DPs can lose on equal values, complete comparability, or all intervals overlapping.

- [TLE] Ship an interval DP with an unoptimized `O(n^3)` or `O(n^4)` transition because samples and random tests have small `n`. Maximum-length instances with no proven monotonicity or quadrangle property make the cubic loop the actual bottleneck.

- [TLE/WA] `O(n^3)` DP with early break: Keeps the full cubic transition but exits a split scan when the current candidate stops improving or a lower bound looks beaten. It is fast on convex-looking or sparse-friendly rows, yet the proof is absent; flat candidate ranges make the break ineffective, and nonunimodal rows can make it skip a later winner.

- [TLE/MLE] Store every reachable frontier state assuming the frontier stays sparse. Equal values, zero transitions, many interchangeable choices, or dense residues can fill the whole state space even when random weights keep it tiny.

- [TLE] Use top-down memoization with map, unordered map, vector/string keys, or normalized profile keys for a dense DP that could be array-indexed. The asymptotic state count is unchanged, but lookup, key construction, allocation, and recursion overhead dominate at the real limits.

- [TLE/MLE] Keep parent or witness histories for every state by copying paths, subsets, interval splits, or transition logs. The value DP fits, but reconstruction bookkeeping multiplies memory and copy time by witness length.

- [TLE] Recompute transition auxiliaries per layer instead of maintaining them incrementally. Prefix minima, convex hulls, automaton closures, child-knapsack merges, and valid-window sets may be rebuilt from scratch until dense equal-key layers make the helper work dominate.

- [TLE] Treat iterative relaxation as a small constant number of DP sweeps. The recurrence is exact only at the fixed point; slow-mixing SCCs, zero-cost cycles, and near-tie probabilities can require many passes, and a hard sweep cap turns the same family into WA.

- [MLE] Keep old layers, maps, or frontier buckets after a rolling transition because reconstruction or debugging once needed them. Peak memory becomes the sum over all layers instead of the intended two-layer footprint.

- [RE/MLE] Place large DP tables, profile arrays, or per-state work buffers on the call stack. It works for small dimensions, then stack-overflows or aborts at maximum bounds before a clean memory-limit failure is reported.

- [RE] Use recursive memoized DP when dependency depth can equal the input size. Tree paths, chain DAGs, nested intervals, and choices that repeatedly peel one element can overflow the language/runtime stack even though the number of states is acceptable.

- [RE] Raise a recursion limit to match a deep DP without accounting for the platform stack. In Python especially, avoiding an early recursion exception can still crash the process when the C stack is exhausted.

- [TLE/MLE] Count exact combinatorial DP values without the required modulo or cap. Path counts, tilings, partitions, and automaton counts can grow to huge integers, making arithmetic and storage balloon even when the state graph is small.
