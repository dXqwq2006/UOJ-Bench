- [WA] Greedy augmenting-path matching is plausible when the graph has many near-equivalent choices: process left vertices by degree, try the first successful DFS path, maybe sort neighbors by rarity, and keep the match if it fits. It fails on Hall-tight pockets and alternating chains where the locally scarce-looking right vertex must be saved for a later vertex, and recovery needs a long alternating path rather than one local swap.

- [WA/TLE] Bounded-reroute matching caps augmenting-path depth or recursive steals and keeps the first matching that looks locally stable. It is WA when the only maximum matching requires a longer alternating path, and TLE when the cap is raised until the search degenerates into the one-path O(nm) augmenting-path routine rather than Hopcroft-Karp-style batching.

- [WA] "Most constrained first" assignment commits degree-1 vertices, then repeatedly matches the minimum-current-degree left vertex to the rarest right vertex. The false theorem is that local scarcity exposes Hall obstructions; break it with a small subset of left vertices whose shared neighborhood is tight but whose degrees stay 2 or 3 until an early outside commitment spends the unique slack.

- [WA] One-level branching on low-degree matches feels robust: when a vertex has two candidates, try both locally, score by remaining forced moves, and keep the better branch, with a tiny brute-force tail. It remains wrong when the first dangerous choice is outside the tiny tail and the Hall violation is only visible after several independent-looking commitments collide.

- [WA] Degree-profile compression groups left vertices by degree, neighbor-count histogram, or coarse neighborhood signature and solves the summarized instance. The hidden assumption is that multiplicity and identity do not matter; break it with two groups that look locally identical but have different global roles through one bridge or reserved right vertex.

- [WA] Bipartite reduction by visible parity is strong on grids, time-expanded states, and pairing statements: split by coordinate parity, time parity, or a guessed color, then run Hopcroft-Karp or flow. It fails when one odd-cycle or parity-coupling gadget changes feasibility but preserves the surface shape, especially if the adversarial solution drops or locally patches the conflicting edge.

- [WA] Component-local bipartite coloring colors each connected component independently, flips each side to improve the score, and later treats cross-component constraints as harmless. The missed obligation is global orientation consistency; a component-flip trap makes every component individually bipartite while one later edge or side constraint forces the opposite orientation from the one chosen locally.

- [WA] "Almost bipartite" patching deletes one edge from each detected odd cycle, contracts the odd cycle, or branches over a tiny list of bad edges before using a bipartite matcher. This is still only a relaxation when the true optimum must use several parity violations together; a single odd-cycle perturbation can be enough if the deleted edge is the unique feasible connector.

- [WA] General matching treated as bipartite is not just a missing blossom implementation; contestants often choose a natural partition from labels, directions, or statement roles and ignore same-side edges as noise. Keep it nontrivial by making same-side edges rare and sample-looking, but essential through an odd cycle or blossom that increases the optimum by one.

- [WA] Maximum matching size used as the whole answer is fragile when the statement also asks for lexicographic choice, recoverable pairs, stability-like side constraints, or a valid witness shape. A standard matcher can return the right cardinality while decoding the wrong witness if multiple maximum matchings exist and only some satisfy the extra rule.

- [WA] Min-cut value as original answer works for pure separation, but fails when the problem needs a structured cut set: connected remaining graph, exactly one object per group, valid deletion witness, or vertex rather than edge removal. The wrong shape preserves capacities and decodes one residual side; break it with equal-value cuts where only one side decodes to a legal original witness.

- [WA] Component compression for cuts merges repeated neighborhoods, SCC-like regions, or equal-capacity blocks and sums boundary capacities. The false premise is that preserving every total in/out capacity preserves the feasible witnesses; it fails when two vertices inside the block must be distinguished by a side constraint, vertex capacity, or post-cut connectivity condition.

- [WA] Edge-capacity modeling of vertex limits is a classic plausible shortcut when most vertices have capacity 1 or infinity and only a few look special. It fails on a vertex-capacity trap where all incident edges can individually carry flow, but the original vertex can be used only once; ad hoc splitting of high-degree or sample-visible vertices leaves ordinary low-degree traps intact.

- [WA] Lower-bound or demand flow simplified to ordinary max-flow treats required flow on edges as a postprocessing check, subtracts obvious demands greedily, or runs max-flow from source to sink without the circulation super-source construction. It passes balanced random data but fails when local demands are individually satisfiable and globally create a deficit cycle or force flow through a cut before any optional capacity is considered.

- [WA] Multi-commodity flow collapsed to one commodity is tempting when commodities share source/sink classes or all capacities are integral. The aggregate max-flow can have the right total while mixing labels through a bottleneck in a way the original commodities cannot realize; break it with two commodities that must cross the same narrow corridor in opposite incompatible ways.

- [WA] Min-cost flow with potentials or shortest augmenting paths becomes wrong when the implementation greedily ships each demand to its currently cheapest sink and only repairs negative cycles locally. The missed theorem is global exchange across multiple units; adversarial costs make two individually cheapest assignments block a later unit, while a more expensive early route enables lower total cost.

