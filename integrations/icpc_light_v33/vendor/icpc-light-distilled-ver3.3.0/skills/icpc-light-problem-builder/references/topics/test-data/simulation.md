# Simulation

## Topic-Specific Variants

- Same visible event multiset, different legal event order, different state.
- Same prefix followed by divergent suffixes that should not share cached future state.
- Phase transition where a summary valid before the switch is invalid after it.
- Undo/redo trace where two branches revisit the same state name with different hidden metadata.

## Strong Adversarial Solutions

- Same-time events can be processed in any order.
- Forked traces with the same prefix can reuse all cached state.
- Phase-local summaries remain valid across a semantic phase change.
- Rollback restores the visible state but not the metadata needed for future events.

## Killer Constructions

- Create paired traces with identical events but different legal tie order when the answer should differ.
- Place the decisive event immediately before and after a semantic phase switch.
- Fork from one prefix into two suffixes that query the reused cache differently.
- Revisit a rollback checkpoint through two branches with the same visible state.
