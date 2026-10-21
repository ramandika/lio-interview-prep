# Purchase Request Tool — PDF quote extraction + PR workflow

Interview case study: build from scratch a tool that extracts structured data
from supplier quote PDFs and drives a purchase-request (PR) approval workflow.
Fully heuristic extraction (no LLM calls), runs in Docker, one container.

## Run

```bash
docker compose up -d          # -> http://localhost:8020
# demo users: alice/alice (requester), bob/bob (buyer)
# regenerate the 3 sample PDFs:
docker compose run --rm app python scripts/generate_samples.py
```

If `localhost:8000` is free you can also map `- "8000:8000"` in docker-compose.yml.

## Sample corpora in `samples/`

| Folder | Docs | Why it's in there |
|---|---|---|
| `samples/invoice-benchmark/` | 181 docs (113 PDF + 53 JPG + 15 PNG) | mirror of DrewKraken/invoice-extraction-benchmark v1 — varied vendors/currencies/layouts, plus phone-photo/scan/handwritten images (JPG/PNG need the OCR/vision tier; the /extract endpoint is PDF-only by design) |
| `samples/generated-invoices/` | 746 PDF + 1517 JSON | kaggle `jakubgal/generated-invoices` — invoice-like PDFs in 7 languages (EN 109, DE 100, CS 128, ZH 108, AR 109, JA 107, SK 85); every `<sample>.pdf` ships a same-named ground-truth JSON. 1–3 pages, up to 51 line items. `.txt` LLM run-logs from the author's benchmark excluded |
| `samples/invoice2data/` | 13 PDF | real vendor invoices (AWS, Azure, coolblue, QualityHosting DE…) behind the 205 vendor templates |
| `samples/camelot/` | 53 PDF | `camelot`'s table-parsing edge cases: rotated, copy-text, background lines, column spans |
| `samples/challenge1-offers/` | 4 PDF | four real supplier quotes from the interview corpus — merged/no-grid headers, `Übertrag` carry rows, alternative positions, stacked totals; per-field ground truth in `test_all.py` (`CH1_EXPECTS`) |
| (root) | 5 PDF | our synthetic quote PDFs — upload these in the app to create PRs |

## Demo script (~5 min)

1. Login as **alice** (requester).
2. Upload `quote_sudhaus.pdf` (German layout, comma decimals,
   no grid lines) — extraction shows all header fields + items
   correctly despite the layout; confidence per field. Note `valid_until`
   (14.11) and `needed_by` (20.01) as *separate* fields.
   Clicking any field chip or item row in the **Review & submit** panel jumps
   the source PDF to that value and highlights the exact text it was
   extracted from (pdf.js text layer, vendored in `backend/static/pdfjs`;
   matching is separator-normalized so `5.763,60` and `5,763.60` both hit;
   the browser's native PDF plugin stays as the fallback if pdf.js can't
   load).
3. Upload `quote_atlas.pdf` → creates a draft PR; extraction warns that
   needed-by fell back to the validity date (no delivery date on the doc);
   correct the vendor name inline, **Save edits**; **Submit** → total
   (GBP 5.763,60 ≥ threshold €1.000) → *pending approval*. The detail card
   now shows what was corrected from the raw extraction (`corrections`).
4. Logout, login as **bob** (buyer): the **Approval queue** card appears;
   Approve → status *approved* + order id `ORD-...`. Bob sees the correction
   note — an informed approval.
5. Auto-approval is server config, not a client input: restart the app with
   `APPROVAL_THRESHOLD=90000` → submitting `quote_sudhaus.pdf` total
   (€18.649,68 < threshold) → *approved* + order immediately, no buyer step.
   (Back to `APPROVAL_THRESHOLD=1000` for the rest of the demo.)
6. Show `quote_meridian.pdf`: 2 pages, item table continues on page 2 with a
   repeated header row — all 6 items merge into one list; multi-line
   Bill to / Ship to blocks captured; `Zahlungsbedingungen` / `Incoterms` read.
