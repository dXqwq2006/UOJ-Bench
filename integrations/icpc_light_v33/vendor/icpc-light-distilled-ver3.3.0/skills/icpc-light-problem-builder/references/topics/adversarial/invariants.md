- [WA] Parity/xor sufficiency: match one aggregate parity, xor, sign, or checkerboard value and treat it as a reachability certificate. Tiny exact cases, duplicate handling, and final value checks hide the gap, but operation graphs can have several orbits with the same aggregate signature.

- [WA] Residue histogram compression: keep only counts by parity, residue, gcd class, color, degree parity, or balance class, then solve the quotient state. Extra moduli and singleton buckets catch easy counterexamples; arrangements with the same buckets but different order, adjacency, or pairing still separate.

- [WA] Necessary invariant bundle treated as complete: compare a tuple of sum, xor, parity, extrema residues, component balances, and moduli, then choose any canonical state with that tuple. The tuple can miss a nonlinear obstruction, a hidden cut, or a root-dependent potential.

- [WA] Independent local invariants: repair each component, interval, row, color class, or connected block once its local balance matches. It fails when the legal moves share a global spare, a boundary capacity, or an ordering resource that one block consumes before another can use it.

- [WA] Permutation parity overreach: use inversion parity or sign as the whole answer for swaps, rotations, sliding tokens, or generated moves. It survives common puzzle-like tests, but the generated group can be a proper subgroup, a bipartite-board restriction, or an exceptional orbit with same-parity states still unreachable.

- [WA] Linear congruence overreach: solve the gcd/modular/linear-span relaxation and greedily realize the certificate. Extra divisor checks and normalized coefficients look strong, while nonnegative counts, operation ordering, bounded capacities, and forbidden intermediate states can still block realization.

- [WA] Boundary-blind conservation: apply a conservation law from the unconstrained operation after moves become clipped, saturated, one-way, or endpoint-blocked. The total remains convincing, but equal-total states can be trapped by boundary capacity or by an irreversible transfer.

- [WA] Monovariant termination as feasibility: assume that any sequence decreasing a potential reaches the target, so the solution always takes the largest drop. Valid instances may require a temporary increase, a plateau traversal, or a nonlocal detour before the potential can fall.

- [WA] Potential as objective proxy: minimize a potential and use it as the greedy score, DP order, or stopping condition. Secondary slack terms reduce obvious misses, but a worse potential now can unlock a better final witness or a cheaper later merge.

- [WA] Invariant-guided local repair: first build any state with the right coarse signature, then swap, reroute, rotate, or locally replace violations while preserving that signature. A bounded local-improvement phase validates small examples; adversarial cases need a long rearrangement or a temporary break of the chosen invariant.

- [WA] Small-table law extrapolation: infer a formula from exhaustive small `n`, small values, or few residues, then keep a short exception table. Larger two-parameter thresholds, delayed periodicity, or the first large obstruction invalidate the guessed invariant law.

- [WA] Eventual-period shortcut: reduce large states by a detected period after a short prefix, assuming the answer depends only on one invariant modulo that period. Apparent periods can split on a second invariant, or the true preperiod can sit beyond any sampled range.

- [WA] Canonical representative quotient: normalize labels, sort buckets, rotate the state, or pick the lexicographically first representative before solving. The representative may spend a scarce hidden resource that another state in the same quotient class would preserve.

- [WA] Invariant certificate plus weak witness check: verify move syntax and the final coarse signature rather than the full semantic target. Partial simulation over touched positions helps, but untouched constraints, delayed effects, and stale component metadata can keep the witness invalid.

- [WA] DSU aggregate stored only at the representative: maintain component sums, minima, bipartite flags, bases, or balances as root data, then forget that unions can swap roots. Connectivity queries keep passing, while metadata follows the wrong representative after a merge order the tests did not cover.

- [WA] Same-component edge treated as a no-op in parity or potential DSU: once endpoints already share a root, the adversarial solution ignores the new constraint. Closing an odd cycle, inconsistent difference equation, or xor contradiction is exactly the case that must compare accumulated potentials.

- [WA] Root-swap sign error in weighted/parity DSU: the relative delta is correct only for one attachment direction, but union by rank or size reverses that direction on balanced cases. Long chains and cycles expose flipped distances, xor labels, or modular potentials.

- [WA] Path compression without label composition: update the parent pointer but not the distance, parity, xor, or potential to the new parent. Plain connectivity remains correct, so the defect hides until a query depends on the accumulated label.

- [WA] Rollback DSU logs parent and size but not augmented state: component answer, bipartite flag, parity basis, min/max, lazy offset, or contradiction marker survives an undo. Later branches of a segment-tree-over-time traversal inherit metadata from edges that are no longer active.

