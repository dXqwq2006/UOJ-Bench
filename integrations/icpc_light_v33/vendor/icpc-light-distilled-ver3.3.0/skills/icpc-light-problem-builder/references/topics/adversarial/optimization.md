- [WA] Divide-and-conquer DP optimization: Assumes the best split index is monotone, so the solver only searches a recursive opt window. It is nontrivial when the cost is almost Monge except for a hidden local reversal; stable tie policy, tiny exact fallback, and wider windows on dense ties make it survive weak tests without proving monotonicity.

- [WA] Knuth-style interval optimization: Assumes `opt[l][r-1] <= opt[l][r] <= opt[l+1][r]` and restricts each interval split to that inherited band. It can look exact on additive length costs but fails when an interval bonus or penalty depends on both boundaries; harden with exact short intervals, deterministic equal-split ties, and wider collapsed bands.

- [WA] Monge/SMAWK row-min pruning: Assumes the cost matrix is totally monotone, so columns deleted for one row never matter later. A matrix that is smooth almost everywhere but has one decisive inversion defeats the pruned search; local Monge spot checks, exact small blocks, and fallback on reversed adjacent optima keep the adversarial route plausible.

- [WA] CHT order assumptions: Assumes slopes and query x-values arrive in the promised order, so one pointer only moves forward along the hull. Duplicate slopes, equal intersections, or a short nonmonotone query segment make the pointer skip the real line; harden with explicit duplicate handling, integer cross-products, and exact or Li Chao routing for detected unordered chunks.

- [WA/RE] Intersection-based hull with floating or narrow arithmetic: Assumes double intersections or 64-bit products preserve hull order under full constraints. Midrange tests pass, but near-parallel lines or max-coordinate products flip comparisons or overflow; harden with wider cross-products where possible, clamped sentinels, and a slow path for near-ties while leaving the broad shortcut unchanged.

- [WA] Li Chao domain assumptions: Assumes every meaningful x-value is inside a fixed or compressed domain and that comparing only stored query coordinates is enough. It is strong for pointwise queries, but range queries, off-grid extrema, or missing sentinels change the selected line; harden by adding all known query coordinates and boundary sentinels, with exact fallback for tiny coordinate sets.

- [WA] Parametric binary search on a non-monotone decision: Assumes `feasible(x)` changes truth value once, while the implemented predicate has holes from parity, integrality, disconnected choices, or tie handling. Smooth thresholds pass ordinary data; harden by returning witnesses, checking final neighbors, and exact-searching short numeric ranges.

- [WA] Lagrangian / Aliens trick without convex tradeoff: Assumes the optimal value by count has the needed convex envelope and that the chosen count moves monotonically with penalty. Nonconvex count-value pairs or tie plateaus produce the wrong K even when each penalized DP is correct; harden with deterministic tie direction, neighbor penalties, and exact repair around the requested K.

- [WA] Discrete ternary search or local hill climb: Assumes the objective is unimodal because sampled values look convex or concave after sorting. Multiple shallow basins or flat plateaus make it settle on the wrong local optimum; harden by evaluating a final window, adding deterministic restarts by structural buckets, and exact-solving small domains.

- [WA/TLE] Monotone queue DP optimization: Assumes candidate expiration and best-candidate order can be represented by one deque because the transition separates cleanly. It breaks when the coefficient/order depends on the outer index or when valid windows have holes; harden by resetting across disorder boundaries, exact-scanning small windows, and falling back when candidate order reverses too often.

- [WA/TLE] Two-pointer boundary optimization: Assumes the best left or right boundary only moves in one direction, usually because costs are nonnegative or constraints are nested. Mixed signs, delayed constraints, or one global budget can force the true boundary backward; harden with sign and sortedness guards, bounded backtracking, and exact handling for tiny or dense intervals.

- [WA] CDQ/offline dominance optimization with unsafe tie order: Assumes contributions flow strictly from earlier sorted coordinates to later ones. Equal coordinates, simultaneous updates, or inclusive/exclusive boundary mistakes create missing or double-counted contributions; harden by processing tie groups consistently, separating update and query phases, and exact-checking small buckets.

- [WA] Candidate shortlist for quadratic transitions: Assumes each state only needs the nearest, cheapest, or top-C predecessors under a proxy score. This survives well-spread data but fails on clustered duplicates or a far predecessor with unique future value; harden by keeping extremes, representatives per bucket, all proxy ties up to a cap, and an exact tiny fallback.

