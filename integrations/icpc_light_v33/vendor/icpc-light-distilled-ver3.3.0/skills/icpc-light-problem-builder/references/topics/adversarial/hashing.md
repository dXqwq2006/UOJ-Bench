- [WA] Substring equality by sampled positions, endpoints, coarse blocks, or periodic checkpoints is not an equality proof. Long strings can agree on all sampled anchors while the unsampled interior changes the predicate.

- [WA] Dropping length, direction, or offset from a rolling hash makes different objects share a signature even when the base hash is otherwise sound. Empty padding, reversed segments, and shifted copies hide mismatches that exact substring hashes would keep apart.

- [WA] Hash-derived ordering is wrong when a fingerprint, block signature, or sampled tail is used as a substitute for lexicographic comparison. Equal prefixes must eventually be resolved by exact characters or a real LCP/order structure.

- [WA] Period, border, repetition, and palindrome certificates fail when assembled from local hashes that do not encode phase. A short-period middle can satisfy every checked block while one offset-sensitive boundary invalidates the certificate.

- [WA] Palindrome checks that compare a forward hash with a reverse hash but normalize endpoints inconsistently are fragile. Odd/even centers, inclusive/exclusive bounds, and separator handling produce valid-looking hashes for the wrong substring.

- [WA] Transform-then-hash shortcuts are wrong when the transform is not injective for the predicate. Case folding, deleting separators, sorting inside windows, coalescing runs, or mapping many tokens to one class can erase positions the task still distinguishes.

- [WA] Character-count or moment fingerprints used for ordered string constraints confuse anagrams with matches. Length, endpoints, and a few sums/xors do not preserve adjacency, occurrence order, or per-position compatibility.

- [WA] Tree canonicalization by child-hash multisets is wrong when the problem needs rooted order, edge labels, or globally consistent identities. Extra degrees and subtree sizes are filters, not a proof that subproblems are interchangeable.

- [WA] Automaton or trie states cannot be merged just because outgoing-label hashes, terminal counts, or shallow failure-link summaries match. Future continuations depend on language equivalence, not on a local fingerprint.

- [WA] Aho-Corasick output handling is wrong when only the nearest terminal state or one representative pattern is retained. Nested patterns require output/failure information or an aggregate that accounts for all relevant terminal suffixes.

- [WA] Suffix-array substitutes built from block hashes fail when refinement stops before ranks are unique. Periodic strings can keep many suffixes tied; input index or hash order is not a valid lexicographic tie-break.

- [WA] Distinct-substring logic that keeps only one representative per hash bucket can drop real substrings when the task is deterministic. It is especially easy to lose length or start position when comparing hashes from different lengths.

- [WA] Hashing generated strings from a grammar, rope, or automaton is not enough if the predicate depends on a witness position, first mismatch, or count. Equal summaries after concatenation do not recover omitted structural information.

- [WA/TLE] LCP-style comparisons by summaries are risky in sorting, suffix ranking, or greedy choice. Long common-prefix families either expose an inexact tie-break or force repeated fallback scans after the cheap predicate stops separating candidates.

- [WA/TLE] Probable-equal buckets are risky when one legal low-entropy family overwhelms the summary. Verifying every member is quadratic, while capping, sampling, or keeping only representatives can drop the candidate that matters.

- [TLE] Hash-first exact-verify-later has a worst case when legal input produces many real or probable candidates. Unary and short-period strings make many substrings pass length, boundary, and hash filters before the expensive work starts.

- [TLE] Rabin-Karp-style scanning that verifies every candidate by rescanning the whole pattern can degrade to O(n * m) on inputs with a hit at almost every position. KMP or Z-style exact matching avoids that repeated character work.

- [TLE] Center expansion for palindromes, even with hash prechecks, is quadratic on unary strings. Manacher-style radius reuse is the linear-time baseline when all centers matter.

- [TLE] Border and period search that tries many lengths and then scans/verifies characters can be quadratic on repetitive strings. Prefix-function or Z-function structure gives the same information in linear time for ordinary string matching and border tasks.

- [TLE] Counting or grouping substrings by rebuilding a hash table for every length is O(n^2) just to enumerate windows. That is too slow when the intended solution uses a suffix array, suffix tree, suffix automaton, or only needs a sparse set of lengths.

- [TLE] Recomputing power arrays, inverse powers, compressed alphabets, or per-length buckets inside each query/probe multiplies the nominal O(1) substring-hash check. Many distinct queried lengths or binary-search probes can force near-full rebuilding.

- [TLE] Suffix or path comparisons inside sort, priority queues, greedy selection, or DP transitions can repeat the same long-prefix work across many pairs. Cached prefix hashes do not remove the comparison count.

- [TLE] Dynamic string or path hashing that rebuilds a whole suffix, heavy-light chain, rope block, or Euler interval after each edit fails under alternating far-apart updates. The hard case is the update order, not a hash collision.