7. Show `quote_vantorre.pdf`: the deliberately problematic one — **one red row
   among green**: the custom item (`VT-2287`) is priced **per lot**, so its
   Unit Price cell says "on request" and the pipeline infers it
   (1.960,00 / 2 = 980,00) at confidence **0.55** with a warning, while the two
   list-priced rows stay at 0.90 (green). The `Σ(line totals) vs subtotal`
   check fires (8.240,00 vs 7.992,80 after the 3% trade discount) — which is
   exactly why the bad row *stays* red instead of being bumped to 0.92 by the
   passing sum check; the buyer is told which numbers are derived, not read.

## Extraction pipeline (the algorithmic story)

Four tiers plus a geometry rescue, cheapest-first:

| Tier | Tool | Why |
|------|------|-----|
| 1. Text layer | PyMuPDF | If the embedder wrote text, geometry is free and exact — no OCR ever needed for this class of input |
| 2. Tables | pdfplumber | Tables are the item structure; we schema-match by column *header keywords*, not fixed column positions, so supplier layouts differ freely |
| 3. Heuristic fill | label-anchored regex | Header fields from label/value pairs; EN/DE label dictionaries (`Valid Until` / `Gueltig bis` / `Liefertermin`) make the parser multilingual without new code paths |
| 3b. Word geometry | pdfplumber `extract_words` → band reader | For quotes whose headers are merged/mangled (`EinzelpreisGesamtpreisSt-Kz`) and that have no grid lines: the header row supplies column x-anchors, body words join the column their glyph center falls in, wrapped lines fold. Arbitrated document-wide against tier 3's stream reader by a junk-penalty score plus a subtotal-coherence bonus (Σ lines ≍ subtotal scores +10) |
| 4. LLM rescue | text LLM via stdlib `/v1/messages` | Fires only when tiers 1-3 come back weak (no items / no totals) — e.g. invoice vocabulary outside the keyword dictionaries (`Beschreibung` / `VK-Preis` / `Zeilenbetrag`). Merge keeps heuristic fields, tags items `source: "llm"`, and re-runs validation so the Σ-lines ≍ total checksum polices model output. Off unless `LLM_TIER=on` + `LLM_API_KEY` (see `docker-compose.yml`); stdlib-only, endpoint-agnostic via env vars |

Then **validation** cross-checks everything: Σ(line totals) ≍ subtotal,
subtotal + tax ≍ total; checks that pass *raise* item confidence, checks that
fail produce visible warnings instead of silent bad data.

### Decisions worth defending in the interview

The *algorithmic* decisions live here; the **product-level** decisions (review flow,
provenance, corrections as eval data, approval gate, known gaps incl. the not-yet-built
commodity-group suggestion) are in [`DESIGN-DECISIONS.md`](DESIGN-DECISIONS.md);
the normative requirements + traceability are in [`spec.md`](spec.md).

- **Why heuristics before LLM**: deterministic, testable, free, fast; an LLM
  fallback layer (for unknown labels/formats, and confidence-threshold rescue)
  is the natural next step — the module boundary (`pdf_extract.extract`)
  already returns a per-field confidence map the LLM layer can merge into.
- **The cheapest-first shape is what the industry converged on** (deep-research
  2026-10-20): in production document pipelines the parse/OCR stage — not the
  LLM — dominates cost and latency (~⅔ of processing time), so hybrid
  local-process + cloud-fallback designs cut per-page cost ~10× at ~96%
  accuracy; head-to-head evaluations show specialized structured pipelines
  beating general-purpose VLMs on printed structured layouts (VLMs take
  ~22–30 s/page on dense text). The tier ladder is that shape at interview
  scale (arXiv 2605.18818; OmniDocBench CVPR 2025, arXiv 2412.07626; IP&M
  2026, S0306457326004383).
- **Money parsing** is the real edge-case sink: `1.480,50` (DE) vs `1,480.50`
  (EN) are disambiguated by *which separator appears last*; a labeled line like
  `MwSt. 19%: 2.977,68` must not capture `19.` — we take the **last monetary
  token** in the line.
- **Stream tables** (quotes without grid lines) defeat default table detection;
  global whitespace-table detection mangles the whole page, so we detect the
  item-table **header row** and the **totals row** and only search the band
  between them.
