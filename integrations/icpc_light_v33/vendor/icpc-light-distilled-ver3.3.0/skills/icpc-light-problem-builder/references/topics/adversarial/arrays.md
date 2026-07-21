- [WA] Multi-tie-break greedy with local repair: assumes only one locally ambiguous choice separates greedy from optimum. Run the same greedy under natural tie policies such as left/right, high/low slack, stable/reverse, then exactly repair tiny windows around ties, duplicate plateaus, and low-margin moves. It remains wrong when equal-looking choices repeat or the bad early choice changes a far suffix.

- [WA] Tiny pivot or split candidate set: assumes the best cut, pivot, moved block, or turning point lies at an endpoint, prefix/suffix extremum, local order break, duplicate boundary, or top proxy position. It is strong when all candidates are fully rescored, but misses interior plateau points or two interacting pivots that no one-candidate proxy ranks highly.

- [TLE] Dense candidate-set exact evaluation: assumes suspicious cuts, pivots, repair windows, or cycle anchors stay sparse. All-equal arrays, monotone arrays, alternating high/low permutations, and every-position-tied greedy traces can make the "small" candidate set linear; exact O(n) validation per candidate becomes quadratic without relying on implementation accidents.

- [MLE] Materialized interval universe: assumes only a few subarrays, windows, or segment operations are relevant. Storing all candidate intervals, covered ranges, overlap edges, or per-interval scores passes one-bad-segment tests, then explodes on flat arrays where every interval has the same proxy or on layered arrays where every boundary pair is plausible.

- [MLE] Materialized pair universe: assumes each position has few partners. Equal-value pairs, candidate inversions, dominance edges, swappable pairs, and cross-cycle pairings become dense on duplicate blocks, reverse-sorted permutations, or interleaved value layers. Streaming the relation is the real fix; capping it turns the first omitted dense layer into WA.

- [TLE] Repeated full rebuild after updates: assumes an update invalidates only a local summary. Recomputing prefix/suffix arrays, nearest boundaries, bucket order, cycle decomposition, or segment scores after every swap, reversal, point change, or range shift passes short-update tests, but hits O(nq) when each update changes a global landmark or one long operation touches most positions.

- [TLE] One-sided range-sum structure: assumes the operation mix is query-heavy or update-heavy. A plain array gives O(1) point updates but O(n) range queries; prefix sums give O(1) range queries but O(n) point updates. Alternating global queries with point edits forces O(nq), while Fenwick or segment-tree style summaries are needed to keep both sides logarithmic.

- [TLE] Lazy-operation trace replay: assumes the pending operation log stays short or cancels often. Recording reversals, cyclic shifts, range additions, stable bucket moves, or deferred repairs and replaying them for every query works on few-query data; alternating overlapping ranges make every later lookup depend on most earlier operations. The input-visible trigger is an operation sequence with deep overlap.

- [MLE] Lazy-operation trace retention: assumes deferred updates can be stored until a convenient flush. Per-block pending tags, per-position operation histories, or copied repair windows remain small on disjoint ranges; nested ranges, repeated whole-array shifts, and alternating half-array reversals create O(nq) retained trace state unless equivalent operations are actually composed.

- [WA] Segment-tree state without a real monoid: assumes a range answer can be merged from child summaries regardless of parenthesization. Min/sum-like samples pass, but non-associative choices such as rounded averages, left-biased tie winners, truncated candidate sets, or state that forgets prefix/suffix witnesses make the answer depend on how the query interval is decomposed.

- [WA] Lazy tag algebra shortcut: assumes range operations can be stacked as one flag or applied in any order. Alternating assignment/addition, reverse/rotate, affine maps, or chmin/chmax-like caps on overlapping ranges expose missing composition, missing identity reset, or a mapping that does not distribute over the stored summary. Pushing tags eagerly usually turns the idea into TLE rather than fixing it.

- [RE] Recursive permutation or Cartesian decomposition depth: assumes cycles, blocks, divide-and-conquer pivots, or Cartesian-tree height are balanced. A single n-cycle, sorted array Cartesian tree, nested interval chain, or always-endpoint pivot gives linear recursion depth. This is a model-visible RE family; truncating the recursion changes it into WA rather than a legitimate fix.

