# Entity Resolution Spec

**Version:** 0.1 (draft)
**Repo path:** `specs/entity_resolution.md`

---

## 1. Purpose

Identify when two or more extracted transactions describe the same real-world deal, and cluster them so downstream aggregation can reconcile their fields into a single canonical transaction record.

The MVP uses name + date matching. No domain-based matching, no ticker-based matching, no external entity resolution services. This is intentionally narrow — the MVP proves the pipeline works; richer entity resolution is a v2 effort.

---

## 2. When Clustering Happens

Stage 8 of the pipeline, after `LC_EXTRACTED` and before `AGGREGATED`. Clustering runs in batch across all staging_extraction rows in `LC_EXTRACTED` state.

Input: set of staging_extraction rows, each with extracted target_name, acquirer_name, announced_date.

Output: each staging_extraction row is assigned a `transaction_cluster_id`. Rows sharing a cluster_id describe the same deal.

---

## 3. Name Normalization

Before any comparison, both target and acquirer names are normalized identically.

### 3.1 Lowercase
All names folded to lowercase.

### 3.2 Strip legal suffixes
Remove (case-insensitive, trailing whitespace-aware):

```
inc., inc, incorporated
corp., corp, corporation
co., co, company
ltd., ltd, limited
llc, l.l.c., llc.
lp, l.p., lp.
plc, p.l.c.
ag, s.a., s.p.a., s.a.s., s.r.l., gmbh, bv, nv
holdings, holding
group
```

Strip both the suffix and any trailing punctuation / whitespace. Do not strip mid-name occurrences — only terminal tokens.

### 3.3 Strip parentheticals
Remove content in parentheses and the parentheses themselves:

- `Acme Industries (NASDAQ: ACME)` → `acme industries`
- `Beta Holdings (UK)` → `beta holdings`

### 3.4 Collapse whitespace
Multiple spaces / tabs → single space. Trim leading/trailing whitespace.

### 3.5 Strip specific noise tokens
Remove (case-insensitive, standalone tokens only):

```
the (when leading)
and, &, + (when between tokens, replaced with space)
```

### 3.6 Preserve
- Hyphens inside names (e.g., `smith-miller`).
- Ampersands when part of a registered company name (e.g., `johnson & johnson` — this is a judgment call; current default is to replace with space, giving `johnson johnson`; comparison is tolerant enough that this works).
- Numbers inside names (e.g., `7-eleven`).

### 3.7 Example normalizations

| Input | Normalized |
| :--- | :--- |
| `Acme Corporation, Inc.` | `acme` |
| `Acme Corp` | `acme` |
| `Acme Holdings LLC` | `acme` |
| `The Acme Group` | `acme` |
| `Acme (NASDAQ: ACME)` | `acme` |
| `Beta Industries S.p.A.` | `beta industries` |
| `Smith-Miller Holdings Ltd.` | `smith-miller` |

Note: `Acme Corporation, Inc.` normalizing to just `acme` is aggressive and could cause false merges (two companies both named "Acme" in unrelated industries). Mitigation: the announced_date proximity requirement (see §4.2) prevents false merges in the overwhelming majority of cases because two unrelated "Acme"-named deals rarely announce within 3 days of each other.

---

## 4. Matching Logic

### 4.1 Fuzzy name match
Uses `rapidfuzz.fuzz.token_set_ratio` between normalized names.

Threshold: ≥ 90 on **both** target and acquirer.

`token_set_ratio` handles word reordering, partial matches, and extra tokens gracefully. A score of 90 tolerates small variations (hyphenation differences, occasional typo, legal-form residue that escaped normalization) without admitting genuinely different names.

### 4.2 Date proximity
Announced dates must be within ± 3 days of each other.

Rationale: the same deal announcement can surface in multiple sources (original PR, wire service repost, SEC 8-K filing next day, coverage piece the day after) within a tight window. Three days comfortably covers this. A deal announced on a Friday and SEC-filed Monday is 3 calendar days apart.

If announced_date is null on either row, date matching is skipped and the fuzzy name match alone does not cluster — the rows stay unclustered and aggregation treats them as separate transactions. (Null announced_date is a data quality issue, not a clustering problem to solve.)

### 4.3 Combined rule
Two staging_extraction rows cluster together when:

```
(fuzzy(target_A, target_B) >= 90)
AND
(fuzzy(acquirer_A, acquirer_B) >= 90)
AND
(abs(announced_date_A - announced_date_B) <= 3 days)
```

All three conditions must hold. Dropping any one produces false merges in practice.