- **Data-driven column inference**: if the header row is merged/mangled we
  infer column roles from cell values alone (money columns → unit/total,
  `XXX-123` token fraction → sku, integer column left of unit → qty).
- **Wrapped description rows** fold back into the previous item — once, globally,
  so a continuation line at the top of page 2 still finds its parent from page 1.
- **Carry-forward rows** (`Übertrag 1.438,00 €`) end the carried page's item
  run but don't kill the table: a numbered row (`1.1a`, `1.2`) reopens it, and
  prose right of the carry word becomes the next row's description head — an
  alternative block (`Alternativ zu vorstehender Position 1.1`) reads as the
  first alternative row's description. Alternative positions stay in the item
  list, excluded from the arithmetic with a visible warning (the supplier's
  own sum excludes them too), so the subtotal checksum stays meaningful.
- **Missing unit price** is inferred as `line_total / qty` at low confidence,
  never silently.
- **Validity vs delivery dates are separate fields** (`valid_until` / `needed_by`):
  conflating them silently is the classic quote-parsing bug; if a document
  names no delivery date, needed-by falls back to the validity date at low
  confidence *with a visible warning* instead.
- **Addresses / terms**: Bill-to / Ship-to are multi-line blocks, so they get a
  block reader (label remainder + following lines) that stops at the next
  labelled field, a blank line, or a table-header-looking line.
- **Multi-page quotes**: item tables merge across pages; the repeated column
  header on page 2 is consumed by the header matcher, never as an item row.
  Continuation pages usually have no lattice table, so their rows come from the
  text-strategy fallback, where wrapped description cells split across lines:
  neighbour text that is word-bearing is re-joined into the description
  (`parts package)` returns to its item), the row before a priced row is
  prepended to it, and totals/terms/header cells are excluded from the merge.
  Header fields are read from the accumulated text of ALL pages, so the grand
  total (last page) checksums against the line sum (all pages). Verified on
  `quote_meridian.pdf` (2 pages, 6 items) by the `demo-quote edge expectations`
  block in `scripts/test_all.py`. Very long documents are out of scope by
  design: a 10-20 page quote is handled by the same accumulate-per-page-then-
  validate flow, but realistic procurement quotes (Angebote) are 1-3 pages;
  50+ page price lists are a different workflow (catalog import), not
  PR-quote extraction.

### Known limits / next steps
- Scanned (image-only) PDFs need an OCR tier (Tesseract) before tier 1 —
  the pipeline is structured so it plugs in as tier 0; a scanned sample is
  where a vision-LLM tier shines and is the answer if one shows up.
  Deep-research caveat (2026-10-20): the current leaderboard for this class
  favors *document-specialized* VLMs (PaddleOCR-VL / DeepSeek-OCR class) over
  general-purpose models — but published numbers are largely self-reported
  and leaderboards move fast, so the tier should be chosen against our own
  corpora (the same discipline `test_all.py` already enforces on heuristics).
  A cautionary production datapoint: Grab rejected general-purpose document
  APIs for low-resource inputs (hallucination on unseen layouts, P99 latency
  3–4× the median) and fixed it with a full-parameter fine-tune of a
  vision-LLM — tail-latency and hallucination risk are exactly why the LLM
  here stays rescue-only and out of the hot path.
