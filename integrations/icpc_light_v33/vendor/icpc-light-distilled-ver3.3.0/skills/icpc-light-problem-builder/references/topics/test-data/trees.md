# Trees

## Topic-Specific Variants

- Reuse the same undirected tree under roots at a leaf, hub, centroid,
  diameter middle, inside the queried subtree, and on the boundary edge to a
  fixed-root parent. Keep the query set fixed when reroot semantics matter.
- `path`: maximum depth, recursion pressure, path-only shortcuts, endpoint
  boundary cases, and naive parent-climb LCA.
- `star`: maximum degree, center special cases, leaf-heavy LCA/query sets, and
  one-hub assumptions.
- `broom` and `caterpillar`: a long spine with one large leaf cluster, or
  leaves attached to many spine vertices. Use for diameter/height bugs,
  "almost path" dispatch, repeated light edges, and off-path contributions.
- `double-star`, `double-broom`, and `dumbbell`: two hubs or dense shells joined
  by a short edge or long bridge. Use for competing diameter endpoints, two
  plausible centers, bridge reroot transitions, and compressed-edge payloads.
- `centroid-split`: child subtree sizes near `n/2` (`n/2`, `n/2 - 1`, filler).
  Include one-centroid, two-centroid, and one-leaf-perturbed versions.
- `balanced-plus-fringe`: balanced binary/k-ary core with one deep chain or
  one high-degree leaf cluster.
- `random_prufer_with_planted_gadget`: random baseline with a planted broom,
  decomposition tie, dirty branch, double hub, or centroid cliff.
- Friendly global shape with one hostile branch: path plus medium side subtree,
  star plus long arm, balanced tree plus dirty child, or random tree plus one
  adversarial HLD/centroid tie.
- Many equal child subtrees, with exactly one child carrying the decisive
  future. Relabel vertices, permute edge order, and reverse sibling order after
  building the structure.
- Same terminal path with different off-path branches: one clean version where
  path-only summaries work, and one where off-path mass, labels, multiplicity,
  or updates change the answer.
- Same fixed-root Euler interval but different path/reroot semantics, to catch
  subtree intervals reused for current-root or path queries.
- Cross each important shape with query locality: same-chain, cross-light-edge,
  leaf-to-leaf, root-heavy, subtree-only, path-only, scattered terminals, and
  repeated terminals.
- For each mechanism, keep a tiny witness, exact-boundary case, noisy neighbor,
  and full-limit stress case.

## Strong Adversarial Solutions

- `shape_dispatch`: exact path/star/bounded-height solver, then diameter-spine
  or one-hub fallback. Kill with brooms, caterpillars, double-stars, and a
  medium off-spine branch whose contribution is decisive.
- `fixed_root_summary`: `tin/tout`, `down`, `up`, and maybe top two child
  contributions are precomputed for root `1`; changing-root queries are patched
  with one excluded child. Kill with same query/different root cases where the
  root lies inside the fixed-root subtree and outside information is
  non-invertible.
- `diameter_or_hub_key`: each node is compressed to distance from one hub,
  centroid, or two diameter endpoints. Kill with double-brooms, dumbbells, and
  symmetric deep branches where equal compressed keys hide different side state.
- `path_summary_overreach`: HLD path queries use a weak monoid such as sum,
  max, top one, or order-blind best. Kill with noncommutative path data,
  reversed endpoints, mixed path/subtree queries, and many light-edge jumps.
- `virtual_tree_only`: answer is computed only on marked nodes plus inserted
  LCAs. Kill with repeated terminals, root-as-terminal requirements, and
  compressed edges whose skipped internal nodes or side branches matter.
- `small_to_large_truncation`: DFS order and merge direction are correct, but
  bags keep only top-k colors, globally heavy labels, one best value, or another
  lossy summary. Kill with medium-frequency labels split across siblings.