### 4.4 Transitive clustering
Clustering is transitive: if A matches B and B matches C, then A, B, C share a cluster — even if A and C do not directly match (e.g., because A's announced_date is 4 days from C's but both are within 3 days of B's).

Union-find (disjoint-set) structure is the standard implementation. Stable across re-runs given stable inputs.

---

## 5. Cluster ID Assignment

Cluster ID format: `tc_` prefix + first 12 hex chars of SHA-256 over the sorted, normalized names of all target/acquirer entities in the cluster plus the earliest announced_date.

Example:
```
normalized_names = sorted(["acme", "beta"])
earliest_date = "2026-04-15"
seed = "acme|beta|2026-04-15"
cluster_id = "tc_" + sha256(seed).hexdigest()[:12]
# → "tc_a1b2c3d4e5f6"
```

Deterministic: same cluster content produces same cluster_id across runs. This means re-running stage 8 on unchanged data produces unchanged cluster_ids, so downstream tables don't need to be rebuilt unnecessarily.

---

## 6. Edge Cases

### 6.1 Same deal, different party name variants
The canonical case. Example:
- PR: Acme Corp acquires Beta Industries
- SEC 8-K: Acme Corporation, Inc. acquires Beta Industries LLC

Both normalize to `acme` / `beta industries`, names fuzzy-match at ~100, dates within a day. Clusters correctly.

### 6.2 Announcement and close of the same deal
- Announcement PR on 2026-01-20: Acme Corp to acquire Beta Industries
- Close PR on 2026-04-02: Acme Corp completes acquisition of Beta Industries

Announced_date on the close PR should be the original 2026-01-20 (extracted from the release's own reference to the original announcement date), OR null if the close PR doesn't reference it. If the close PR's `announced_date` comes through as `2026-04-02` (the close date), they're 72 days apart and won't cluster — they'll be treated as two separate transactions.

This is a known MVP limitation. Close and announcement linking via the original announcement date is an extraction improvement, not a clustering fix. High-confidence extraction prompt instructs the model to extract the original announcement date from close releases when mentioned.

### 6.3 Multiple acquirers in a consortium
If the same target is acquired by a consortium and one press release lists `Alpha Capital, Bravo Partners, and Charlie Fund` while another lists only the lead `Alpha Capital`, fuzzy name match on acquirer may drop below 90 and they won't cluster.

MVP handling: both rows become separate transactions. Consortium handling is v2.

### 6.4 Hyphenated / accented names
`Saint-Gobain` and `Saint Gobain` — normalized forms differ. Hyphen preservation (§3.6) means they stay distinct. Token-based fuzzy match gives high similarity because tokens `saint` and `gobain` appear in both. token_set_ratio is typically 100 in this case — clusters correctly.

`Müller Industries` and `Muller Industries` — no unicode normalization in MVP. These would produce different normalized forms and may not cluster. Acceptable MVP limitation. Unicode normalization (NFD / stripping diacritics) is a v2 enhancement.

### 6.5 Entity with no name
If an extraction row has a null target_name or acquirer_name (extraction failure or genuine ambiguity in source), the row cannot be clustered. It stays in `LC_EXTRACTED` state with `transaction_cluster_id = null`. Aggregation treats each null-party row as its own singleton cluster, producing a separate transaction record flagged for review.

---

## 7. Limitations and Future Work

| Limitation | MVP Behavior | Future Direction |
| :--- | :--- | :--- |
| No domain-based matching | Clusters rely entirely on names + dates | v2: use target_domain or acquirer_domain as a stronger signal when present |
| No ticker-based matching | Public companies matched by name only | v2: add exchange:ticker as an alternate match key |
| No cross-lingual matching | English-only | v2 (EU expansion): transliteration and multilingual name variants |
| Unicode handling is minimal | Diacritics not normalized | v2: NFD normalization + diacritic strip |
| Close/announcement linking is fragile | Relies on the model extracting the original date from close releases | v2: separate linking step via acquirer × target match on any date after announcement |
| Consortium acquirers | Separate records for each acquirer variant | v2: acquirer set matching with subset tolerance |
| Typos / transliteration in source | fuzzy=90 catches most; severe cases miss | v2: train a domain-specific similarity model |

Each of these is tracked explicitly — clustering gaps are a known input to v2 scoping.

---

## 8. Testing

### 8.1 Unit tests
For each stage of normalization, a small set of fixtures in `tests/entity_resolution_tests.py` covering:
- Legal suffix stripping (several variants)
- Parenthetical stripping
- Lowercase + whitespace collapse
- Composite cases combining all rules

### 8.2 Clustering tests
Fixture pairs that should / should not cluster:
- Identical names, same date — should cluster
- Minor legal form variation, same date — should cluster
- Identical names, 5 days apart — should NOT cluster
- Totally different companies, coincidentally similar token — should NOT cluster
- Hyphenated variant — should cluster
- Transitive chain — all in one cluster

### 8.3 Real-data validation
After the first 100-PR run:
- Manual review of any cluster with > 2 members (rare; warrants inspection).
- Manual review of all singleton clusters from rows with high-confidence extractions (possible missed merges).
- Log of all clusters in `logs/clusters_<run_id>.log` for post-hoc review.

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