- [TLE] Successive shortest path min-cost flow is correct only after its complexity is matched to the demanded flow. With integral capacities it can perform one flow augmentation per unit of sent flow, and SSP also has bad worst-case iteration behavior; large `F` needs scaling, blocking-style batching, or a different min-cost-flow algorithm rather than a hidden per-unit loop.

- [TLE] Ford-Fulkerson with arbitrary one-path augmentation assumes the augment count is harmless. Integral capacities can still force augmentation count to scale with the max-flow value; Edmonds-Karp's shortest-path rule bounds the count by graph size, but its O(VE^2) work can still miss dense or repeated-flow limits.

- [WA/TLE] Crown, repeated-C4, and order-dependent Kuhn traps assume vertex order, edge order, or greedy pre-matching only changes constants. A bad initial maximal matching can leave long alternating repairs where each outer scan finds little progress, and variants that cap reroutes, cache failed roots, or reuse visited state can turn the same order trap from TLE into a nonmaximum matching.

- [TLE] Kuhn-style bipartite matching scans one DFS augmenting path per left vertex and can cost O(nm) on dense or adversarial orderings. It may pass sparse random cases after degree sorting, but layered near-complete graphs expose why limits often require Hopcroft-Karp's O(m sqrt n) batching.

- [WA/TLE] One-DFS-per-Dinic-phase implementations build a level graph and then send only the first augmenting path, treating one DFS success as if it were a blocking flow. The level graph may contain many compatible shortest augmenting paths in one phase; failing to drain it loses the Dinic/Hopcroft-Karp batching guarantee, while stale `iter`, `seen`, or level caches can also hide residual routes that reopen through reverse edges.

- [TLE] Dinic borrowed from unit matching instances is easy to overgeneralize. The O(E sqrt V) behavior is tied to unit-network or vertex-capacity-one reductions; ordinary capacitated networks still have the O(V^2E) style worst case, so dense non-unit residual layers can TLE despite smooth samples.

- [MLE] Explicit time/state-expanded flow multiplies the residual graph before any solver runs. A time horizon or DP dimension usually creates one node copy per state and edge copies for transitions, so `T * (V + E)` or `states * transitions` storage that is small in samples can exceed memory at the stated maxima.

- [WA] Capacity bucketing or top-bit scaling keeps only edges above a threshold, rounds capacities to powers of two, or solves the high-capacity backbone before patching small residual edges. The bad premise is that low bits are local noise; many disjoint small bridges can collectively beat the coarse route while staying far from the vertices touched by the repair pass.

- [WA] Keeping only the top `k` incident capacity edges per vertex is a stronger-looking sparsification for wide-capacity networks. It fails when no discarded edge is individually important, but a cut is widened by many medium or small edges distributed across different vertices, so every local top-`k` view hides the aggregate capacity.

- [WA] One residual snapshot used for decoding assumes the first stable min-cut, BFS tree, or level graph represents all optimal witnesses. Equivalent max flows can leave different residual reachability sets; if the original problem distinguishes which objects are selected, a stale or tie-dependent residual side can decode an illegal or nonoptimal witness.

- [WA] Residual dead-edge caching marks an edge or vertex useless after one failed DFS, sometimes clearing only touched vertices between phases. This relies on monotonic residual structure, but reverse edges can reopen a layer after an unrelated augmentation; a reopen-layer gadget makes the only remaining augmenting path pass through a vertex cached dead earlier.

- [WA] Single-parent BFS in flow or matching stores just the first predecessor for each residual vertex and ignores alternate shortest parents. That is fine for reachability but not for choosing a globally compatible set of augmenting paths; a two-parent construction can force the solution to use the second parent after the first path consumes a shared bottleneck.

- [WA/TLE] Partial Dinic phases stop after a fixed number of DFS pushes or after one DFS template fails, without proving the level graph is blocked. That breaks correctness if an alternate branch still exists, while larger push limits just move the failure toward the documented phase worst case.

- [WA] Treating integrality as automatic after capacity transformations is wrong when capacities are scaled, averaged, divided by gcd per component, or represented with floating costs. Standard integral max-flow gives integral answers only under the original integral network; a rounded or normalized network can produce a feasible-looking value that cannot be lifted back to valid discrete assignments.

- [WA] Decomposing by connected components before adding source/sink or side-constraint edges assumes independence too early. Matching and flow reductions often create coupling through capacities, quotas, or global cardinality after the original graph components are separate; a pair of easy components sharing one quota can make the locally optimal decomposition globally infeasible.

- [WA] Binary search plus max-flow on an answer threshold is fragile when feasibility is not monotone under the chosen threshold, even if the intended property has a monotone formulation. A wrong reduction may filter edges by cost, distance, or rank and test plain max-flow; adversarial instances make adding a "better" edge change parity, quota, or witness structure so the test flips in both directions.
