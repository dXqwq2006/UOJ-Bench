# Hash And Fingerprints

(Note: `unordered_map` or fixed-double-moduli hash should not be targeted, if they are valid when the moduli are randomized.)

## Topic-Specific Variants

- Birthday amplification: Collision pressure from many comparable objects of the same length/shape.
- Structured shells: long borders, repeated blocks, or isomorphic subtrees around the compared core.
- Actual collision pairs for known base/mod or natural-overflow hash.
- Same fingerprint summary, different ordered/rooted structure.
- Hacking common wrong tree hashes.

## Strong Adversarial Solutions

- One hash is safe because the case has too few comparisons for collisions to matter.
- Fingerprints preserve order/root/shape information that was not encoded.
- Randomized parameters make targeted replay unnecessary.
- A collision pair found in isolation stops working after being embedded in the problem structure.
- A casually implemented tree hash is valid.

## Killer Constructions

- Batch many equality/dedup/LCP comparisons at the same length or shape.
- Search collision pairs using the solver's actual base/mod when known.
- Embed a collision or near-collision inside a periodic/string/tree shell.
- Common wrong tree hash hacks.
- Keep the exact colliding objects and parameters with the generator seed.

- Double hash with fixed public parameters: use meet-in-the-middle collision search for both moduli when parameters are known.
- Randomized base but fixed modulus: use birthday amplification and many independent comparisons so one collision is enough.
- Hash-as-filter with exact fallback only on suspicious cases: make colliding objects look unsuspicious by length/frequency statistics.
- Custom `unordered_map` salt: target the higher-level fingerprint collision instead of bucket collision when the salt is unknown.

- Exact fallback after hash hit: generate many equal-hash or long-common-prefix candidates so fallback comparisons dominate.
- Hash table average-case maps: insert keys that share low bits or bucket residues for common identity hashes; include a non-hash oracle case too.
- Tree/string canonicalization by hash: many equal summaries force expensive tie handling or expose missing tie handling.

- **Polynomial anti-hash**: for known base/mod, split a string into two halves and meet in the middle on `sum c_i base^i mod mod` to find two different halves with equal contribution. Embed them between identical prefix/suffix padding.
- **Natural-overflow pair**: search under unsigned `2^64` arithmetic with small alphabet and fixed length; once a collision is found, repeat it in many queries because overflow parameters cannot be randomized.
- **Tree hash commutativity trap**: build rooted trees with child multisets `[{leaf},{chain2}]` and `[{chain1},{chain1}]` that collide under sum/xor/count summaries. Add ordered-child or root-sensitive queries so the real answer differs.
- **Hash-table bucket pack**: for identity hash maps with power-of-two buckets, use keys `x + t*2^k` for the observed bucket size scale. Mix with normal keys so the data does not look like a single artificial run.
