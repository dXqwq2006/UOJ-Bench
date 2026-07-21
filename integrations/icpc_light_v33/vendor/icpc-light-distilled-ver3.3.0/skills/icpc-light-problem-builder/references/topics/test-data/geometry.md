# Geometry

## Topic-Specific Variants

- Exact degeneracy paired with near-degeneracy: collinear vs almost collinear, tangent vs almost tangent.
- Same-angle or same-coordinate batches where ordering policy changes the constructed object.
- Large-coordinate small-residual predicates: cross product or distance difference is tiny after huge terms.
- Local degenerate gadget embedded inside an otherwise generic point/segment set.
- Non-convex polygon shells: one reflex notch, many shallow notches, spiral chains, and comb boundaries.
- Polygon containment with holes if the statement allows them; pair each valid hole case with an invalid or touching-hole case if the validator/checker must reject it.
- Self-touching, repeated vertices, zero-length edges, overlapping consecutive edges, and vertex-on-edge contacts when the input contract allows non-simple polygons or needs to reject them cleanly.
- Convex-only versus arbitrary-polygon twins: same bounding box, area scale, and number of vertices, but one family has a hidden reflex vertex.
- Point-in-polygon boundary cases: point equals vertex, lies on an edge, lies on a hole boundary, lies on a ray-casting horizontal edge, or lies just outside by one grid unit.
- Rotating-calipers inputs that violate the hidden precondition: unsorted convex hull vertices, duplicate hull vertices, all-collinear hull, non-strictly convex hull with long flat edges, or a non-convex polygon passed as if convex.
- Orientation overflow families: coordinates near the stated maximum where `(bx-ax)*(cy-ay)` and `(by-ay)*(cx-ax)` fit individually only in wider-than-64-bit arithmetic or cancel to a small sign.
- Floating threshold pairs: same combinatorics with an exact residual just below and just above `EPS`, at unit scale, huge scale, and mixed scale.
- Hard-to-hack heuristic cases: legal-looking geometric witnesses with many local repairs available, but only one global topology reaches the optimum.

## Strong Adversarial Solutions

- General-position reasoning that survives random tests but fails on one exact degeneracy: collinear triples, tangent contacts, zero-length edges, repeated points, overlapping segments, or vertex-on-edge contacts.
- Floating orientation or intersection predicates that assume the same combinatorial structure after applying `EPS`, even when exact and near-exact cases differ.
- Angle, sweep-event, or hull sorting with a harmless-looking tie policy for same ray, same line, same coordinate, horizontal edge, or shared endpoint batches.
- Large-coordinate implementations that treat overflow as only a precision issue, not a source of discrete topology changes in orientation, area, distance, or containment.
- Polygon code that assumes convex, simple, hole-free, consistently oriented, and duplicate-free input unless the statement explicitly says otherwise.
- Boundary semantics that collapse inside, outside, on boundary, tangent, touching, overlapping, and shared endpoint into one easy edge case.
- Point-in-polygon code using ray casting or winding that ignores horizontal edges, vertex hits, hole boundaries, or hole orientation.
- Rotating-calipers code that works on any visually convex-ish point order instead of requiring a correctly ordered convex polygon with a documented collinearity policy.
- Hull, calipers, and counting code that removes duplicate points or collinear middle points even when the task is containment-, witness-, or multiplicity-sensitive.
- One global `EPS` reused for equality, ordering, intersection, containment, and checker acceptance across unit scale, huge scale, and mixed scale.
- `long long` cross products trusted because final coordinates or final answers fit in `long long`, even when intermediate products need wider arithmetic.
- Heuristic or local-search solutions that rely on local uncrossing, 2-opt, hull-first insertion, greedy repair, randomized restarts, or pruning to cover every important witness basin.

## Killer Constructions

