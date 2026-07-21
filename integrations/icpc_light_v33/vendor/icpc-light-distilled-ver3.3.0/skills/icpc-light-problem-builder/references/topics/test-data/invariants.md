# Invariants

## Topic-Specific Variants

- Same claimed invariant vector, different reachability or optimum.
- Every local invariant condition holds, but a global obstruction remains.
- Monotone measure decreases for every move yet gets trapped before the target.
- Relabeled or symmetric states share the invariant but have different legal futures.

## Strong Adversarial Solutions

- A necessary invariant is treated as sufficient.
- The invariant proves feasibility but not optimization or counting.
- A second constraint such as connectivity, order, capacity, or history is invisible to the invariant.
- Small-state reachability evidence is extrapolated without checking invariant collisions.

## Killer Constructions

- Brute force small states to find invariant-collision pairs, then inflate them.
- Combine locally valid pieces behind one global bridge/blocker/capacity obstruction.
- Build two instances with identical invariant vectors and different optimal values.
- Relabel a symmetric state, then change only the legal move future.
