# Floating Point

## Topic-Specific Variants

- Cancellation: large terms with a small residual that decides the branch.
- Scale twins: same formula at tiny, huge, and mixed tiny-plus-huge scales.
- Near-tie ordering: exact winner differs by less than ordinary floating noise.
- Exact-predicate cases where approximate comparison changes combinatorial structure.

## Strong Adversarial Solutions

- Numerical ordering is stable enough to choose the same discrete object as exact arithmetic.
- Scaling the input does not change the effective error model.
- Cancellation only affects low-order output digits, not branch decisions.
- A floating predicate can replace an exact predicate without changing topology/order.

## Killer Constructions

- Sum alternating large values so the exact residual is small and sign-relevant.
- Scale the same construction by tiny and huge factors while preserving the exact answer relation.
- Create near-ties where the selected candidate flips under exact arithmetic.
- Feed approximate predicates into a downstream discrete choice.

- Epsilon bands: put the correct branch at distances `0`, `eps/2`, `eps`, and `2eps` from the boundary.
- Recompute-with-long-double patches: use integer coordinates near `1e9` so `long double` arithmetic still needs exact predicate discipline for topology.
- Normalize/scale-first logic: include both tiny and huge coordinates in the same case, not just separate cases.
- Binary/ternary search with fixed iterations: make the optimum flat over a long interval with a tiny decisive endpoint.

- Brute force all candidate intersections with dedup by epsilon: create many nearly identical intersections that cannot be safely merged.
- Iterative refinement until stable: alternate cases that converge quickly and cases that oscillate around the tolerance.
- Pairwise distance/angle scans with pruning: make every candidate near-tied so pruning by current best never fires.

- **Cancellation sign flip**: compare `(10^18 + a) - 10^18` against `a+1` for small integer `a` in the problem's formula. The exact sign is small; floating branches can flip.
- **Orientation residual one**: points `A=(0,0)`, `B=(10^9,10^9-1)`, `C=(10^9-1,10^9-2)` give a tiny cross product relative to term size. Sweep nearby `C.y` by `-1,0,+1`.
- **Epsilon boundary pack**: for a threshold `R`, use distances exactly `R`, `R +/- eps/2`, and `R +/- 2eps`; attach a combinatorial decision so classifying one point differently changes the answer.
- **Flat optimum**: define candidate positions whose objective values differ by `1e-12` after scaling; add one discrete tie-break requirement so "any minimum" is not valid.
