- **[TLE] ODT/Chtholly or interval partition with bounded fragmentation.**
  Maintain a `set` of equal-value or equal-state intervals and process whole
  segments exactly. This is strongest when there are many assign/cover
  operations that collapse state, and much weaker when legal updates only split
  or scan fragmented intervals. Harden it with eager neighbor merges, sidecar
  Fenwick/segment-tree summaries for common queries, segment-count watchdogs,
  and local materialization before falling back to the interval set.

- **[TLE] Canonical data-structure name hiding a trace-sensitive shortcut.**
  Cite a known exact family such as segment tree, sqrt decomposition, rollback
  DSU, persistent tree, treap, splay, or Mo ordering, but replace the
  proof-critical part with a bounded dirty list, threshold rebuild, partial
  rollback, predictable priority, local cache, or simplified potential. Generic
  source checks confirm the named structure and miss the actual gamble. Audit the
  visible operation order, version topology, entropy source, boundary churn, and
  potential recharge instead of trusting the data-structure label.

- **[TLE] Dirty buffer plus periodic rebuild.** Keep a clean base structure,
  append updates to a dirty list, answer by combining the base answer with dirty
  corrections, and rebuild when the dirty budget is crossed. Stronger variants
  use two thresholds, block-local rebuilds, opportunistic flush before expensive
  queries, or several block sizes chosen from sample timing. The answers can be
  exact; only the amortization story is unproved.

- **[TLE] Rebuild-threshold oscillation treated as amortization.** Use a
  dirty-buffer, sqrt, scapegoat-style, or block rebuild route that switches mode
  only after a count crosses `B`. Friendly phases pay for the rebuilds, while
  hostile but legal traces keep the visible dirty set, segment count, block
  churn, or depth just below or just above the cutoff and force expensive work
  before each cleanup. Hysteresis, per-block caps, sampled thresholds, and
  work-counter triggers make it harder to expose, but do not replace a charging
  proof.

- **[TLE] Sqrt decomposition with optimistic partial-block cost.** Use block
  summaries for full blocks and scan boundary fragments or dirty cells directly.
  This is often a correct middle-score solution: tiny ranges, clustered updates,
  and simple summaries are handled well, while alternating boundary-heavy traces
  make every operation pay the expensive part. Make it harder to kill with
  special cases for tiny ranges, hot-block caches, and rebuilds only for blocks
  whose lazy state has become complicated.

- **[TLE] Mo-style or Hilbert-order offline processing outside its envelope.**
  Reorder queries to minimize state movement, then maintain the answer exactly
  with add/remove/update/rollback operations. This is semantically safe only for
  tasks where offline reordering is legal, but even then it can be too slow when
  modifications, expensive removals, large state deltas, or unlucky tie-breaks
  dominate the sorted order. A hardened version uses Hilbert order, tuned block
  sizes, batched rebuilds, symmetric add/remove code, and exact small-case
  routing.

- **[TLE] Trace-local trees and finger search.** Start searches, splits, or cuts
  from the last few accessed keys and rely on locality of operation traces. Full
  splay trees have a real amortized proof; the adversarial family copies only the
  intuition through partial splaying, single-rotation "spaly" code, finger
  caches, ropes, or sequence trees whose operation grammar changes the accounting
  argument. These routes can be exact and fast on clustered workloads, then fail
  when far-apart alternation or a moving hotspot keeps resetting the locality
  payoff.

- **[MLE/TLE] Lazy deletion heaps and stale containers.** Maintain extrema or
  top-k candidates with heaps, multisets, or queues, and delete old records only
  when they become relevant. Exact answers survive as long as every stale record
  is eventually recognized, but memory and cleanup time can grow far beyond the
  visible live state. Hardening includes garbage-ratio rebuilds, hot-id exact
  maps, duplicate buckets, and periodic heap reconstruction from current values.

- **[MLE/RE] Persistent path-copy node explosion.** Use persistent segment
  trees, tries, treaps, or copy-on-write containers where each update creates a
  small number of nodes. This is exact and usually robust on linear version
  histories, but high fan-out, many attached queries per version, or large
  branching depth can exhaust the node pool or memory limit. Strong versions recycle
  dead nodes, compact old versions, store tiny versions as deltas, or split hot
  and cold metadata.

- **[TLE/MLE] Persistent or rollback brute-force replay.** Keep roots,
  checkpoints, deltas, or common ancestors, but answer by replaying the short
  suffix, diff list, or branch-local brute force from that saved state. It looks
  exact when every version differs by only a few operations. The resource failure
  appears on version trees with many siblings: each replay is small in isolation,
  but the same prefix and cleanup work are paid again across many branches.

- **[TLE/MLE] Rollback with amortized shortcuts still inside.** Rollback data
  structures require every mutation cost to be real, not merely amortized. A
  tempting worst-case-complexity-risk route keeps path compression, cached roots, lazy
  cleanup, or partially shared metadata and logs "enough" state to look safe.
  It may stay semantically correct if every change is recorded, but the rollback
  stack, replay cost, or root-walk cost can blow up when the version tree
  branches heavily.

