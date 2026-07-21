# Strings

## Topic-Specific Variants

- Border ladders: many valid borders whose lengths differ by one repeated period, plus jumpy chains where the longest border is misleading.
- One-break periodic strings: a long exact period with one defect that changes matching state, minimal period, palindrome centers, or suffix order.
- Shifted repeated blocks: the same block appears at neighboring offsets so LCP/equality is decided only after a long shared prefix.
- Dense automata: many active fail links, output links, suffix states, suffix-array intervals, or palindromic suffix links are live at once.
- Anti-hash shells: repeated or near-repeated windows that amplify one bad equality decision across many queries or state merges.
- Non-random structured strings: de Bruijn, Thue-Morse, and mixed periodic/random blocks to defeat both random-only and simple-period-only tests.
- Multi-query clusters over the same repeated core, so one bad equality, range scan, or stale dynamic summary is reused many times.

## Strong Adversarial Solutions

These are the wrong assumptions and resilient WA/TLE strategies worth modeling; weak data often misses them.

- `hash_plus_guard`: double hash, overflow hash, or hash ordering is treated as a certificate, with exact checks only for short windows, dangerous all-same cases, or final candidates. Kill with long equal-length windows over a repeated core, especially when one false equality contaminates grouping, sorting, DSU, DP, or suffix ranks.
- `few_borders`: computes `pi` or `Z` but inspects only `n - pi[n-1]`, the longest border, or a bounded number of ancestors. Kill with deep border ladders, non-dividing candidate periods, jumpy border chains, and one-break periodic strings.
- `local_lcp`: binary-searches LCP with hashes, then samples a short tail, skips one mismatch with loose boundaries, or assumes equality ties are rare. Kill with first/last mismatch, late mismatch after a long prefix, and replacement-vs-insertion/deletion ambiguity.
- `bucket_suffix`: groups suffixes or substrings by a head block, keeps a few representatives, and exact-compares only finalists. Kill with repeated blocks whose decisive mismatch lies after the sampled head.
- `automaton_sparse`: builds AC/SAM/PAM/suffix structures but brute-forces fail chains, output chains, state length ranges, right sets, suffix-array intervals, or palindromic suffix walks under a sparsity assumption. Kill with dense intervals and repeated query amplification.
- `dynamic_locality`: rebuilds edited blocks and nearby centers/periods/hashes only. Kill with a point update inside a periodic or palindromic core that changes a global border, minimal period, suffix order, longest palindrome, or suffix bucket.
- `overlap_independence`: treats overlapping occurrences, active states, or equal windows as independent. Kill with shifted repeated blocks and many queries whose ranges differ by one period or one character.
- `state_undercontext`: compresses automaton/DP summaries without enough suffix context, parity, boundary, or root/sentinel information. Kill with parity-sensitive palindromes, prefix/suffix asymmetry, and strings touching both ends.
- `hard_to_hack_bruteforce`: exact on random data, caches identical repeated queries, hash-prunes comparisons, or threshold-scans intervals. Kill with families where many long candidates survive cheap filters but differ enough to avoid simple memoization; do not rely only on one huge `a^n`.
- `guarded_special_cases`: exact islands for small `n`, short substrings, all-same data, or final answers hide the bug outside the guarded envelope. Include medium cases, shifted witnesses, and boundary-crossing queries.

## Killer Constructions