- [WA/TLE] Min-plus or max-plus convolution shortcut: Assumes costs are convex or concave enough for divide-and-conquer, SMAWK, or a transformed ordinary convolution. Arbitrary arrays with one nonconvex dip break the envelope, while exact quadratic fallback is too slow at full constraints; harden with convexity probes, exact short segments, and routing only verified convex chunks through the shortcut.

- [WA/TLE] D&C DP monotonicity probes/fallback: Assumes sampled opt traces, short exact prefixes, or local Monge checks are enough to route layers through a monotone-window optimizer and only rarely fall back. The theorem-backed speedups need global opt monotonicity, quadrangle/Monge structure, or total monotonicity; when the probe misses the decisive violation the answer is wrong, and when it trips often the code reverts to the original quadratic or rectangular scan.

- [MLE/TLE] Pseudo-polynomial budget blowup: Assumes value, sum, or weight bounds in generated data reflect the true resource budget. Max-value cases make `O(n * sum)` memory or time explode even though the recurrence is correct; harden by capping dominated ranges, switching to sparse states when density is low, and exact-solving only the small-budget subcase.

- [WA/MLE] Bitset universe/offset failure: Assumes all shifted states are nonnegative, bounded, and meaningful after truncation. Positive small-sum data hide the issue, but offsets, signed transitions, or a cap can discard a later-needed state; harden by tracking offsets explicitly, keeping overflow sentinels, and sparse-falling back near the cap.

- [TLE/MLE] Frontier or Pareto pruning with a hidden antichain: Assumes most states are dominated, so the implementation keeps a capped frontier or a few representatives per compressed signature. Weak data often collapse well, but constraints can force a large antichain where every dropped label has different future reach; harden by preserving structural diversity, exact small layers, and cost-aware fallback instead of treating the cap as proof.

- [TLE/MLE] Li Chao or hull structures over a broad coordinate universe assume the active domain is small after compression. Point queries over known coordinates fit well, but range-restricted lines, dynamic coordinates, or off-grid extrema can force many segment-tree nodes or expensive fallback comparisons. Compressing only observed query points is a resource hardening step only when the optimum cannot occur between them.

- [WA] Sparse event coordinate compression: Assumes the optimum changes only at input endpoints or observed query points. It fails when gap length, parity, open intervals, or an off-event extremum affects the cost; harden by adding adjacent sentinels, parity/classes, and exact handling for small compressed spans.

- [WA] Prefix-sum cost precomputation with missing interaction terms: Assumes an interval or rectangle cost is additive after preprocessing, so the optimizer can query it in O(1). Boundary interactions, exclusions, or pair terms crossing the split survive many smooth tests but invalidate D&C, Knuth, or hull decisions; harden by isolating truly additive parts and exact-computing suspicious short or dense pieces.

- [WA/TLE] Amortized pointer or heap shortcut with non-monotone stale state: Assumes lazy deletions and one-way candidate movement are bounded because each candidate becomes irrelevant once. If the objective can revive an old candidate or many stale candidates accumulate under worst-case updates, the answer or resource use breaks; harden with timestamps, periodic rebuilds, and exact scans when the live/stale ratio collapses.

- [WA/TLE] Parallel binary search with coupled queries: Assumes each query answer is monotone under the same update sequence and that processing midpoint batches independently preserves state. Shared budgets, destructive updates, or tie-sensitive rollback violate the offline schedule; harden by separating immutable updates from per-query state and exact-solving small coupled groups.

- [TLE] Parametric search with a heavy checker assumes the logarithmic factor is harmless. Binary searching an answer while each check runs matching, flow, shortest paths, or dense DP can exceed the intended budget even when every check is exact. Caching and parallel binary search help only when checks share monotone immutable state; otherwise the same full instance is solved too many times.

- [WA] Optimizer selected by weak structural probes: Assumes a cheap check such as "few inversions", "mostly sorted", or "nearly convex" is enough to route an instance to a theorem-based shortcut. Adversarial inputs can satisfy the probe while violating the theorem at the decisive state; harden by making probes witness-based, keeping exact fallback for small decisive regions, and refusing to let a probe replace the missing proof.
