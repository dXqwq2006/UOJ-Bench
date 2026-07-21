# Data Structures

## Topic-Specific Variants

- Phase order: all updates then all queries, strict update/query alternation,
  query-heavy with rare disruptive updates, and rebuild-threshold oscillation.
- Locality: one hotspot, two far-apart hotspots, hotspot migration just before
  amortization pays off, and full-range operations mixed with point punctures.
- Boundary shape: length `1`, length `B-1/B/B+1`, full range, adjacent ranges,
  crossing ranges, and many events at the same coordinate.
- Multiplicity: all equal values, long platforms plus one spike, repeated keys,
  repeated endpoints, repeated unions, and duplicated max/min priorities.
- Version shape: one chain, one star from an old version, deep branch tree,
  rollback to the same checkpoint many times, and sibling versions differing by
  one update.

## Strong Adversarial Solutions

- Wrongly implemented data structures: "spaly" (single-rotation splay), wrong segment tree beats, etc.
- Wrong complexity: n^2, n^{2/3}, sqrt or one-more-log solutions with small constants, when they should be hacked
- Sophisticated worst-case-complexity-risk solutions: large-to-small merge, bruteforce pushdown, bruteforce merge, etc.
- ODT/interval partitions stay small because random updates merge often.
- Sqrt/block decompositions only pay for whole blocks, not boundary churn.
- Rollback DSU can use path compression, or can skip reverting size/rank/parity
  metadata after redundant unions.
- Hash tables are average-case safe under judge data, and rehash/collision costs
  are not part of the adversary.
- Segment-tree beats state remains valid after simplified `push`, or after
  threshold-equal values are repeatedly nudged.
- Treap/splay depth stays logarithmic without a real randomized or amortized
  proof.

- Exact-island heuristics: pass `n <= K`, short ranges, few versions, or obvious
  suspicious buckets. Kill them with full-size instances whose local windows all
  look harmless but whose global interaction differs.
- Telemetry heuristics: rebuild only when segment count, dirty size, depth, or
  bucket size crosses a cap. Keep every statistic just below the cap, or trigger
  the cleanup repeatedly with slightly shifted hotspots.
- Semantic shortcuts: assume commutative update order, endpoint-only
  compression, one representative per block/bucket, or visible connectivity only.
  Use paired traces with the same coarse summary and different answers.
- Local-patch heuristics: brute force one dirty block, one bad bucket, or one
  version branch. Spread the decisive defects across two or more blocks/branches
  so no single fallback sees the whole witness.

- Prefer TLE families with a proof-scale gap, not constant-only hopes: e.g.
  `Theta(qB)` dirty scans at `B ~= sqrt(n)`, `Theta(q * segments)` ODT scans, or
  `Theta(q log n)` intended versus `Theta(q sqrt n)`/`Theta(q^2)` wrong work.
- Mix clean and noisy cases. A clean worst case explains the trace; noisy
  variants shift boundaries, reorder harmless operations, and randomize labels so
  hard-coded detectors do not skip it.
- Force expensive work immediately before each cleanup: query all dirty updates
  before rebuild, split many ODT segments before assigning over them, or jump
  block boundaries before cached summaries stabilize.
- Include resource evidence in the package notes when possible: operation-count
  estimates, expected segment/bucket/depth maxima, and measured verdicts against
  intended, brute, and optimized-adversarial routes.

## Killer Constructions

**ODT Split Explosion**

- Start with one plateau `[1,n]`.
- For `i = 1..k`, assign point or tiny interval `p_i` alternating values so the
  segment count grows: odd/even positions, then shifted odd/even positions.
- Query or update `[1,n]` after the punctures so the adversarial solution must traverse
  all fragments.
- Add heal-then-puncture variants: assign `[1,n]` to one value, then puncture
  `Theta(n)` separated points again; repeat enough times to defeat one rebuild.
- Noise knobs: randomize puncture order, keep value alphabet small, add a few
  adjacent punctures that merge, and shift the dense fragment zone away from the
  center.

**Sqrt / Block Boundary**

- Choose the likely block size `B ~= sqrt(n)` and use ranges of length
  `B-1`, `B`, `B+1`, `2B-1`, plus full blocks with one endpoint outside.
- Alternate updates crossing adjacent block boundaries with point queries on the
  first/last element of each block.
- For buffered rebuilds, issue `B-1` dirty updates touching many blocks, then a
  query intersecting all dirty updates; repeat with the hotspot moved by one
  block.
- Include same compressed block IDs but different real gaps when coordinate
  compression or bucket indexing may lose distance semantics.

**DSU Rollback / Path Compression**

- Build a long chain with size/rank/parity metadata, checkpoint, then repeatedly
  rollback to different depths and query old states.
- Add redundant unions inside an already connected component; wrong rollback
  code often logs nothing but still changes rank, size, distance, or parity.
- Create sibling branches from the same checkpoint: branch A changes one edge,
  branch B changes another, then compare connectivity/component metadata in both.
- If path compression is present, query deep nodes before rollback so parent
  pointers are compressed across history; later old-version queries should expose
  the pollution.

**Hash-Table Collision / Rehash**

- If the key hash is known or default integer hashing is used, generate many keys
  with identical low bits or identical `key % bucket_count` for common bucket
  counts; keep a fallback random-key case for non-targeted submissions.
- Stress rehash by inserting just past load thresholds, erasing most entries,
  then inserting another colliding wave; repeat with queries after each wave.
- Use duplicate-heavy keys and near-duplicate keys together: one family checks
  correctness under multiplicity, the other checks collision-chain runtime.
- Do not rely only on one magic bucket count. Provide several seeds with shifted
  arithmetic progressions and record the key formula used.

**Segment-Tree Beats Bad Pushes**

- Use long platforms where `max`, `second max`, `min`, and `second min` are equal
  or differ by `1`.
- Apply small alternating `chmin`, `chmax`, and `add` updates so a node is barely
  pushable, then no longer pushable, then barely pushable again.
- Mix full-cover updates with point updates under the same ancestor, then query
  the ancestor before all children are normalized.
- Pair traces with the same multiset of values but different update order; wrong
  push/tag composition often preserves the multiset but not the answer.

**Treap / Splay Degeneracy**

- For deterministic treaps, insert monotone keys, repeated keys, or keys whose
  priority formula is monotone/colliding under the known seed.
- For weak randomized treaps, use many split/merge operations at adjacent
  positions so depth spikes cannot be amortized by random-looking inserts.
- For single-rotation "spaly" or finger-search shortcuts, alternate accesses
  between far-apart keys, then add short local bursts so simple locality
  detectors look plausible.
- For implicit sequence trees, repeat cut/paste/reverse near the ends and across
  the middle; include tiny exact witnesses plus near-limit traces with the same
  operation grammar.
