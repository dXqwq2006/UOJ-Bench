- [WA] Half-proved exchange greedy: assume the locally best feasible element can always be exchanged into an optimum because that is true in matroid-like settings such as MST. Keep a deterministic comparator and feasibility checks, but leave the real gap that the underlying independence system may not satisfy the exchange property.

- [WA] Exchange-cycle shortcut: assume every bad greedy choice can be repaired by a sequence of harmless one-for-one exchanges. Pairwise swaps, adjacent exchanges, and local feasibility checks make the proof sketch look complete, but the missing object may require a k-for-k cycle where intermediate states violate capacity, parity, order, or a scarce shared resource.

- [WA] Interval-key transfer: assume earliest start, shortest duration, fewest conflicts, or most conflicts is just another safe interval ordering. It looks disciplined on activity-selection samples, but only the right structural order has the exchange proof; dense overlap blocks expose keys that reject several compatible intervals.

- [WA] Weighted interval downgrade: keep an unweighted interval greedy after values are added, sorting by finish time, value, value per length, or conflict count. Tiny exact fallback and slack tie-breaks help, but two modest compatible intervals can still beat one locally attractive weighted interval.

- [WA] Fractional-to-integral density: import value/weight, value/cost, or marginal-density greedy from a divisible relaxation into indivisible 0/1 choices. It survives loose capacities, then fails when one dense item prevents the only pair that fills capacity profitably.

- [WA] Noncanonical denomination greedy: assume taking the largest coin, chunk, or decrement first minimizes count because common currency systems often behave that way. Guard divisibility and tiny amounts, but leave noncanonical systems where smaller repeated denominations beat one large denomination plus filler.

- [WA] Priority finalization without monotonicity: settle a state permanently when it is popped with the best key, as in Dijkstra, even though later negative discounts, refunds, state budgets, or non-additive costs can improve it. Stale-entry checks make the queue clean, but the missing invariant is that settled keys never need to decrease.

- [WA] Huffman-like merge overreach: repeatedly merge the locally best pair by combined score, ratio, depth penalty, or id, assuming any priority merge has the same exchange proof. The produced tree or witness remains valid, but only the specific "two least frequencies become deepest siblings" invariant justifies Huffman-style greedy.

- [WA] Dense tie classes: assume equal immediate scores are interchangeable, then choose by a secondary estimate such as remaining options, scarce resources preserved, or local conflicts avoided. Inspect whole equal-score buckets and make ties deterministic, while preserving the weakness that local slack can protect the wrong future dependency.

- [WA] Proxy objective greedy: assume a local score correlates tightly with the final objective, then repeatedly take the move with best immediate gain, smallest cost, earliest finish, largest deficit reduction, or lexicographic improvement. Add one cheap future-aware term or a danger filter, but keep cases where a locally second-best move unlocks delayed value.

- [WA] Approximation-as-exact scheduling: assign each job to the currently least-loaded machine, maybe after sorting largest first, and optimize the produced makespan locally. Approximation guarantees explain why it looks strong, not why it is exact; adversarial order or near-equal bins can leave a better global partition.

- [WA] Constructive extension greedy: assume every legal prefix can be completed if it stays locally clean, then place the lexicographically safest item, fill the most urgent slot, or connect the closest compatible endpoint. Keep the partial state valid and reserve one flexible option per scarce class, but leave the failure in a legal, attractive, globally dead prefix.

- [WA] Scarce-resource overspend: assume a color, endpoint, bridge, residue, or slot is safe to consume when it improves the current step, then spend it unless a simple reserve rule blocks the move. A one-spare reserve hides obvious examples, but fails when the true bottleneck is a different type or needs a larger reserve.

- [WA] Delayed-regret greedy: assume ambiguous choices can be postponed without changing feasibility, then commit only dominant moves and flush a backlog by stricter slack or scarcity keys. This dodges early-consumption counterexamples, but the backlog order is still another greedy tie-break and can deadlock on hidden dependency chains.

- [WA] Scarcity ledger with delayed flush: track rare colors, endpoints, residues, coupons, or slots and defer spending them until a move looks forced. The ledger can reserve one spare per class and replay exact choices on tiny backlogs, but it still fails when the true bottleneck is a combination of resources or when flushing one deferred class consumes the bridge needed by another.

- [WA] Safety-filter greedy: assume visible danger predicates catch most losing moves, then choose the best-scoring candidate that does not isolate a block, consume the last endpoint, break a parity class, or exceed a violation threshold. If none pass, pick the least bad move; passing the filter is still not equivalent to being globally safe.

- [WA] Valid-state-first greedy: assume maintaining feasibility is more important than optimizing aggressively, then only apply moves that keep hard constraints satisfied and accept suboptimal slack loss. Full witness validation prevents trivial invalid output, but the valid greedy state can be dominated by an earlier temporarily awkward route.

