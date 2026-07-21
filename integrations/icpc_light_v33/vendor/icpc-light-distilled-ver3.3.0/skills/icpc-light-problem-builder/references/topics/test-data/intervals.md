# Intervals

## Topic-Specific Variants

- Touching chains whose answer depends on whether contact merges components.
- Nested shell with one crossing interval that invalidates laminar assumptions.
- Identical compressed endpoints but different integer gaps between them.
- Many interval events at one coordinate where the semantic event order matters.

## Strong Adversarial Solutions

- The solver treats touching, crossing, and nesting as the same overlap regime.
- The solver compresses endpoints and loses adjacency/gap information needed later.
- The solver assumes a laminar family after seeing mostly nested intervals.
- The solver's sweep state is valid only when no decisive events share a coordinate.

## Killer Constructions

- Pair cases that differ only by a touching-vs-overlapping contact.
- Keep compressed coordinates fixed while changing real gaps.
- Add one crossing interval inside a large nested family.
- Put start, end, and query events at the same coordinate only when their ordering changes the result.
