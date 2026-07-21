# Shortest Paths

## Topic-Specific Variants

- Layered equal-distance graphs with many parents per node.
- Shortest-path DAGs where equal-distance predecessors have different downstream feasibility.
- Zero-weight plateaus with a narrow positive exit.
- Late shortcut after a long route that looks settled.
- Relaxation schedules that keep improving a wide frontier by tiny amounts.
- SPFA SLF/LLL killer families where the queue looks smart but churns anyway.
- Edge-order twins: the same graph emitted in friendly and hostile adjacency order.
- Negative-chain dense hubs that repeatedly lower a hub frontier.
- Positive-only-looking graphs with one legal negative edge or negative detour.
- `0/1/2` graphs that trick `0-1 BFS` submissions into deque misuse.
- Overflow-scale graphs where the mathematical answer fits but intermediate sums do not.
- Hard-to-hack TLE variants with exact islands, churn monitors, and fallback thresholds.

## Strong Adversarial Solutions

- The solver stores one predecessor where the full shortest-path DAG matters.
- The solver finalizes a state before equal-distance alternatives have been exposed.
- The solver's queue heuristic relies on benign relaxation order.
- The solver treats zero-weight plateaus as harmless because distances do not change.
- SPFA with LLL or SLF works.
- A few Bellman-Ford-style sweeps converge because the input order is natural.
- Dijkstra is safe because negative edges are rare, far from the source, or do not form a negative cycle.
- Marking a vertex visited on push is equivalent to finalizing it on a current minimum pop.
- Deque shortest path remains valid for weights outside `{0, 1}` if small weights are "almost" binary.
- `long long INF` can be added to edge weights without guarding, or `int` distances are enough.
- One queue/hack family is enough; strong adversarial solutions often use portfolios and exact fallbacks.

## Killer Constructions

- Pair distance-only and witness-required tasks on the same graph.
- Attach a downstream filter that accepts only some equal shortest paths.
- Put the decisive shortcut last in adjacency/order-sensitive runs.
- For SPFA-like solvers, alternate relaxations across a wide plateau before each real improvement.
- Emit the same base graph under multiple orders: source-sorted, reverse source-sorted, weight-sorted, input-friendly, input-hostile, and randomized.
- Control zero density separately from graph density: sparse positive skeleton plus dense zero plateaus is better than uniform random zeroes.
- Separate correctness killers from TLE amplifiers. First make a small witness, then widen layers or hubs.
- Add exact-small and suspicious-case boundary tests just above common fallback thresholds, not only at maximum size.
- Mix tiny minimized witnesses, medium structured killers, and maximum stress versions in the same family manifest.

**Negative-Chain Dense Hub**

- Use when negative edges are legal but negative cycles are not.
- Build chain `a_0=s, a_1, ..., a_k` with forward edges weight `0` or small positive.
- For every chain node `a_i` and hub node `b_j`, add `a_i -> b_j` with weight `2*k-i`; add zero edges from hubs to exits or the next layer.
- Reveal improvements late: emit hub edges before the chain edge that improves `a_i`, or put the best chain edge last in adjacency.
- If using negative edges, keep them only on the forward chain so the graph is acyclic. `k` controls waves; hub width controls cost per wave.

**Zero-Layer Reawakening**

- Make `L` layers of width `W`; inside each layer add a directed zero cycle/SCC or dense zero edges.
- Add ordinary layer edges of weight `1`, plus one late edge from a tail vertex in layer `i` to the best entry of layer `i+1` with weight `0`.
- Emit the late edge after ordinary edges and relabel so the special entry is not obvious. One improvement then floods a whole zero plateau.

**Alternating Staircase**

- Build several below-threshold SPFA killer blocks with fresh vertices, connect block exits by one mandatory edge, and keep per-vertex push counts modest.
- This targets churn monitors such as `push_count[v]`, queue-length alarms, or fallback after one large spike.

**K-Sweep Failure**