- LLM fallback tier (tier 4) is wired: `app/extraction/llm_tier.py` rescues
  weak heuristic results — off unless `LLM_TIER=on` + `LLM_API_KEY`, so the
  corpus sweeps below run offline and stay comparable. Proven on the
  QualityHosting invoice (German hosting bill: `Beschreibung`/`VK-Preis`/
  `Zeilenbetrag` outside the keyword dictionaries, totals printed as
  `Total EUR`): tier 4 reads all 7 items (Σ = 34,73 = total, checksum-backed)
  and the letterhead vendor, and the upload row moves `partial → extracted`
  with items visibly tagged `source: "llm"`. The rescue trigger matches the
  endpoint's `partial` definition (no items / no totals / total only
  inferred) — proven second on the 5-page stacked-label invoice
  `multipage-5p-de-6` (`Subtotal` / `MwSt` / `TOTAL` each with the value on
  the next line): tier 4 reads total 171.602,27 + tax where heuristics could
  only infer the total. `rescue` also backfills the `sku` slot when
  heuristics found items but left it null: per-row match on (qty, line_total),
  heuristic line text never overwritten — with `Code` now in the sku header
  dictionary (and hyphen-less `NIT112` codes valid there too), the backfill
  is rarely needed: all 72 skus on `multipage-5p-de-6` come from tier 2.
  Two cheaper-before-costlier rescue layers cover docs whose Code column
  never got claimed at all: a deterministic lift reclaims code-led
  descriptions (`NIT112 Nitrile…` → sku) when a majority of items read that
  way, then `_sku_starved` extends the tier-4 trigger to items-present-
  skus-all-null so the model backfills the missing codes per row.
- Only the first candidate table per page is treated as items (stray
  letterhead tables ignored by design).
- A vendor the document never names legibly stays `null` at low confidence —
  real offer `AN-OF…` prints no VAT id and no company-suffix letterhead line,
  so the field is honestly empty rather than guessed (visible in the review
  panel, correctable before submit).
- Open work items (upload triage tooling, `corrections` coverage, regression
  fixtures from uploads, failure-rate alerting, the OCR/vision-LLM tiers) are
  tracked in [`backlog.md`](backlog.md).

## External benchmark