- [TLE] Hash-filtered pair enumeration remains quadratic when a low-alphabet or periodic input lets most pairs survive the filter. Capping a bucket turns this into WA; verifying every pair turns it into TLE.

- [TLE] Trie traversal that restarts from the root for every text position is O(text length * max pattern length) on repeated prefixes. Aho-Corasick exists to scan once through automaton transitions.

- [TLE] Aho-Corasick implementations that walk the entire failure chain at every text character can time out when the query asks for existence or counts rather than explicit output. Output links or accumulated counts avoid paying trie depth at every position.

- [TLE] Building a full transition table for every Aho-Corasick state is O(states * alphabet). It is fine for a tiny fixed alphabet, but a sparse large alphabet needs maps, sorted edges, compression, or persistent-transition construction.

- [TLE] Suffix automata with ordered-map transitions are O(n log k), and array transitions buy O(n) time only for small alphabets. Treating either choice as constant for arbitrary tokens misses the worst-case bound.

- [TLE] Hash tables used with full substring or string keys can be dominated by key construction and hashing. Average O(1) lookup does not make O(length) key materialization disappear across O(n^2) windows.

- [TLE] Graph or tree hashing by repeated neighbor-signature refinement can time out on regular or highly symmetric inputs. Each round processes nearly the same edge set while colors do not separate enough states.

- [TLE] Meet or merge phases that join all entries in an equal-summary bucket have a worst case whenever one legal family creates a giant bucket. The failure is the cross product, not the collision mechanism.

- [MLE] Storing every interval in each substring hash bucket is unsafe: one string has O(n^2) windows across all lengths. Keeping witnesses, adjacency lists, or rollback data for all of them can exceed memory even if each hash is small.

- [MLE] Precomputing hash groups for all lengths turns a per-length O(n) table into O(n^2) storage. Compute the needed lengths, stream buckets, or use a suffix structure.

- [MLE] Pairwise LCP/equality caches for all suffixes, paths, or states are O(n^2) memory. Long common-prefix inputs make the cache attractive, but they are exactly the inputs that fill it.

- [MLE] Dense arrays indexed by hash value, packed residue tuple, or a length/endpoint/signature product are unsafe. Coordinate-compress observed summaries; do not allocate the theoretical Cartesian product.

- [MLE] A trie or Aho-Corasick node with an array of alphabet outgoing edges costs O(states * alphabet) memory. This is only acceptable for small fixed alphabets; sparse token alphabets need compact transitions.

- [MLE] Copying inherited Aho-Corasick output lists into every state can become quadratic on nested patterns. Store terminal links, counts, or compact output structure instead of duplicating suffix outputs.

- [MLE] Suffix automaton transition arrays cost O(states * alphabet), with up to about 2n states. Map-like transitions keep memory linear in transitions but change the time bound, so allocating for the wrong alphabet model is a hard resource failure.

- [MLE] Materializing all distinct substrings, generated strings, path labels, or canonical representatives defeats the point of hashing and automata. A length-n string can have O(n^2) distinct substrings even though a suffix automaton stores O(n) states and transitions.

- [MLE] Rerooted tree/path hashing that stores a fingerprint for every root-context pair can be quadratic on paths or symmetric trees. A small number of distinct values on easy tests does not bound the number of contexts.

- [MLE] Exact-fallback caches that keep full witnesses beside summaries can dominate memory on repeated inputs. One representative per proven-equivalent class is cheap; retaining every candidate for later tie-breaking is not.

- [MLE] Multi-level block hashing with per-level buckets can keep all levels live: short-period data fills coarse buckets, high-diversity data fills fine buckets. The total table size can exceed the exact linear structure.

- [TLE/MLE] Hashing a grammar, automaton, or search frontier by generated strings is wrong when compact input describes exponentially many expansions. Summaries delay materialization, but joins, comparisons, or witness storage can still scale with the expanded family.

- [RE] Concatenation-friendly hashes for ropes, grammars, dynamic paths, or generated strings can ask for powers beyond the original input length. Legal compact concatenations may represent strings far longer than the scanned source.

- [RE] Packing length, state id, alphabet index, and signature fields into 32-bit indexes can overflow before any hard comparison runs. Products like states * alphabet and layer * state count need checked sizing.

- [RE] Recursive trie, DFS, suffix-link, or automaton DP code can stack overflow on a single path of length n. Path-like dictionaries and unary strings are ordinary legal inputs.

- [RE] Fallback materialization of a canonical representative is unsafe for compressed objects. Hash summaries stay constant-size while the representative string can be exponential in grammar depth or total repetition count.

- [RE] Direct allocation from maximum length times alphabet times number of layers can throw or segfault even when the intended argument assumes a constant alphabet. The alphabet or token limit in the statement matters.