- **[TLE/MLE] Dynamic sparse structures over a dense reality.** Dynamic segment
  trees, sparse Fenwick trees, sparse maps, and implicit grids allocate only
  touched nodes and assume the active coordinate set stays small. They are exact
  for endpoint-driven or low-density streams, and can be hardened with sentinels,
  hot-block densification, and periodic compaction. The resource failure appears
  when legal operations gradually fill the supposedly sparse domain or create
  many short-lived nodes.

- **[TLE] Segment-tree-beats-style potential without the full proof.** Keep
  max/second-max, min/second-min, counts, or similar summaries and descend only
  into "dangerous" nodes. This can be exact even when the complexity proof is
  missing: every query/update returns the right value, but operations can keep
  recreating the expensive state the potential argument was supposed to consume.
  Watchdogs on visited nodes, local materialization, and conservative fallback
  make the adversarial solution much harder to separate from a real one.

- **[TLE] Split/merge structures with rechargeable potential.** Segment-tree
  merge/split, mergeable treaps, value-domain partitions, and small containers
  often rely on "each node is merged once" or "each element moves logarithmically
  many times." That reasoning collapses when legal operations can split after
  merge, undo ownership, or rebuild the bad state. Tombstones, delta logs, and
  periodic flattening help, but they usually shift the hidden cost rather than
  proving it away.

- **[TLE/RE] Randomized balanced structures with weak or visible entropy.**
  Treaps, skip lists, randomized linking, and implicit randomized trees rely on
  priorities or promotion levels being independent of the input trace. They are
  strong worst-case-complexity-risk candidates when the seed, priority formula, duplicate
  ordering, or split/merge sequence is predictable enough for legal keys and
  operations to correlate with the shape. Height watchdogs, reseeding, inorder
  rebuilds, and exact routing for tiny trees improve survival, but the expected
  proof is gone once the balancing choices are input-visible.

- **[TLE] Small-to-large used in non-monotone ownership.** Merge smaller maps,
  sets, tries, or color counters into larger ones while assuming items only move
  toward larger containers. This is exact for monotone tree traversals, but a
  wrong worst-case route for rerooting, rollback, delete/replace, split, or
  repeated "move subtree" tasks. The hard-to-hack version keeps lazy tombstones
  and rebuilds dirty containers when stale mass becomes visible.

- **[RE/TLE] Recursive exact structure with unsafe depth.** Recursive segment
  trees, implicit treaps, divide-and-conquer over time, persistent traversals,
  and DFS-style rollback can be logically perfect and still die on stack depth or
  frame cost. Raising recursion limits, tail-recursing "small" branches first,
  or using larger stack settings makes this survive normal tests. It remains a
  first-class RE/TLE adversarial solution when the legal structure can be a path, broom,
  deep version chain, or skewed split tree.

- **[TLE] Cache-local exact structure with input-visible locality only.** Use a
  simpler exact structure with excellent constants: flat vectors, packed blocks,
  branch-regular loops, or sorted-vector rebuilds. Treat this as a plausible
  adversarial solution only when the locality claim follows from the visible operation
  trace, key order, range width, or topology shape. It fails when legal input
  randomizes labels, forces many middle inserts/deletes, alternates hot and cold
  regions, or moves the hotspot just before the layout stabilizes.

- **[WA] Simplified lazy-tag algebra.** Implement only the common tag
  interactions: assign plus add, min plus add, cover plus flip, chmin without the
  full invariant set, or "latest tag wins" composition. Exact-solve short
  intervals, flush nodes after mixed overlaps, or route rare tag types through a
  slower handler. This is a semantic adversarial solution when nested mixed updates
  require information the simplified tag stack discarded.

- **[WA] Sparse coordinate compression that loses gap semantics.** Compress only
  coordinates mentioned by operations, maybe plus sentinels and immediate
  neighbors, and treat huge gaps as irrelevant. This is safe only when the answer
  truly depends only on endpoint order. It becomes a semantic shortcut when gap
  length, adjacency, missing interior points, or future dynamic insertions affect
  legality or value.

- **[WA] Approximate top-k or bounded candidate frontier.** Keep only the largest
  few values, nearest few neighbors, most frequent few colors, or a bounded
  frontier per block/node, then exact-check inside the shortlist. This is
  plausible when the answer usually comes from an extreme. It breaks when many
  near-tied candidates trade places after delayed updates or when the true
  witness is deliberately non-extreme.

- **[WA/TLE] Cached query answers with weak invalidation.** Memoize range
  answers, component summaries, nearest active elements, block aggregates, or
  auxiliary tree state and invalidate only the touched node/block/component.
  Epochs, dirty rectangles, and cheap certificates can make this hard to hack.
  The hidden assumption is that dependencies are local; if they are not, the
  route either serves stale answers or spends too much time proving cache entries
  are still valid.

- **[WA/TLE] Offline ordering assumption.** Sort queries, batch updates, divide
  time into phases, or process events in a convenient order while assuming the
  answers are order-independent. Mo-like, CDQ-like, and divide-over-time routes
  are especially tempting: maintain an almost-correct current state, then
  simplify add/remove symmetry, rollback, cross-phase cleanup, or online
  barriers. The verdict depends on whether the maintained state is incomplete or
  merely too expensive to move.
