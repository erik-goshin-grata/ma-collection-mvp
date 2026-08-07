# Handoff: Bug #6 — funding_hc_extract multi-transaction INSERT (ALREADY PATCHED locally)

**Repo:** `ma-collection-mvp` · **File:** `stages/funding_hc_extract.py` (~L282, multi-txn INSERT branch)
**Status:** fixed in the local working tree (uncommitted) and validated by a full resume run;
included here so it can be reviewed and committed (not re-authored).

## What was wrong
The `i > 0` INSERT branch (fires when one source yields ≥2 funding transactions) reused
`round_params` — a 27-element tuple shaped for the `i == 0` UPDATE SET clause — so it supplied
`6 (prefix) + 27 + 3 (suffix) = 36` params to a 34-column / 34-`?` INSERT:
```
sqlite3.ProgrammingError: Incorrect number of bindings supplied.
The current statement uses 34, and there are 36 supplied.
```
This aborted the entire run at Stage 4b on the first multi-transaction funding source. (The
9-row smoke test never hit it; the full 181-source batch did.) `round_params` carries a
duplicate `v2_event_type` (already in the prefix), and the UPDATE SET also lists
`valuation_currency = ?` twice — both symptoms of the same shape mismatch.

## The fix (applied)
Replace the `... + round_params + ...` reuse with an explicit 34-value tuple in the exact
column order of the INSERT. Diff:
```diff
                     """,
+                    # Explicit param tuple matching the 34-column list above.
+                    # (Do NOT reuse round_params here — that tuple is shaped for the
+                    # i==0 UPDATE SET clause and carries 2 extra fields, causing a
+                    # 36-vs-34 binding crash on multi-transaction funding sources. bug #6)
                     (
                         row["source_raw_id"], "HC_EXTRACTED",
                         v2_event_type, v2_event_type,
                         event_history_type, event_history_type,
-                    ) + round_params + (row["dt_prompt_version"], now, now),
+                        co.get("name"), co.get("domain"), co.get("ticker"), co.get("description"),
+                        rd.get("label"), rd.get("size"), rd.get("valuation_currency"),
+                        rd.get("pre_money_valuation"), rd.get("post_money_valuation"),
+                        rd.get("facility_size"), rd.get("total_raised_to_date"),
+                        1 if rd.get("is_extension_round") else 0,
+                        1 if rd.get("is_down_round") else 0,
+                        1 if rd.get("is_bridge_round") else 0,
+                        dt.get("announced_date"), dt.get("announced_date_precision"),
+                        dt.get("closed_date"), dt.get("closed_date_precision"),
+                        txn.get("financials_disclosure_status"), txn.get("consideration_type"),
+                        txn.get("model_confidence"), _VERSION,
+                        json.dumps(nd) if nd else None,
+                        row["dt_prompt_version"], i, multi_total,
+                        now, now,
+                    ),
                 )
```
Result: full resume run completed 0 crashes; the ~61 funding rows extracted (e.g. Base Power
Series D with 15 investors captured).

## Leftover smell (optional cleanup)
The `i == 0` UPDATE branch still lists `valuation_currency = ?` **twice**. Harmless (SQLite
accepts it; last assignment wins) but worth de-duping while in the file. Not required for the fix.

## Verify
`DB_PATH=data/pl_funding.db python run.py --mode=aggregate` no longer errors; funding
extractions persist. (Note: they still won't reach `transaction_record` until bug #5 is fixed.)
