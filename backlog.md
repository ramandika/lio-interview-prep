# Backlog

Items below come from reviewing how new PDF layouts are discovered and fixed.
The benchmark path (`bench.py` → `failure_table.py` → `bench-failures.md`) works:
failures were clustered into fix families. The open gaps are all on the
**user-upload side**, where no ground truth exists.

## 0. Fix families 2026-10-20 — real interview corpus (`samples/challenge1-offers/`)

Four real supplier quotes (docs Challenge 1) run through the extractor; every
failure found on them is fixed and guarded by per-field ground-truth checks in
`backend/scripts/test_all.py` (`CH1_EXPECTS`).

- **Word-geometry item reader (tier 3b)** — `pdf_extract._band_items`: the
  header row supplies column x-anchors (char-interpolated, so merged header
  words like `EinzelpreisGesamtpreisSt-Kz` still yield one anchor per role,
  with an overlap-walk against mid-word double-claims); body words join the
  column their glyph center falls in; wrapped lines fold; sku fragments
  re-join. Arbitrated document-wide against the stream reader by
  `_items_score` + subtotal-coherence bonus — the score that finally beat the
  bad stream read on GgG (1438 + 320 = 1758 = Nettosumme).
- **Carry-forward rows (`Übertrag`)** — no longer kill the rest of the page's
  table: a numbered row (pos token `1.1a`, `1.2`) reopens it and text right
  of the carry word becomes the next row's description head; carry lines
  close only pos-less rows.
- **Currency-token bug** — `_CUR_TOK_RE.fullmatch(t, re.I)` on a *compiled*
  pattern passes `re.I` as the `pos` argument, so lone `€` tokens survived
  everywhere (leaked into descriptions via the carry path). Flags now at
  compile time; a full-tree grep found no other instance.
- **Totals vs footer** — `_BAND_TOTALS_RE` breaks the band on the vendor
  footer block (`USt.-IdNr.: / Steuer-Nr.: / IBAN: / BIC: / Amtsgericht`),
  whose stray bank column fragments (`62`) otherwise read as a priced row
  (line sum 540 million).
- **Alternativ blocks** — a line opening `Alternativ zu …` is the head of the
  next numbered row, never a continuation of the previous; both real alt rows
  carry the head, and `_validate` excludes them from the sum check with a
  visible warning.
- **Header fields** — vendor name trims trailing address segments
  (`Gärtner Gregg, Berger 7, 59394 Nordkirchen` → `Gärtner Gregg`); quote
  number tries every `\bangebot\b` occurrence for a digit-bearing token
  (prose "unser Angebot für das angefragte Moosbild." precedes the real
  `Angebot A0492/23` line in text order). Apple keeps the `Erstellt von` →
  person-name override (`_vendor_from_author`).
- Known honest gap: AN-OF's vendor stays null (no VAT id, no company-suffix
  letterhead anywhere in the document) — null + low confidence, never
  guessed.

## 1. Upload triage — an analog of `failure_table.py` for uploaded PDFs

Every upload already lands as a reviewable row (`backend/app/main.py:107-152`):
`extract_status` in `extracted/partial/failed` + `extract_error` reasons, PDF
kept on disk (`source_file`), streamable at `/prs/{rid}/pdf`. But triage over
uploads is manual today. Build a small script/endpoint that aggregates over the
`requests` table:

- group by `extract_status` + reason parsed from `extract_error` → failure
  clusters (analog of the bench category × field counts)
- group by correction field from `corrections` → which fields humans fix most
  (= fields the extractor gets wrong, the upload-side eval signal written at
  `main.py:275-288`)
- list candidates for layout review: `extract_status='partial'` and low
  `min_conf` first, PDF viewable at `/prs/{rid}/pdf`

Output mirrors `bench-failures.md`: one fix should heal a cluster, and healed
rows are verifiable after a fix by re-running `pdf_extract.extract` on the
stored `source_file`.

## 2. Widen what `corrections` covers

`corrections` diffs only `title`, `needed_by`, `vendor_name` (`main.py:277-286`).
Two consequences: line items and subtotal/total — the hardest fields (see
`line F1` in `bench-failures.md`) — produce no eval labels, and item-level
mistakes surface only via arithmetic warnings or a careful reviewer. Extend the
diff to the corrected item rows and money fields so the triage in item 1 gets
real coverage.

## 3. Regression fixtures from uploads

Uploads that expose a new layout should be promoted into `samples/` as
fixtures so `scripts/test_all.py` guards them. Without this, a heuristic
extended for one vendor can silently regress a case nobody re-runs (benchmark
corpus covers its own families only). *(First instance done 2026-10-20 with
`samples/challenge1-offers/` — promoted from the interview corpus rather than
an upload; the upload-path workflow is the same: keep `source_file`, re-run
`extract`, diff `extract_json`.)*