`backend/scripts/bench.py` runs the extractor over the 113 digital-PDF slice of
[DrewKraken/invoice-extraction-benchmark v1](https://github.com/DrewKraken/invoice-extraction-benchmark)
(181 docs total; the other 68 are scans/photos/handwriting outside our tier-1
scope) and scores it with that repo's own field-level evaluator:

```
docker run --rm -v /tmp/bench-repo:/corpus:ro -v .../lio-interview/backend:/srv:ro \
  -v /tmp:/out lio-interview-app python /srv/scripts/bench.py /corpus/corpus/v1 /out/predictions.json
docker run --rm -v /tmp/bench-repo:/corpus:ro -v /tmp:/out -w /corpus \
  lio-interview-app python -m scorer.evaluate /out/predictions.json --markdown /out/scores.md
```

Baseline run (2026-10-19): key header fields 19.4%, vendor_name 52.5% (a
letterhead heuristic added during the run took it from 0%), line-item mean F1
0.39, all 5 arithmetic-broken invoices correctly flagged — and every
scan/photo expectedly scored 0 because no OCR tier exists yet. The benchmark's
category names are a ready-made edge-case taxonomy for the interview answer:
percent-vs-amount capture, VAT-inclusive layouts, credit notes printed five
ways, dual-currency payables, carried-forward multi-page rows, and vendors
whose letterhead is not labelled "Vendor:".

## Testing all local corpora

`backend/scripts/test_all.py` sweeps every PDF under `samples/` and asserts
three layers, cheapest first: **survival** (no crash), **coverage** (items /
subtotal / total found), and **ground truth** where the corpus ships answer
keys. The 68 photo/scan/handwritten images are outside PDF-scorable scope
(OCR tier) and the invoice-benchmark is scored field-level by its own repo
(above).

```bash
docker cp backend/scripts/test_all.py lio-app:/srv/scripts/test_all.py
docker exec lio-app python /srv/scripts/test_all.py /data/samples /tmp2/summary.json
# single corpus:
docker exec lio-app python /srv/scripts/test_all.py /data/samples /tmp2/summary.json generated-invoices
docker cp lio-app:/tmp2/summary.json .
```

Sweep results (2026-10-20, 933 PDFs):

| Corpus | n | extract ok | items det. | subtotal | total |
|---|---|---|---|---|---|
| our 5 demo quotes | 5 | 100% | 100% | 100% | 100% |
| challenge1 real offers (interview corpus) | 4 | 100% | 100% | 100% | 100% |
| invoice-benchmark | 113 | 100% | 95% | 96% | 96% |
| camelot table edge cases | 53 | 98% | 47% | n/a* | n/a* |
| invoice2data real vendors | 13 | 100% | 23% | 15% | 31% |
| generated-invoices (7 lang) | 746 | 100% | 19% | 22% | 22% |

\* camelot files are bare table tests — most print no subtotal/total line at
all, so 0 there is by design; the 47% item rate measures how many
(rotated / no-grid / spanned) tables the parser reads at all. One doc,
`health_protected.pdf`, is password-protected: reading it must surface as a
clean per-document error rather than a process crash (the one crash in 933).

GT scoring on generated-invoices (vs each PDF's paired answer JSON, nested
schema `totals.due/subtotal/tax`, `invoice.number`, `seller.name`,
`items[].line_total`): latin-script bucket total 32%, tax 34%, subtotal 34%
(was 18% before the word-geometry tier — the stacked totals-block reader
doubled it), vendor 0% (the corpus's first text line is the *title* "Invoice
Details", not the seller — the DrewKraken letterhead heuristic misfires here);
AR/ZH/JA ~0% — expected, they sit outside the EN/DE heuristics design space
(the LLM/vision-tier argument). The corpus exposed one *layout family* our
tier-3 readers don't cover yet: **stacked key/value labels** (`INVOICE
NUMBER` on one line, value on the next) and currency-prefixed amounts
(`USD3,500.00`) — same-line label anchoring mis-assigns the `REFERENCE`
line's value to the number slot. Named as a concrete next step, not fixed.

## Architecture

```
docker-compose ──┐
                 ├─ app (FastAPI, port 8020)
  ./samples ─────┤     ├─ POST /extract        upload → extract → draft PR
  uploads volume ┘     ├─ POST /extract-sample preview on bundled samples
                       ├─ POST /prs/:id/submit  (author only, from draft only)
                       │     config threshold (APPROVAL_THRESHOLD env):
                       │     total < threshold      → approved + order
                       │     total ≥ threshold      → pending_approval
                       ├─ POST /prs/:id/approve|reject  (buyer role only)
                       ├─ auth: session cookie (SQLite-backed,
                       │        roles: requester / buyer)
                       └─ / static single-page UI (no build step)
       SQLite in uploads volume: users, sessions, requests
```

State machine: `draft → submitted → (approved | pending_approval → approved | rejected)`
— enforced server-side per transition and in the CHECK constraint.

## Three data layers (and corrections as eval data)

The source PDF, the machine's view, and the human's view are deliberately
separate — only one of them is editable:

| Layer | Storage | Editable |
|---|---|---|
| Source PDF | `source_file` (uuid-prefixed, immutable disk path) | never — it is evidence |
| Raw extraction | `extract_json` — written once by the extractor, never updated | never — provenance & eval baseline |
| Official PR data | normalized columns on `requests` | yes — the review screen edits these, from `draft` state only |

Because layers 1–2 are immutable and layer 3 is human-corrected, the diff
between them captured at **submit** time (`requests.corrections`) is free
labeled evaluation data: it says *which field the human had to fix, per
supplier template* — the per-field extraction accuracy a full system would
measure exactly this way. The buyer sees the same diff before approving, so
approval is an informed decision. Rejected PRs are terminal by design: a
corrected re-submission is a new PR, so provenance stays append-only.

## Challenge 2 — re-matching order headers with their order lines

The interview's second challenge (procurement lost the header↔line links;
reconnect them so line prices sum exactly to each header's `total_price`
over exactly `total_lines` lines) is solved standalone in
[`challenge2/`](challenge2/):

```bash
python3 challenge2/solve.py          # shipped CSVs -> challenge2/matched_orders.json
python3 challenge2/test_solve.py     # 11-test suite (stdlib unittest)
```

All 5 shipped orders match and every one of the 13 lines is consumed (the
instance is fully conserved, so no match is "left over" and none dropped).
The solver is an exact pruned backtracking search in integer cents with a
`--node-budget` cap for NP-hard dense instances; design notes worth
defending (cents-vs-floats, complete-matching-first, honest orphan
reporting, the measured exhaustive cliff, CP-SAT as the next step at
adversarial scale) are in [`challenge2/README.md`](challenge2/README.md).