- [WA] Bounded local repair: assume all greedy damage is local, then build a witness, detect violations, and apply a small fixed number of swaps, reroutes, evictions, or replays. Validation keeps failures semantic, while two interacting repairs or one far-away bad commitment remain outside the repair radius.

- [WA] Short backtrack window: assume the decisive bad choice is recent, then rewind only the last few greedy decisions and replay a few alternate local orders. This hardens suffix counterexamples without becoming full search, but fails when the necessary change is outside the window or requires a globally different prefix.

- [WA] Canonical representative greedy: assume symmetric candidates are equivalent, then relabel components, endpoints, colors, or equal-score blocks into a canonical order and always take the first representative. It removes accidental input-order noise, but adversarial symmetry can make every canonical representative spend the same hidden bottleneck while a noncanonical twin survives.

- [WA] Randomized restart layer: assume a few shuffles or restarts will hit a good tie resolution, then keep the best valid witness. No seed-specific hack is needed if the bad basin dominates or the required tie path has tiny probability across legal symmetric instances.

- [WA] Sort-key portfolio as proof substitute: run several plausible orderings such as earliest finish, smallest slack, largest density, rarest resource first, reverse id, and stable input order, then keep the best valid witness. This is a strong contestant wrapper for key uncertainty, but it remains wrong when all keys share the same local proxy blind spot or when the winning order is defined by a future dependency no listed key observes.

- [TLE] Quadratic candidate scan: assume scanning every feasible move per step is fine because candidates shrink quickly. Early breaks, cached scores, and sorted buckets pass random data, but dense equal-key inputs can keep `O(n)` live candidates for `O(n)` steps.

- [TLE] Sparse/dense mismatch: choose heap or set updates for a complete or implicit dense candidate graph, or choose all-pairs scans for a sparse one, because both are "greedy MST style." The answer may stay correct, but visible density pushes `O(m log n)`, `O(n^2 log n)`, or worse past limits when a matching representation would fit.

- [TLE] Priority-queue lazy greedy with stale candidates: assume each candidate is pushed or repaired only a few times, then pop the best-looking move, recompute if stale, and push it back when its score changed. Version stamps avoid wrong answers, but global score changes can make one item reenter the heap many times and turn the run quadratic or worse.

- [MLE] Lazy-deletion heap accumulation: update priorities by inserting new entries and marking old ones removed, assuming stale entries drain soon. The live answers remain clean, but hard instances update the same item many times before it reaches the top, leaving `O(updates)` stale records in memory.

- [TLE] Tie-bucket scan greedy: assume equal-score buckets are small, then for every step scan the current bucket to choose the move with best slack or lowest repair cost. Bucket by primary score and cache local slack to survive ordinary tests, while dense equal-score layers force near-full rescans.

- [TLE] Local repair cascade: assume a bounded or nearly bounded number of repairs after each greedy choice, then fix conflicts by walking to the nearest free slot, rerouting along a short path, or swapping until the local violation disappears. A cap avoids infinite loops, but legal chains can shift the same violation across the whole instance.

- [TLE/RE] Naive DSU chain in Kruskal-like greedy: use DSU as a cheap feasibility check but omit rank, size, or path compression, often with recursive find. Legal edge orders create parent chains, making finds `O(n)` each and risking stack overflow on maximum-length chains.

- [TLE/MLE] Keep-multiple-near-best greedy: assume retaining a tiny beam of near-best candidates is still cheap and not real search, then carry several partial witnesses or backlog variants through ambiguous regions. Caps and deduplication help on weak ties, but dense tie zones either grow too fast or prune the only viable continuation.

- [TLE/MLE] Bounded exact fallback spill: route small active components, low-width tie buckets, or short suffixes to exact enumeration, assuming the fallback is rare. Many regions just below the threshold, or one fallback repeated after every greedy step, turn the hardening into the bottleneck.

- [MLE] Snapshot-based rollback greedy: assume short backtracking is affordable, then store states or histories so the last few choices can be replayed. Deltas, capped windows, and compact ids help, but large states plus many tentative repairs or backlog flushes keep too many copies alive.

- [MLE] Materialized implicit candidate universe: precompute all pairs, edges, intervals, or moves before greedy because scoring is easier. This passes small cases, but complete graphs, metric candidates, and pairwise compatibility moves expose `O(n^2)` storage where streaming or per-item minima would fit.

- [MLE] Full witness per candidate: store a vector, path, assignment, or chosen-set copy inside every heap entry, beam state, or repair option. The algorithmic idea may be near-linear in states, but memory multiplies by witness length and fails on many near-best candidates.

- [RE] Greedy recursion on forced chains: assume the chain of forced local choices is shallow, then recurse through unique feasible moves, repair paths, or dependency propagation. Visited marks stop cycles, but a legal acyclic chain of length `n` can still overflow the call stack.

- [RE] Recursive local repair path: walk nearest free slot, next forced owner, parent dependency, or replacement edge recursively until the violation disappears. Ordinary tests have short paths; path, comb, or chain-shaped legal inputs make the repair depth equal to the input size.