## 4. Failure-rate alerting / dead-letter review

`main.py:111-113` documents the intent ("production: dead-letter queue +
alerting on the rate") but nothing tracks it. Add a threshold on the
`partial`/`failed` share of recent uploads with an alert; rows already persist,
so the queue is the table itself — only the tripwire is missing.

## 5. OCR / vision-LLM tiers (pre-existing, from README "known limits")

- Scanned (image-only) PDFs need an OCR tier (Tesseract) as tier 0; scanned
  samples in `samples/invoice-benchmark/` currently score 0 and are excluded
  from bench scope.
- Vision-LLM tier for phone-photo/scan/handwritten inputs — where heuristics
  structurally have nothing to anchor on.
- **Research check for when we build this (deep-research 2026-10-20):**
  (a) document-*specialized* VLMs (PaddleOCR-VL / DeepSeek-OCR class) lead the
  current leaderboards for document parsing — pick against our own corpora,
  self-reported vendor numbers and synthetic-benchmark claims don't transfer;
  (b) general-purpose API routes show real tail latency and hallucination on
  unseen layouts (Grab: P99 3–4× P50, hallucinated fields → rejected the API
  route entirely) — the same reasons the LLM tier here is opt-in rescue-only;
  (c) where a fine-tune is eventually warranted, full-parameter fine-tunes of
  vision-LLMs deliver large low-resource gains (Grab: +70pp Thai, +40pp
  Vietnamese) — that is the long-tail fix, not the first move;
  (d) LLM-as-judge accuracy claims need grounding in verified corpora — which
  the upload-side `corrections` data (item 2) is designed to become.
- LLM fallback tier — **done 2026-10-19** (`llm_tier.py`, tier 4): rescues
  weak heuristic results (no items / no totals) when `LLM_TIER=on` +
  `LLM_API_KEY`; merge keeps heuristic fields, tags items `source="llm"`,
  re-runs `_validate` so checksums police model output. Proven on the
  QualityHosting upload (`requests` id 20: status partial→extracted, 7 items,
  Σ=34.73=total, vendor "QualityHosting AG" vs the old mis-read "Hong Kong"),
  and its trigger matches the endpoint's `partial` definition — `_weak()` =
  no items, no totals, OR only-inferred total (proven on the 5-page
  stacked-label invoice `multipage-5p-de-6`, requests id 22: total 171.602,27
  + tax read where heuristics could only infer). Note: the tier replaces items
  only when heuristics found NONE; mangled-but-present item lines (sku null,
  split-word descriptions) are heuristic-tier work, not LLM work.
  Remaining: extend the trigger to weak-but-nonempty item sets (the sku
  rescue below covers the sku-only slice), and consider
  caching per doc-hash to keep retry cost flat.
- **sku rescue ladder 2026-10-20.** Two rescue layers for docs whose Code
  column was never mapped, cheapest first: (1) deterministic lift —
  `pdf_extract._lift_codes` lifts a code-shaped leading token back into the
  sku slot when a *majority* of sku-less items lead that way (`NIT112
  Nitrile…`), with a visible warning; (2) extended tier-4 trigger —
  `llm_tier._sku_starved` fires the LLM when items are present but no item
  carries a sku, and `rescue` backfills per row on (qty, line_total). Docs
  that genuinely have no code column get the honest "LLM tier consulted for
  item codes — the document names none" note, no fake rescue flag. Live on
  the local gateway: 72/72 skus backfilled on the sku-starved variant of
  `multipage-5p-de-6`.
- **Code → sku mapping fixed 2026-10-20.** The `multipage-5p-de-6` vendor
  prints a `Code` column (no hyphen: `NIT112`) outside the sku header
  dictionary, and the value-inference regex required hyphenated `XXX-123` —
  so the sku column went unclaimed and its tokens leaked into the
  description. Fixed three ways: `code` joined both sku header dictionaries,
  the value-inference token predicate accepts any no-space letters+digit
  code (`NIT112` and `XXX-123`), and `llm_tier.rescue` gained a sku
  *backfill* branch: when heuristics found items but left skus null, model
  items backfill just the sku slot, matched per row on (qty, line_total) —
  heuristic line text is never overwritten, mismatches are skipped. 72/72
  skus on `multipage-5p-de-6`; full sweep numbers unchanged (no regression).

## 6. Bench-side polish (small)

- Only the first candidate table per page is treated as items; stray tables are
  ignored by design (`pdf_extract.py:364`) — revisit if bench shows multi-table
  pages.
- Fix-family work itself feeds `bench-failures.md` regeneration: after each
  heuristic change, regenerate the table and judge the fix by *which rows it
  heals*, never by aggregate score drift.