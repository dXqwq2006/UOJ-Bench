# Amortized Structures

## Topic-Specific Variants

- Fragment storm: one uniform region is repeatedly split into many tiny regions before a full traversal.
- Heal-then-puncture: coalesce/rebuild work is undone by a targeted next operation.
- Rollback ping-pong: repeated returns to one checkpoint with divergent suffixes.
- Hotspot migration: traffic moves just before locality-based amortization pays off.

## Strong Adversarial Solutions

- ODT/interval partitions stay small under non-random schedules.
- The amortized proof still holds when the adversary controls phase order.
- Rollback branches can share enough structure that restoring visible state is sufficient.
- Locality/caching assumptions survive deliberate hotspot movement.

## Killer Constructions

- Start from one plateau, puncture it into alternating tiny ranges, then query the whole region.
- Repeatedly coalesce a region and puncture the same boundary again.
- Create sibling rollback branches with one changed update and a later comparison query.
- Keep values stable while moving only the touched index/range/key.

- Rebuild-on-threshold: oscillate just below the threshold, then force a full traversal before the rebuild triggers.
- Merge-adjacent-equal ODT patches: move single-point spikes so adjacent equal intervals never stay adjacent long enough to merge.
- Rollback checkpoint caching: revisit the same visible checkpoint with one hidden stack/tag difference.
- Locality caches: keep the same hot value but migrate the hot index/range by one block each phase.

- "Probably amortized" interval sets: many operations are individually legal and produce correct answers, but interval count grows linearly.
- Lazy rebuild structures: alternate cheap updates and queries that force materialization of stale tags.
- Sqrt decomposition with partial-block scans: use ranges that include two large partial blocks and almost no full blocks.
- Persistent/rollback brute force: branch from a common prefix into many suffixes so each suffix is small but total replay is large.

- **ODT split explosion**: start with assignment `[1,n]=0`. For `t=1..q`, assign one point `mid + (-1)^t ceil(t/2)` to `t mod 2`; every fifth operation assign a short interval around `mid` to a third value. This grows many tiny intervals and defeats naive merge patches.
- **Heal-then-puncture**: assign `[L,R]=0`, query `[L,R]`, then assign `[L+i,L+i]=1` for increasing `i`; periodically reassign `[L,R]=0` and repeat with shifted `i`. This tests both coalescing and immediate re-fragmentation.
- **Rollback sibling trap**: apply prefix `P`, checkpoint, branch A updates `x`, rolls back, branch B updates `y`, then query a structure depending on both historical tags. Wrong rollback that restores only parent pointers or visible values fails.
- **Threshold oscillation**: if a solution rebuilds when dirty count `>B`, do phases of exactly `B` dirty updates followed by a query that touches all dirty positions, then one neutral update that resets the apparent pattern.