- Create long path `p_0=s..p_R=t` with weight `1` edges and a better path `q_0=s..q_R=t` with total weight `0` or smaller.
- Order edges so useful `q_i -> q_{i+1}` edges are scanned just before `q_i` is improved; choose `R` larger than the guessed sweep count.
- Add cross edges from `p_i` to `q_i` with misleading weights so partial sweeps look plausible.

**Adjacency-Order Runtime Flip**

- For any churn family, emit `friendly`, `hostile`, and `shuffled` twins with the same edges.
- Friendly order puts the good edge first; hostile order puts all decoys first and the good edge last.
- If the adversarial solution sorts by weight or endpoint id, make decoys preferred locally but worse downstream, or relabel endpoints into preferred ids.

**Plateau With Narrow Exit**

- Make a zero plateau `P` of width `W`, reachable at distance `D`, with equal-distance incoming edges to every plateau vertex.
- Add exactly one good exit `x -> y` of weight `1`, plus many decoy exits of weight `1` or `2`.
- Put the good exit late and behind a parent that single-parent logic is unlikely to keep.
- For witness/counting/stateful tasks, make only one equal predecessor carry the required downstream property.

**Zero SCC Compression Trap**

- Create zero SCCs `A` and `B`. Initially only `A` is cheap; a late shortcut makes `B` reachable at the same best distance.
- Make the answer depend on fully exploring `B` after that improvement, and add `A`-like decoy exits so one-time zero compression appears to work.

**Late Negative Detour**

- Minimal shape: `s->t(5)`, `s->a(10)`, `a->b(-10)`, `b->t(0)`.
- Scale by adding near-target decoys with distances `1..K`, putting the negative detour behind a larger first edge, and fanning the detour into many targets.
- Ensure there is no directed path back into the detour.

**Negative Edge Hidden Behind Nonnegative Prefix**

- Make the first two or three layers entirely nonnegative, then put one negative edge in a deeper layer that improves a settled vertex or target.
- Add enough positive edges around it that the case does not resemble a toy negative-edge test.

**INF Plus Weight**

- Include unreachable vertices with large outgoing weights early in input order, plus reachable targets with ordinary correct distances.
- This exposes missing `dist[u] == INF` guards without requiring an extreme final answer.

**Int Distance Overflow**

- Use a path of length `K` and edge weight `C` where `K*C > 2^31-1` but the statement permits the true distance in 64-bit.
- Add an alternative path with distance just above or below the wrapped value; include near-boundary and just-over-boundary cases.

**Weight-2 Deque Trap**

- Minimal shape: `s->a(2)`, `s->b(1)`, `b->a(1)`, `a->t(0)`.
- Build stronger `{0,1,2}` layers where a weight-2 edge looks locally attractive but is worse than two weight-1 edges; add zero edges after both routes so parent/tie choices affect the witness.
- Include a near-identical all-`{0,1}` subcase that the adversarial solution passes.

**Small-Integer Bucket Off-By-One**

- Use weights in `[0,C]` where `C` is just above a common assumed bound.
- Make distances wrap around the bucket modulus and include stale entries that must be discarded before relaxing outgoing edges.

**Exact-Island Boundary**

- Infer likely cutoffs such as `n <= 200`, `m <= 5000`, DAG, all weights in `{0,1}`, or low zero density.
- Build the killer just above the cutoff; add one back edge, one weight-2 edge, or a few zero edges to destroy the easy certificate while keeping the shape similar.

**Churn-Monitor Evasion**

- Split the killer into many fresh-vertex blocks, keep per-vertex pushes and queue length below alarms, and maximize total relaxations across blocks.
- Place the hardest block after benign blocks to defeat early sampling.

**Portfolio-Order Killer**

- Build segments where source-sorted, target-sorted, weight-sorted, and reversed orders are each bad for a different segment.
- Concatenate segments so no single global order is friendly everywhere, then relabel after concatenation while preserving a debug manifest.