- [WA/TLE] Rollback sibling branch restores only visible state: after one branch returns, parent pointers, roots, or array values look correct, but cached component answers, dirty buffers, lazy tags, memoized certificates, or history cursors still describe the previous sibling. This is especially plausible when each branch differs by one small update and public tests only compare visible representatives.

- [WA/RE] Rollback snapshot mismatch on no-op merges: record history only for successful unions, then roll back one operation per edge occurrence. Duplicate edges or same-component edges make the history depth diverge, producing stale state or an empty rollback pop.

- [WA] Offline lifetime endpoints shifted by one: convert add/remove events to active intervals, but include the deletion time, miss the insertion time, or merge duplicate lifetimes. The segment tree over time is structurally correct, yet answers a neighboring version of the graph.

- [WA] Reverse-time deletion assumes a true inverse: process operations backward and "undo" by toggling an aggregate. Saturating, clipped, order-dependent, or metadata-bearing updates are not inverted by the same local change.

- [WA/TLE] Exact search behind an invariant filter: trust the invariant to reject most instances, then run BFS, DFS, subset DP, or meet-in-the-middle inside the passing class. All-pass inputs with the same coarse certificate leave the exponential core exposed.

- [TLE/MLE] Orbit closure inside one signature: generate every state reachable under a fixed invariant until the target appears. Hashing and canonicalization remove duplicates, but a same-signature orbit can still be factorial or puzzle-state sized.

- [TLE] Segment-tree-over-time replay at leaves: decompose lifetimes correctly, but rebuild or replay all active operations for each query instead of using rollback on entry and exit. Dense lifetimes turn the intended logarithmic multiplier into a quadratic run.

- [TLE/MLE] Full data-structure copies per time node: store a complete DSU, component map, basis, or certificate for each segment-tree node. It is hard to break on small `q`, but maximum inputs multiply both active intervals and per-state payload.

- [WA/TLE] Path compression used inside rollback DSU: compressed parent changes are not logged, or the solution relies on amortized `alpha(n)` bounds after undoing work. Rollbacks can both contaminate later branches and destroy the amortization argument.

- [TLE] Rollback DSU without rank or size because the leader is semantically fixed: disabling path compression is fine only if the tree height is controlled. Adversarial union order builds chains, so repeated finds become linear.

- [TLE] Explicit member lists without small-to-large merging: update every vertex's representative, parity base, or component aggregate whenever two sets merge. Public tests with random merges look near-linear; repeatedly merging a large set into a small one makes the total movement quadratic.

- [TLE] Amortized invariant maintenance reset by rollback or rebuild: a potential proof pays for expensive repairs over one monotone run, but offline branches, snapshots, or repeated rebuilds restore the expensive configuration. The same legal pattern can pay the startup cost many times.

- [TLE] Global certificate recomputation after local moves: recompute component balances, residue buckets, dependency graph ranks, or obstruction certificates after every repair. Cached totals cover easy cases; long cascades still force near-full work per local step.

- [TLE] Canonicalization cost treated as free: sorting buckets, renaming components, reducing by symmetry, or hashing full representatives happens after nearly every invariant-preserving move. Large collision classes spend more time normalizing than progressing.

- [TLE/MLE] Product table over invariant dimensions: tabulate residues, parities, ranks, balances, boundary modes, and contradiction states together because each dimension is small alone. Inputs maximizing several dimensions create a dense Cartesian product; pruning rare signatures loses the witness-bearing cases.

- [MLE] Representatives per invariant class: store all witnesses, predecessors, best candidates, or canonical states for each coarse signature so later checks can disambiguate. A high-collision signature can contain too many distinct states for the memory limit.

- [MLE] Rollback history stores full payload snapshots: save whole parent arrays, component maps, bases, or answer objects instead of only changed cells. Maximum active-interval depth makes the memory failure deterministic from the visible version structure.

- [MLE] Meet-in-the-middle keeps full witnesses for every same-signature state: storing predecessor paths, strings of moves, or complete boards doubles as proof material. The reachable half may fit as hashes, but not with full reconstruction data attached to every node.

- [RE] Recursive `find` or repair descent assumes shallow invariant trees: a decreasing potential proves termination, not stack safety. A legal chain of unions, implications, or local fixes can have depth `Theta(n)` or `Theta(value range)`.

- [RE] Recursive state search assumes invariant pruning bounds depth: the invariant prevents cycles but not a long simple path. Large-diameter orbits can overflow the call stack before the search has enough states to hit TLE.

- [WA/RE] Accumulated potential overflows fixed-width arithmetic: distances, signed deltas, component sums, or modular lifts are kept in a type sized for sample values. Parity-only tests pass, while maximum legal paths wrap the certificate or trigger undefined arithmetic behavior.