- [TLE] Locality-shaped block heuristic: assumes active positions form a few contiguous runs, so block scans and bucket passes are effectively streaming. Blocky weak tests pass; alternating values, bit-reversal order, interleaved cycles, or checkerboard updates create many tiny active runs at every layer and force repeated scattered passes. The failure is the run count implied by the array, not a generic cache complaint.

- [WA] Prefix/suffix one-state summary: assumes the middle interaction is captured by one left summary and one right summary. Max/min variants, split neighbors around equal runs, and a narrow exact middle patch hide small failures. It breaks when two internal regions interact or when many splits are equivalent under the summary but not under the real objective.

- [TLE] Prefix/suffix rescoring with dense equal splits: assumes only a few split points need rich recomputation after the cheap summary. Long plateaus or repeated local extrema can make every split equally suspicious; rescoring a middle window for each split is quadratic, while selecting top-K by proxy risks dropping the only split that satisfies the global constraint.

- [WA] Sparse-table overlap on a non-idempotent query: assumes the O(1) two-block RMQ trick works for any range aggregate. It is valid for min/max-like idempotent operations; sums, xor-with-witnesses, ordered states, and counted answers double-use the overlapped middle and silently drift. Splitting into disjoint powers of two avoids WA but raises query time.

- [TLE] Sparse-table rebuild under updates: assumes a static range-query table can absorb occasional edits. Sparse tables are an immutable-array structure; one point change can require rebuilding O(n log n) precomputed cells. Tests with all queries first pass, while alternating edits and queries make the repair cost O(qn log n).

- [MLE] Sparse-table state inflation: assumes O(n log n) cells are still cheap after storing witnesses, multiple metrics, versions, or per-value layers. Large n with many independent tables fails before any tricky correctness case appears; compressing the table by dropping witnesses changes the failure into WA on ties or reconstruction queries.

- [WA] Rank-compressed value geometry: assumes replacing values by ranks preserves all information needed by the array logic. It can keep original min/max, gap parity, or exact small-gap buckets to survive obvious tests, then run the main DP, greedy, or two-pointer logic on compressed ranks. It fails when absolute gaps, threshold distances, weighted moves, or adjacent-value semantics make two arrays with the same order require different choices.

- [WA] Single frontier per endpoint: assumes each left endpoint has one best right endpoint, or each value bucket has one advancing boundary, so discarded partners never become useful. Strict/non-strict variants, small backward checks, and exact repair near local violations make it look like a normal two-pointer solution. It fails when a later budget, max-min interaction, negative contribution, or second-nearest witness revives a partner behind the frontier.

- [WA] Distinct-value skeleton with duplicate-aware fill: assumes equal values are interchangeable after solving the all-distinct version. Stable, reverse-stable, nearest-slot, and leftmost-hole fills plus exact handling of small equal groups are plausible. Large layered duplicate structures still fail when equal values have different legal neighborhoods, deadlines, costs, or future compatibility.

- [TLE] Duplicate group exactification without a structural bound: assumes troublesome equal-value blocks stay small. Matching, DP, or factorial repair inside each group is exact on small plateaus and weak low-duplicate tests; one giant equal block or many medium equal layers makes the exact path exceed time. A hard size cap is not a TLE fix if the discarded ordering is semantically important.

- [WA] Sort-by-one-key ordering: assumes a local exchange key is globally transitive enough. Stable/reverse ties, adjacent swaps inside equal-score buckets, and legality checks make the route robust on ordinary data. It breaks when equal or near-equal keys need a coordinated order chosen by future constraints rather than by immediate score.

- [TLE] Dense tie-bucket expansion: assumes equal-score buckets are small enough to enumerate, locally sort by several secondary keys, or try adjacent swaps. Weak random data has tiny buckets; many identical values, equal slack, or repeated deadlines create a bucket of size n and turn bucket repair into quadratic or worse. If only a few bucket permutations are kept, the failure becomes WA.

- [WA] Nearest-boundary stack shortcut: assumes nearest smaller/greater or first dominating boundaries contain all useful structure. Strict/non-strict variants and plateau repair harden the idea. Second-nearest witnesses, long equal runs, sibling interactions in a Cartesian tree, or delayed effects that skip over the nearest boundary remain outside the model.

- [TLE] Dynamic nearest-boundary maintenance by local patching: assumes a point update or small reversal only changes nearby stack boundaries. In monotone arrays, a single changed endpoint can alter nearest-smaller/greater links across a suffix; repeated edits rebuild long chains. Weak tests with random values pass because expected affected length is small, but sorted or plateau data makes it linear per update.

