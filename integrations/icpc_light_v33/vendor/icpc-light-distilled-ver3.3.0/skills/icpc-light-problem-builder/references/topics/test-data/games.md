# Games

## Topic-Specific Variants

- Mirrored positions with different legal futures.
- False early period with a preperiod just beyond sample-sized exploration.
- Disjoint sums where many zero-value components hide one small nonzero component.
- Same board/statistics reached with different move rights, turns, or repetition history.

## Strong Adversarial Solutions

- Symmetric states are equivalent even when move constraints break symmetry.
- An observed early period is the true period.
- Component values can be combined with the wrong operator or without rule-specific side conditions.
- The stored state summary omits history that changes legal moves or outcome class.

## Killer Constructions

- Brute force small states to find same-summary different-outcome pairs.
- Build ladders across the claimed period and just beyond it.
- Combine many neutral components with one component that flips the xor/outcome.
- Mirror a state, then add one asymmetric legal move.

- Period detection with verification window: put the first period break after several repeated windows.
- Symmetry canonicalization: create mirrored states where move rights, pass rights, or repetition history differs.
- Sprague-Grundy shortcuts: include components whose combination rule is not xor under the actual rules.
- Monte Carlo rollout policies: make many moves equal for the rollout horizon and separate only after a forced reply.

- Minimax with alpha-beta: order moves so the best move is always scanned last and all earlier moves tie until deep.
- Retrograde search with memo compression: create many same-board states with different player/history metadata.
- Period/Grundy brute force: use a preperiod just larger than the solver's table and a false small period before it.

- **False period ladder**: choose a simple recurrence/game where outcomes repeat for `K` states, then add a move available only at `K+1` that flips the class. Include cases at `K-1`, `K`, `K+1`, and `2K+1`.
- **Neutral-sum camouflage**: combine `m` zero-Grundy components with one component of Grundy `1`; duplicate a visually similar but actually zero component so xor-only and sum-only mistakes separate.
- **History fork**: two traces reach the same board, but one used a pass/repetition/ko-like right. Query both traces, not just the final board.
- **Move-order minimax killer**: build a balanced game tree where all left moves return the same heuristic value until the last ply; place the winning move at the end of the input order.