- `canonical_order`: DFS order, child order, labels, heavy child, or centroid is
  treated as canonical. Kill with equal children, label shuffles, adjacency
  reversals, and exact ties plus/minus one vertex.
- `wrong_root_semantics`: exact LCA/Euler/depth data is used under the wrong
  root. Kill with roots outside/inside/equal to the query node and on subtree
  boundaries.
- `per_query_dfs_with_cache`: exact DFS per distinct endpoint/query set, cached
  for repeats. Use many unique scattered endpoints, shuffled order, and no
  repeated logical queries.
- `reroot_recompute`: recomputes DP after every root change but passes when
  roots are few or move locally. Use alternating roots at opposite leaves of a
  double-broom/dumbbell and interleave queries that force full-state changes.
- `naive_lca_parent_climb`: climbs parents or scans ancestors. Use maximum
  paths, broom spines, and leaf-to-leaf queries with LCA near the root.
- `pairwise_terminals`: handles virtual-tree-like queries by all-pairs among
  marked nodes. Use large terminal sets made of many leaves from several
  branches; include one case just above any public small-k cutoff.
- `wrong_direction_map_merge`: merges child maps into parent without
  small-to-large or clears/rebuilds too often. Use a caterpillar or balanced
  tree where many medium maps of comparable size are merged repeatedly.
- `block_rebuild_tree_ds`: rebuild baseline exactly and scans pending updates.
  Use many scattered updates across distinct heavy chains, then queries whose
  paths intersect most pending updates before each rebuild threshold.

## Killer Constructions

- HLD tie: at a node `u`, create equal-size children and route the decisive
  path/query through the child that loses under smallest-label, first-adjacent,
  last-adjacent, or insertion-order tie rules. Add an `s, s, s-1` version where
  one leaf changes the heavy child.
- HLD amplification: put equal heavy choices along a caterpillar spine. Mix
  same-chain easy queries with cross-chain hostile queries; for
  direction-sensitive DP, query both `(u, v)` and `(v, u)` across several chain
  heads.
- Centroid split: join two components of size `n/2` by an edge. Use answers
  that differ depending on which centroid is chosen as hub/root.
- Centroid cliff: use child sizes `floor(n/2)`, `floor(n/2)-1`, and filler,
  then add/remove one leaf. Hide one dirty branch below a symmetric child to
  break "equal centroid branches are interchangeable."
- Centroid-decomposition stress: update nodes in many centroid branches, then
  query a node whose answer must combine several branches, not just the nearest
  marked ancestor.
- DSU split: spread one important color/value across many light children so
  each child is below a top-k/heavy-frequency threshold, but the union crosses
  it. Add several medium-frequency colors to defeat top-one/top-few bags.
- DSU lifetime: make the heavy child clean and light children decisive. Alternate
  local and scattered subtree queries, include equal-size children where bag
  survival depends on tie choice, and add updates that make rare colors
  medium/global-heavy after sibling merges.
- Reroot semantics: same node set, different roots outside the queried subtree,
  inside it, equal to the query node, and on the fixed-root boundary edge.
- Euler boundaries: first/last child in DFS order, single-vertex subtree,
  whole-tree subtree, and sibling subtrees with adjacent `tin` ranges.
- LCA depth traps: leaves from opposite ends of a broom/dumbbell with LCA near
  the top, plus leaves whose LCA is deep inside a dirty branch.
- Path versus subtree: choose a path whose Euler coverage resembles one or two
  intervals but whose semantics require excluding side branches.
- Virtual-tree closure: terminals from three or more branches so consecutive
  Euler LCAs are insufficient. Include repeated terminals, deduplicated controls,
  required root insertion, and long compressed edges whose internal nodes carry
  weights, colors, updates, or side branches.
- Verification: brute force tiny rooted trees over all roots/query sets; use a
  rooted-state simulator or LCA/virtual-tree replay for compression-heavy
  families; keep label-shuffled and sibling-order-shuffled copies.