- [TLE] Monotone-stack work charged per query instead of per element: assumes the linear push-once/pop-once amortization survives repeated windows, repeated thresholds, or rollback queries. Rebuilding the stack for each candidate split or each sliding-window query is O(nq) or O(nk); the linear guarantee only applies when the scan is shared and each array index leaves the structure once.

- [WA] Near-sorted or few-bad-positions repair: assumes the answer is close to sorted order, identity permutation, or another canonical arrangement. Mark locally violated positions, expand by a small radius, and exact-solve or beam-search only that focus set. Dispersed small mistakes, one long cycle, or a global parity/budget constraint can make every local repair look good while the global answer is wrong.

- [TLE] Inversion-budget exact search: assumes inversions, bad adjacencies, or misplaced elements are few because samples look smooth. A capped DP or search over bad positions is strong on nearly sorted arrays; reverse order, block swaps, or one long permutation cycle make the bad set dense. Falling back to greedy past the cap should be classified as WA, not as solving the resource issue.

- [WA] Local swap/reversal improvement: assumes a local optimum under short swaps, short reversals, or single moves is close to global optimum. Several canonical starts and a menu of edit types cover many visible cases. The gap is a configuration that needs two distant moves, a long reversal, or a temporary score decrease before the real improvement appears.

- [TLE] Segment-operation simulation: assumes swaps, reversals, rotations, range shifts, and local repairs touch short ranges. Directly rewriting every affected element can pass tests with small operations, but alternating whole-array and half-array operations gives quadratic work. Lazy tags, ropes, implicit treaps, or difference arrays are the intended escape; stopping after a touch budget creates WA.

- [TLE] Sqrt decomposition used past its budget: assumes O(sqrt n) per operation is close enough to logarithmic. It may pass when q is small or blocks are friendly, but n and q both large make q sqrt n operations visible. If each block action also scans a sorted list, rebuilds a block, or does a nonconstant add/remove, the hidden factor becomes the dominant cost.

- [TLE] Mo-order dependency ignored: assumes any offline order of range queries gives small pointer motion. Mo's algorithm relies on block order and cheap add/remove operations; original order, sorting only by one endpoint, or adversarial alternating ranges can move the maintained window across most of the array for most queries. Heavy add/remove state multiplies the resource failure.

- [MLE] Merge-sort-tree state where a scalar summary was needed: assumes storing sorted lists or rich per-node vectors is only a small upgrade over a segment tree. Static versions already cost O(n log n) memory; adding updates, multiple witnesses, per-query snapshots, or copied node lists can exceed memory on plain large arrays.

- [WA] Cycle-local permutation solve: assumes permutation cycles can be optimized independently or nearly independently. Small cycles can be exact, while large cycles try anchors such as minimum index, minimum value, or best local gain. It fails on one long cycle with delayed effects or on cross-cycle constraints hidden by symmetric indices.

- [TLE] Dense cross-cycle candidate matching: assumes only a few cycles interleave or compete for the shared budget. Building all cross-cycle repairs, exchange edges, or anchor pairs is fine on many-small-cycle weak tests, but two long interleaved cycles or many cycles crossing the same value range create a dense bipartite candidate surface.

- [WA] Cross-cycle independence under a shared budget: assumes costs or choices add over permutation cycles or array blocks. Exact per-block optimization plus greedy allocation of leftovers is plausible when blocks are weakly coupled. The real failure is a shared parity, count, or resource constraint where a locally second-best choice in one block unlocks a better total arrangement.

- [WA] Sliding window on an almost-monotone predicate: assumes extending the right end and moving the left end changes feasibility monotonically. Separate sign regimes, prefix extrema, and both shrink policies harden it. Negative values, removals, or constraints involving both max and min can make a discarded left endpoint become useful again.

- [WA] Frequency-only reconstruction: assumes the multiset or value counts determine most of the answer. Construct from counts, then fill positions by stable, reverse, or nearest-target policies and repair tiny conflicting groups. Arrays with the same frequencies but different order-sensitive constraints expose the lost position information.

- [WA] Prefix-extrema landmark compression: assumes new maxima, new minima, and their suffix analogues are enough representatives. Local breaks and duplicate boundaries make the compressed array less fragile. Long stretches with no new extrema can still contain the only index whose position matters to the objective.
