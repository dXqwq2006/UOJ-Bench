# Topic Routing

Use the topic leaves vendored under `references/topics/` as a bounded candidate
generator. Route by proof mechanism and failure mechanism, not merely by nouns
in the story.

## Select Leaves

Choose 1--3 primary topics and at most two cross-cutting topics. Read only the
matching local leaf files. Do not look up the source heavy skills, load every
vendored leaf, or adopt any subtask, ledger, review-count, quota, or rerun
obligation mentioned in source material.

Use this alias map:

| observed mechanism or alias | canonical route | adversarial leaf | test-data leaf |
| --- | --- | --- | --- |
| array, prefix/suffix, two pointers | `arrays` | [arrays](topics/adversarial/arrays.md) | [arrays data](topics/test-data/arrays.md) |
| bitwise, bitmask, xor basis, carry/borrow | `bitwise` | [bitwise](topics/adversarial/bitwise.md) | [invariant data](topics/test-data/invariants.md) |
| DP, state compression, transitions | `dynamic-programming` | [dp](topics/adversarial/dp.md) | [dynamic programming data](topics/test-data/dynamic-programming.md) |
| divide-and-conquer DP, CHT, monotone queue/opt | `dp-optimizations` | [optimization](topics/adversarial/optimization.md) | [DP optimization data](topics/test-data/dp-optimizations.md) |
| graph modeling, connectivity, traversal | `graphs` | [graphs](topics/adversarial/graphs.md) | [graphs data](topics/test-data/graphs.md) |
| Dijkstra, Bellman-Ford, distances, path counts | `shortest-paths` | [shortest paths](topics/adversarial/shortest-paths.md) | [shortest-path data](topics/test-data/shortest-paths.md) |
| matching, max-flow, min-cut, cost-flow | `matching-and-flow` | [matching and flow](topics/adversarial/matching-and-flow.md) | [graphs data](topics/test-data/graphs.md) |
| tree DP, rerooting, LCA, subtree structure | `trees` | [trees](topics/adversarial/trees.md) | [trees data](topics/test-data/trees.md) |
| string matching, borders, automata | `strings` | [strings](topics/adversarial/strings.md) | [strings data](topics/test-data/strings.md) |
| hash, rolling hash, fingerprint | `hashing` | [hashing](topics/adversarial/hashing.md) | [hash data](topics/test-data/hash.md) |
| segment tree, Fenwick, DSU, rollback | `data-structures` | [data structures](topics/adversarial/data-structures.md) | [data-structure data](topics/test-data/data-structures.md) |
| intervals, range assignment, ordered disjoint tree | `intervals` | [data structures](topics/adversarial/data-structures.md) | [interval data](topics/test-data/intervals.md) |
| amortized structure, ODT fragmentation, rebuild | `amortized-structures` | [data structures](topics/adversarial/data-structures.md) | [amortized data](topics/test-data/amortized-structures.md) |
| greedy, sorting, exchange, tie choice | `greedy` | [greedy](topics/adversarial/greedy.md) | [greedy data](topics/test-data/greedy.md) |
| game, minimax, Grundy/SG, move history | `games` | [games](topics/adversarial/games.md) | [games data](topics/test-data/games.md) |
| invariant, parity/orbit, propagation, conservation | `invariants` | [invariants](topics/adversarial/invariants.md) | [invariant data](topics/test-data/invariants.md) |
| permutation, cycles, swaps, inversions | `permutations` | [permutations](topics/adversarial/permutations.md) | [permutation data](topics/test-data/permutations.md) |
| deterministic DFS/backtracking, branch-and-bound, A* | `search` | [search](topics/adversarial/search.md) | choose the matching structural data leaf |
| simulation, event order, phases, undo/redo | `simulation` | [invariants](topics/adversarial/invariants.md) | [simulation data](topics/test-data/simulation.md) |
| randomized search, restart, annealing, beam/evolutionary heuristic | `randomized-search` (P3 conditional) | [randomized search](topics/adversarial/randomized-search.md) | [search/restart data](topics/test-data/search-and-restarts.md) |
| randomized implementation, collision amplification, adversarial expected time | `probability-and-randomization` (P3 conditional) | [randomized search](topics/adversarial/randomized-search.md) | [probability/randomization data](topics/test-data/probability-and-randomization.md) |
| constructive witness, output construction | `constructive` | [constructive](topics/adversarial/constructive.md) | [invariant data](topics/test-data/invariants.md) |
| geometry, orientation, containment, hull | `geometry` | [geometry](topics/adversarial/geometry.md) | [geometry data](topics/test-data/geometry.md) |
| floating point, epsilon, cancellation | `floating-point` | [numeric stability](topics/adversarial/numeric-stability.md) | [floating-point data](topics/test-data/floating-point.md) |
| modular arithmetic, divisibility, sieve | `number-theory` | [number theory](topics/adversarial/number-theory.md) | [number-theory data](topics/test-data/number-theory.md) |