- Pair exact and near versions of the same layout: collinear vs almost collinear, tangent vs tiny gap, overlap vs one-grid gap, boundary vs epsilon-outside.
- Put many points on the same ray, line, coordinate, hull edge, or event bucket so ordering, add/query/delete order, and tie policy decide the answer.
- Use huge coordinates with deliberately small residuals: translated copies near `10^9`, `10^12`, or the problem maximum where orientation, area, or distance signs cancel to `-1`, `0`, or `+1`.
- Embed the degenerate gadget away from the visual center, then surround it with clean random-looking points so broad random tests still look ordinary.
- Freeze the geometry contract before generating: duplicate points, collinear triples, touching, overlapping, holes, self-touching, zero-length edges, and boundary inclusion must each be marked allowed or forbidden.
- Generate valid and invalid siblings for validator-sensitive contracts: simple polygon vs bow-tie, hole strictly inside vs hole tangent to outer boundary, repeated closing vertex allowed vs duplicated interior vertex.
- Build non-convex polygon families from a convex frame plus one planted notch, then increase difficulty with symmetric notches, thin corridors, spiral chains, comb boundaries, and alternating inward/outward teeth.
- `single-notch`: start with a rectangle or regular convex polygon, replace one edge by two edges forming a narrow inward notch, and put queries just inside the notch mouth, on the notch vertex, and just outside the original convex hull.
- `comb`: alternate many shallow reflex vertices along one side to target code that treats the polygon as x-monotone, convex, or safely ray-cast with casual horizontal-edge handling.
- `spiral`: create a simple polygon whose boundary winds inward through a thin corridor to target local visibility, triangulation, and "sort vertices around centroid" shortcuts.
- `same-area-twin`: build a convex polygon and a non-convex polygon with the same bounding box and almost the same area/perimeter to target heuristics that infer containment or diameter from coarse features.
- For holes, vary orientation and nesting: outer CCW with inner CW, reversed rings, one ring hole, many tiny holes, nested-looking invalid holes, two holes touching at a vertex, a hole edge sharing a segment with the outer boundary, and one query point on each boundary type.
- If holes are forbidden, include reject-only validator cases: hole ring supplied as extra contour, bow-tie self-intersection, vertex touching nonadjacent edge, repeated nonconsecutive vertex, and overlapping edges.
- If self-touching is allowed, distinguish `touch at vertex`, `touch along segment`, and `zero-area lobe`; pair each with a query or witness that depends on the exact rule.
- Always test both orientations for each ring when the contract says orientation is irrelevant; test wrong orientation rejection when it is part of the format.
- For point-in-polygon, include query packs at every semantic class: strict interior, strict exterior, edge midpoint, reflex vertex, convex vertex, hole interior, hole boundary, and epsilon-near each class.
- `line-plus-one`: put many points on `y = ax + b` plus one point one grid unit off the line; vary whether the off-line point is near an endpoint, middle, or far outside the segment range.
- `duplicate-cluster`: repeat one vertex, one interior point, and one hull extreme to target set-based deduplication, zero-length vectors, and multiplicity loss.
- `same-ray-pack`: choose pivot `O`, put many points on the same ray and opposite ray, include `O` itself if legal, then add one point on the `atan2` branch cut.
- `same-event-bucket`: make many segment endpoints, polygon vertices, or sweep events share the same `x`, `y`, or angle; include one case where add/query/delete order changes the answer.
- For overflow, use triples `A=(M,M)`, `B=(M+u,M+v)`, `C=(M+r,M+s)` with `u*s-v*r` equal to `-1`, `0`, or `+1`, while each product is near the largest intermediate size contestants may use.
- Include translated copies of the same orientation gadget; exact geometry is translation-invariant, but overflow and floating cancellation are not.
- Compare squared distances near the limit: two candidate pairs whose exact squared distances differ by `1` after products near the maximum coordinate square.
- For area, build polygons whose positive and negative shoelace terms are huge and cancel to a tiny nonzero area.
- For rotating calipers, feed both the raw point set and the computed hull order into adversarial solutions: duplicate extremes, flat hull edges with many collinear points, all-collinear points, and a nearly convex polygon with one reflex vertex.
- `flat-hull-edge`: many boundary points on one hull edge; vary whether the intended answer needs extremes only or also middle boundary points.
- `all-collinear-hull`: the hull degenerates to a segment; targets diameter, width, area, and calipers code that assumes at least three strict vertices.
- `unsorted-convex-cycle`: supply convex points in input order, sorted-by-x order, reversed order, and correct cyclic order to target code that skips hull construction.
- `almost-convex-reflex`: move one vertex just inside a convex polygon to target calipers or convex polygon containment routines called on non-convex input.
- `antipodal-tie`: use symmetric rectangles, regular polygons, or flat chains with many equally good antipodal pairs to target first-pair tie policy and infinite-loop calipers updates.
- For floating traps, create threshold pairs rather than singletons: exact orientation, distance, or angle at `T-d`, `T`, and `T+d`; repeat after scaling and translation.
- Pair collinear vs cross `1`, tangent vs gap `1`, and overlapping vs gap `1`, then scale and translate the whole layout.
- Use mixed magnitude: one cluster near the origin, one near the coordinate maximum, with a query depending on the small residual between them.
- Build angle ties with slopes differing by one denominator unit to target `atan2`, normalized vector, and `y/x` sorting.
- Include boundary answers at `0`, near `0`, and huge values for any checker using absolute, relative, or hybrid tolerance.
- For hard-to-hack brute/heuristic cases, keep `n` small enough for an exact oracle on the core gadget, then surround it with neutral random-looking points so brute cutoff, pruning, hull-first, and local repair shortcuts all see a plausible shell.
- `oracle-core-plus-padding`: make a tiny exact counterexample core, then add neutral points that do not change the answer but push `n` above brute-force cutoffs.
- `two-basin-witness`: create two locally valid polygonizations, matchings, tours, or visibility structures; the obvious hull-first/local-repair basin is legal but suboptimal.
- `repair-cycle`: arrange crossings or visibility defects so one local uncrossing creates the next defect unless a nonlocal move is made.
- `restart-clones`: use symmetric clusters so many random pivots, shuffles, or sampled anchors collapse to the same wrong topology.
- `hidden-degenerate-gadget`: place the real breaker in a small off-center cluster and surround it with clean random points so broad random tests still look ordinary.
- Shuffle, reflect, rotate by 90 degrees when integral, reverse polygon orientation, and relabel points after construction to expose input-order and orientation bias without changing the intended answer.