- **Period and border base cases:** start from `a^n`, `(ab)^k`, `(aba)^k`, or `p^k` for several `|p|` values. Query lengths that are both multiples and non-multiples of `|p|`, and vary alphabet size across `1`, `2`, `3..5`, and large alphabet when allowed.
- **Non-dividing period:** use `s = p^k + prefix(p, t)` with `0 < t < |p|`. Then `n - pi[n-1]` looks like a period candidate, but whole-repeat and divisibility logic can disagree.
- **Jumpy border chain:** start with a self-overlapping seed such as `ababa`, then iterate `x = x + c_i + x` with rare separator changes. Several ancestors look plausible, so longest-border-only logic is insufficient.
- **One-defect period:** build `s = p^k`, then flip one character at `0`, `n-1`, `|p|-1`, `|p|`, `n/2`, and just outside a queried range. Also include two-defect controls where defects cancel for one relation but not another.
- **Z/LCP late break:** use `s = p^k + x + p^k`, or compare `P + x + R` against `P + y + R`, where the mismatch appears only after a long shared prefix. This kills sampled verification, linear LCP scans, and late lexicographic decisions.
- **Long-LCP query storm:** on `s = p^k + defect + p^k`, ask many equality/LCP queries between offsets `i` and `i + |p|`, plus offsets that cross the defect. Shift starts, lengths, and query order so cache keys do not repeat exactly.
- **Failure staircase:** pattern `a^m b`, text `(a^m c)^r`; KMP/AC-like code repeatedly falls from depth `m` on the wrong next character if missing transitions are not memoized.
- **AC output and fail storm:** patterns `a`, `aa`, ..., `a^m`, plus `a^i b`; text alternates `a^m b` and `a^m c`. Every position has many terminal ancestors, and the `c` branch forces missing-transition fail walks.
- **AC frontier blowup:** include all binary strings of length `d` when constraints permit, or many strings with a shared prefix and distinct next character. This kills assumptions that the trie is path-like.
- **SA interval scan storm:** in `a^n`, pattern `a^t` matches `n-t+1` suffixes; in `(ab)^k`, prefixes of `(ab)^t` match every other suffix. Repeat many `t` values so interval enumeration becomes `Theta(nq)` and one interval cannot be memoized.
- **SA tie-order blowup:** repeated blocks with one corrupt block, such as `B B B C B B` where `C` differs near the end, create suffixes with long common prefixes and late order decisions.
- **SAM range and occurrence storm:** all-same and short-periodic strings create states with long represented length intervals `[len(link[v])+1, len[v]]`; repeated blocks create many end positions. Query per length, per occurrence, right set, or suffix-link subtree repeatedly.
- **Suffix tree depth:** `a^n`, `(ab)^k`, and `a^m b a^m` create chain-like or deep suffix-link shapes. Add recursion-depth and stack-safety cases when implementations are likely recursive.
- **Palindrome parity set:** include `u + c + reverse(u)` and `u + reverse(u)`, plus palindromes touching the first and last character with lengths `1`, `2`, and the whole string. This catches one-parity and PAM root/sentinel bugs.
- **Palindrome density and defects:** `a^n`, `(ab)^k`, `aaaaabaaaaa`, and `abacabadabacaba` create many centers, suffix links, or duplicate transition labels. Flip one character in `u + c + reverse(u)` or `u + reverse(u)` at center, edge, and one-off-center positions.
- **Palindrome query cuts:** ask for longest palindrome in prefixes, suffixes, whole string, and ranges that just exclude the defect. Range PAM/Manacher shortcuts often fail at these boundaries.
- **De Bruijn counterweight:** use a linearized de Bruijn sequence `B(sigma, k)` plus its first `k-1` characters to cover every length-`k` word over a small alphabet. This stresses full trie/SAM/SA transition coverage without obvious periodicity.
- **Thue-Morse structure:** use `t[i] = parity(popcount(i))`, or `periodic_prefix + thue_morse_block + periodic_suffix`, to defeat guards that only special-case all-same, alternating, or short periods.
- **Anti-hash collision shell:** only when hash parameters are fixed, predictable, logged, or replayable, search for `x != y` with equal hash, then embed `P + x + Q` and `P + y + Q` inside repeated padding. For `uint64_t` polynomial hashes with odd base, Thue-Morse/Prouhet-style sign blocks are useful candidates, but verify against the actual implementation.
- **Hash summary/order attacks:** if the solution hashes sorted blocks, multisets, unordered summaries, head signatures, or fingerprint tuples, use the same block multiset in different orders and many suffixes that share a long head but differ after the sampled tail.
- **Query/update amplification:** cluster equal-length substring/LCP queries at neighboring starts; make half cross a defect and half stop just before it. For dynamic tasks, start with a clean period or palindrome, move one internal defect through `T-1` local updates if rebuild period is `T`, query after each update, then restore and query again.
- **Mixed instance:** combine random prefix, periodic core, one-break core, palindromic core, de Bruijn or Thue-Morse block, and random suffix. Put several small adversarial strings in one input file to catch per-test initialization and stale global caches.
