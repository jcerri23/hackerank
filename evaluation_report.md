# Evaluation report

All numbers below come from `python3 code/main.py --sample` followed by
`python3 code/evaluation/main.py` (public samples) and `python3 code/evaluation/custom_cases.py`
(custom cases). The run is deterministic: repeated runs give identical files.

## 1. Metrics on the 32 public samples

| Metric | Value |
|---|---|
| Case-level `deletion_status` accuracy | **1.000** (32/32) |
| Step precision (all 6 fields equal) | **1.000** |
| Step recall | **0.984** |
| Step F1 | **0.992** |
| Sequence accuracy (whole `steps_json` identical, order included) | **0.906** (29/32) |

Field-level accuracy, over gold steps aligned to predicted steps on (phase, system_id, resource_id):

| Field | Accuracy |
|---|---|
| phase | 0.984 |
| system_id | 0.984 |
| resource_id | 0.984 |
| operation | 0.984 |
| state | 0.984 |
| evidence_id | 0.984 |

### Remaining sample failures (3 steps, 3 cases)

| Case | Missing step | Why |
|---|---|---|
| case_04 | `VERIFY_RECORD_ABSENT account_004` | `account_004` appears in no participant-visible file or activity; it cannot be discovered. |
| case_18 | `VERIFY_RECORD_PRESENT audit_record_6645` | Same: the id exists only in the expected answer. |
| case_20 | `VERIFY_RECORD_PRESENT audit_record_5569` | Same. |

We deliberately do not guess these ids (e.g. from naming patterns): that would be a case-family shortcut.

## 2. Full dataset run (518 requests)

| | Count |
|---|---|
| INCOMPLETE | 356 |
| COMPLETE | 152 |
| NOT_APPLICABLE | 10 |
| Steps written | 3,257 (6.3 per case) |
| Contract validation errors | 0 |

`reported_status` is a poor predictor of the real state, as expected: 161 cases reported
COMPLETE by the workflow are INCOMPLETE according to the evidence.

## 3. Custom evaluation cases

`python3 code/evaluation/custom_cases.py` — 6/6 pass.

| # | Case | What it guards against | Result |
|---|---|---|---|
| C1 | case_28: a search document for `smartinez6461.jr@gmail.com` exists next to the subject `smartinez6461@gmail.com` | Look-alike decoys being merged by fuzzy matching | PASS: not linked |
| C2 | case_37: request sent from an e-mail the user later replaced | Missing historical identifiers | PASS: resolves to `user_35` with all 3 historical e-mails |
| C3 | case_26 re-audited one hour after `reported_at` | Using evidence captured after the cut-off (time travel) | PASS: `evidence_92` (captured later) is never cited |
| C4 | Multilingual probes (FR, JA, EN) incl. "marketing lists only, keep my account" | Wrong intent on non-deletion requests | PASS |
| C5 | Tombstone with direct identifiers | Deleting a record that must be kept as a reference | PASS: `REDACT_RECORD` → `VERIFY_RECORD_REDACTED` |
| C6 | Security audit record inside its retention period | Deleting a record policy requires to retain | PASS: `VERIFY_RECORD_PRESENT` |

## 4. Failures found and improvements made

Each row is one iteration measured on the samples (step F1 / sequence accuracy).

| # | Change | F1 | Seq. acc. |
|---|---|---|---|
| 1 | First end-to-end version: identifier links + latest observation wins | 0.760 | 0.375 |
| 2 | **Failure:** a bundle exported after `audit_at` was cited because its inner snapshots were older. **Fix:** an observation is usable only if both its own timestamp and its evidence item's capture time are ≤ `audit_at`. **Failure:** anonymised analytics and 404 profiles were attributed to the wrong subject. **Fix:** deletion-job reconstruction from the worker's claims, with the job cadence learned from identifier-linked jobs and global elimination across requests | 0.934 | 0.531 |
| 3 | **Failure:** neighbouring jobs (1–2 s apart) stole each other's actions. **Fix:** build action chains first (exact learned step gaps), then attach chains to profile submissions | 0.950 | 0.688 |
| 4 | **Failure:** profiles proven absent only by a complete table capture (`evidence_54`, `evidence_51`) had no citation. **Fix:** scoped absence proofs. A first version applied them to every system and to curated inventories, which hid retained security records (F1 dropped to 0.907); restricted to profiles and to captures that are complete, and only when no direct read exists | 0.976 | 0.719 |
| 5 | **Failure:** an attachment last seen months before the request (`object_19`, case_07) was reported again. **Fix:** state is reconstructed only from reads made after `requested_at`; older reads still serve to link identifiers. **Fix:** legacy contact-index rows are secondary identifiers (deleted), not conversations (redacted); older already-closed profiles of the same person are dropped unless they still hold data | 0.992 | 0.906 |
| 6 | Full-dataset review: screenshots with a single record now attach the page's contact card to it; connector consoles report the running state; file names in object listings resolve through catalog aliases; category is taken from every read of a record; a deterministic intake-order tie-break is used when identifiers, cadence and elimination cannot separate two jobs (only 7 of 518 cases end with an unproven profile verification) | 0.992 | 0.906 |

## 5. Known limitations

- Ids that never appear in participant-facing data cannot be produced (see §1).
- When two deletion jobs in the same window carry no identifier at all, the intake-order
  tie-break may swap them; this affects the profile and anonymised-analytics steps of those cases.
- OCR quality depends on the local Tesseract build; OCR text is cached in `.cache/ocr/` so a run
  is reproducible once the cache exists.
