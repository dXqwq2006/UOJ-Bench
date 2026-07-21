# Graphs

## Topic-Specific Variants

- One-edge property flips: bipartite, DAG, bridge, SCC, Euler, cactus/general cycle.
- Tree-like shell with one non-tree edge whose effect is not local.
- Alternating structures for matching/flow/cover shortcuts.
- Many valid witnesses where only some satisfy a downstream graph condition.

## Strong Adversarial Solutions

- The solver applies tree/cactus/DAG reasoning after a near-miss structural check.
- The solver relies on a canonical DFS/BFS witness when several witnesses are equivalent locally.
- The solver builds an auxiliary graph that loses equality, multiplicity, or alternating-path structure.
- A greedy matching/flow/cover shortcut is trusted because random graphs do not expose the alternating gadget.

## Killer Constructions

- Add exactly one edge that flips the claimed structure while preserving most local degrees.
- Embed a hard alternating or SCC gadget inside a large easy component.
- Generate the same graph with several valid traversal witnesses and attach a downstream filter.
- Keep the auxiliary graph summary identical while changing the original graph's multiplicity or reachability.

- DFS/BFS witness portfolios: relabel vertices and reorder edges so every common scan order sees a plausible but wrong witness.
- Greedy matching/flow plus repair: use alternating paths/cycles longer than the repair radius.
- Sparse/dense switching: put the hard gadget just across the threshold where the solution changes algorithm.
- Auxiliary graph compression: keep degrees/components identical while changing multiplicity, direction, or a single bridge.

- Kuhn matching with lucky order: emit a crown or chain of `C4` gadgets in the order that causes one useful augmentation per scan.
- Dinic-looking one-path-per-level BFS: use layered unit-capacity graphs with many blocking-flow paths per phase.
- SCC/toposort brute force: make a nearly-DAG graph with one back edge per layer, so many vertices are repeatedly reconsidered.
- Search with branch ordering: create twin-heavy graphs where all local scores tie and the contradiction is deep.

- **One-edge structural flip**: start from a tree/DAG/cactus, add one back/cross edge between two far nodes. Preserve degrees by adding a leaf elsewhere if degree multiset matters.
- **Crown matching order trap**: left `L_i` connects to all `R_j` except `R_i`; pre-match or order edges so greedy/Kuhn repeatedly dislodges a chain. Sweep vertex order and include the worst order, not only random order.
- **Weak Dinic layered graph**: create `k` layers of width `w`, edges from every vertex in layer `i` to every vertex in `i+1`, unit capacities, source to first layer, last layer to sink. Implementations that send one DFS path per BFS phase lose the blocking-flow advantage.
- **Directed/undirected bridge trap**: use two SCCs connected by a pair of opposite-looking but not equivalent directed edges. Undirected lowlink logic sees no bridge; directed reachability changes.