## Light Boundary for Restored Leaves

`bitwise`, `games`, `invariants`, `permutations`, `simulation`, and
theorem-backed deterministic `dp-optimizations` are ordinary batch mechanisms.
Route them normally when their proof or failure mechanism matches; their
presence does not by itself raise the problem to P3.

Treat exact deterministic `search` as an ordinary route only when the visible
constraints give a bounded search model and the candidate is judged by normal
AC/WA/TLE/MLE semantics. If correctness or termination relies on a heuristic
cutoff, beam width, local basin, restart count, seed, or wall-clock budget,
route the investigation through a recorded P3 round instead.

The `randomized-search`, `probability-and-randomization`, and
`search-and-restarts` leaves are P3-conditional specialist leaves. Read them
only for an actual randomized, expected-time, restart, local-search, or
collision-risk implementation--not merely because the mathematical problem
mentions probability or because an exact solver uses DFS. Every admitted
breaker must be deterministic or fixed-seed, reproducible, independently
checked, and bounded by the recorded round plan. Escalate if release confidence
would depend on a lucky seed, an unknown runtime/hash seed, nondeterministic
timing, unbounded search, or an attack that only manifests after the declared
limit.

These restored leaves do not expand the Light profile to interactive,
communication, score-driven, partial-scoring, or other nonstandard judging.

Examples:

- Route rerooting tree DP to `trees` and `dynamic-programming`.
- Route optimized DP whose proof assumes monotonicity to
  `dynamic-programming` and `dp-optimizations`.
- Route Dijkstra with path counting to `shortest-paths` and `graphs`.
- Route dynamic rolling hash to `strings`, `hashing`, and optionally
  `data-structures`.
- Route interval assignment with adversarial fragmentation to `intervals`,
  `data-structures`, and optionally `amortized-structures`.
- Route polygon containment to `geometry` and optionally `floating-point`.
- Route a Grundy decomposition with a shared move budget to `games` and
  optionally `invariants`.
- Route an exact branch-and-bound solver to `search`; add the P3-conditional
  `search-and-restarts` data leaf only if the candidate actually uses restarts,
  local repair, or another heuristic cutoff.

## Filter Entries by Risk

Always check relevant low-cost fundamentals: boundaries/off-by-one, integer
width, stale multi-case state, duplicates/ties, order dependence, missing
state, invalid greedy reasoning, asymptotic complexity, special shapes, and
input-contract misunderstandings.

Use mechanism-specific leaves for cross-bit carry, coupled game components,
false DP monotonicity, equal-distance provenance, rerooting, fragmentation,
rollback pollution, permutation-cycle coupling, event-order simulation, border
ladders, geometric degeneracy, and propagation invariants only when the
mechanism matches.

Treat fixed-parameter anti-hash, engineered SPFA killers, hostile amortized
schedules, large-scale cancellation, exhaustive geometry degeneracy, heuristic
local basins, and complex protocol attacks as specialist signals. A
deterministic, independently checkable, bounded version may be handled by P3
within its recorded round plan. Escalate when release confidence requires an
unbounded, external-specialist, non-reproducible, or post-limit attack.

For every borrowed idea, verify `applicable_when` and `not_applicable_when` (or
derive those conditions when the leaf is unstructured). Translate the idea
into a problem-specific wrong assumption, implementation shape, small witness,
and scalable breaker. Do not count a catalog entry as coverage until an
implemented route or concrete test produces evidence.
