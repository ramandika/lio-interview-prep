# Per-document failure table — baseline run (2026-10-19)

This is the engineer-review triage view of the benchmark run (see README,
"External benchmark"): one row per document that fails at least one field,
scored by the benchmark's own scorer, so each engineering fix can be judged by
*which rows it heals*, not by aggregate drift. Regenerate after any extractor
change with:

    python3 backend/scripts/failure_table.py /tmp/bench-repo /tmp/predictions2.json bench-failures.md

(preds come from `bench.py`, run over the 113 digital-PDF docs; the 68
scan/photo docs are OCR-tier scope and listed nowhere here. The only two
"fully correct" docs, `hard-statement-na/za`, pass because nothing is printed
— empty predictions score correct on their sparse answer keys.)

Fields `invoice_date`/`due_date`/`document_type` are excluded from the
failure display: `bench.py` maps them to null by documented decision, and
`flag` fails everywhere because any warning (even a benign "needed-by not
found") sets `flagged: true` — these are mapping choices, not parsing
failures, and listing them would drown the real signal.

## Fix families (engineer review: one rule change heals a whole family)

Reading the failing-field counts at the bottom, five clusters explain nearly
every row — each is a *generalization* of an existing heuristic, not a new
parser:

1. **Label dictionary gaps** (~all rows) — the extractor only knows quote-style
   labels. `Invoice No:`, `B/L No`, `Tax invoice #`, and stacked
   `TOTAL INVOICE AMOUNT PAYABLE` / value-on-next-line labels all miss →
   `invoice_number` and `total` fail on most docs. Fix: extend
   `LABEL_PATTERNS`, plus a stacked-label reader (value N lines under the
   label).
2. **Currency dictionary gaps** — `CUR_SYMS`/the currency regex know only
   EUR/GBP/USD and symbols; ZAR, NAD, ZMW, MUR are invisible, so `currency`
   fails on every local-currency doc and dual-currency totals can't be
   validated. Fix: ISO-code token list + per-amount currency tagging.
3. **Label-anchor precision** — `VAT No:` anchors the `tax` field (the
   negative lookahead exists only for `tax`, not `vat`), and percent-vs-amount
   capture grabs the percentage. Fix: port the lookahead to `\bvat\b`, prefer
   money-shaped tokens with 2 decimals.
4. **Item-table header gaps** — the ≥3-keyword gate misses headers like
   `Description Qty Unit USD Amount USD Amount ZAR` (`unit`, `amount` unknown);
   currency-column suffixes and space-grouped ZA amounts
   (`R 1 966,16`) mislead data-inferred column mapping. Fix: widen keyword
   list, let `_idx_by_data` prefer the correct currency column for line totals.
5. **Document classes with no reader** — freight trip references, multiple
   invoices in one file, credit-note sign semantics (`-R`, `(R)`, `CR`),
   statements. These need new modules (or the LLM tier); they are the honest
   "beyond heuristics" rows.

## The table

Scored by the benchmark's own scorer against ground truth. 111 of 113 digital-PDF docs fail at least one field (line_items wrong if F1 < 1.0; credit-note money compares magnitude; ±1 cent noted beside money fields).

| Doc | C | D | score | line F1 | failed fields |
|---|---|---|---|---|---|
| currency-dual-usd-zar-3 | currency | hard | 0.00 | 0.0 | currency, invoice_number, subtotal, tax, total, vendor_name, line_items (of 3) |
| multipage-2p-za-1 | multipage | medium | 0.03 | 0.05 | currency, invoice_number, subtotal, tax, total, vendor_name, line_items (of 20) |
| freight-carrier-na-5 | freight | medium | 0.06 | 0.0 | currency, invoice_number, references, subtotal, tax, total, line_items (of 4) |
| freight-carrier-za-3 | freight | medium | 0.06 | 0.0 | currency, invoice_number, references, subtotal, tax, total, line_items (of 3) |
| freight-carrier-za-4 | freight | medium | 0.06 | 0.0 | currency, invoice_number, references, subtotal, tax, total, line_items (of 4) |
| freight-trip-reference-1 | freight | medium | 0.06 | 0.0 | currency, invoice_number, references, subtotal, tax, total, line_items (of 2) |
| credit-cr-suffix-za | credit | hard | 0.07 | 0.0 | currency, invoice_number, subtotal, tax, total, line_items (of 3) |
| credit-note-minus-za | credit | easy | 0.07 | 0.0 | currency, invoice_number, subtotal, tax, total, line_items (of 2) |
| credit-r-minus-2 | credit | medium | 0.07 | 0.0 | currency, invoice_number, subtotal, tax, total, line_items (of 3) |
| currency-dual-eur-nad-2 | currency | hard | 0.07 | 0.0 | currency, invoice_number, subtotal, tax, total, line_items (of 3) |
| currency-multi-line-zar | currency | hard | 0.07 | 0.0 | currency, invoice_number, subtotal, tax, total, line_items (of 4) |
| hard-lines-vs-subtotal | hard | hard | 0.07 | 0.0 | currency, invoice_number, subtotal, tax, total, line_items (of 4) |
| hard-multiple-invoices-2 | hard | hard | 0.07 | 0.0 | currency, invoice_number, subtotal, tax, total, line_items (of 4) |
| tax-percentage-line-2 | tax | hard | 0.07 | 0.0 | currency, invoice_number, subtotal, tax, total, line_items (of 3) |
| tax-sage-subtotal-after-vat-2 | tax | hard | 0.07 | 0.0 | currency, invoice_number, subtotal, tax, total, line_items (of 4) |
| tax-zero-rated-za | tax | medium | 0.07 | 0.0 | currency, invoice_number, subtotal, tax, total, line_items (of 1) |
| multipage-6p-za-8 | multipage | hard | 0.13 | 0.11 | currency, invoice_number, subtotal, tax, total, line_items (of 98) |
| multipage-8p-na-10 | multipage | hard | 0.13 | 0.12 | currency, invoice_number, subtotal, tax, total, line_items (of 110) |
| credit-brackets-us | credit | medium | 0.14 | 0.0 | invoice_number, tax, total, vendor_name, line_items (of 1) |
| credit-ndc-na | credit | hard | 0.14 | 0.0 | currency, invoice_number, tax, total, line_items (of 2) |
| credit-note-minus-gb | credit | easy | 0.14 | 0.0 | invoice_number, tax, total, vendor_name, line_items (of 1) |
| currency-multi-line-nad | currency | hard | 0.14 | 0.0 | currency, invoice_number, tax, total, line_items (of 4) |
| multipage-4p-za-5 | multipage | hard | 0.14 | 0.13 | currency, invoice_number, subtotal, tax, total, line_items (of 58) |
| multipage-5p-na-7 | multipage | hard | 0.14 | 0.13 | currency, invoice_number, tax, total, vendor_name, line_items (of 61) |
| tax-percentage-line-1 | tax | hard | 0.14 | 0.0 | currency, invoice_number, tax, total, line_items (of 3) |
| multipage-3p-na-3 | multipage | medium | 0.15 | 0.16 | currency, invoice_number, subtotal, tax, total, line_items (of 35) |
| clean-za-code-zebra | clean | easy | 0.17 | 0.33 | currency, invoice_number, subtotal, tax, total, vendor_name, line_items (of 6) |
| tax-inclusive-total-za | tax | medium | 0.18 | 0.22 | currency, invoice_number, subtotal, tax, total, line_items (of 4) |
| freight-carrier-us-2 | freight | medium | 0.19 | 0.0 | invoice_number, references, tax, total, line_items (of 5) |
| currency-zmw-2 | currency | easy | 0.20 | 0.25 | currency, invoice_number, tax, total, vendor_name, line_items (of 4) |
| clean-us-clean-simple | clean | easy | 0.21 | 0.0 | invoice_number, tax, total, line_items (of 3) |
| freight-clearing-na-1 | freight | hard | 0.23 | 0.33 | currency, invoice_number, references, subtotal, tax, total, line_items (of 6) |
| freight-clearing-na-3 | freight | hard | 0.23 | 0.33 | currency, invoice_number, references, subtotal, tax, total, line_items (of 6) |
| freight-clearing-za-4 | freight | hard | 0.23 | 0.33 | currency, invoice_number, references, subtotal, tax, total, line_items (of 6) |
| credit-r-minus-1 | credit | medium | 0.24 | 0.33 | currency, invoice_number, subtotal, tax, total, line_items (of 3) |
| clean-za-band-space-groups | clean | easy | 0.25 | 0.5 | currency, invoice_number, subtotal, tax, total, vendor_name, line_items (of 4) |
| multipage-8p-us-9 | multipage | hard | 0.27 | 0.12 | invoice_number, tax, total, line_items (of 117) |
| freight-clearing-za-5 | freight | hard | 0.31 | 0.5 | currency, invoice_number, references, subtotal, tax, total, line_items (of 6) |
| currency-nad-2 | currency | easy | 0.32 | 0.5 | currency, invoice_number, subtotal, tax, total, line_items (of 4) |
| hard-multiple-invoices-3 | hard | hard | 0.32 | 0.5 | currency, invoice_number, subtotal, tax, total, line_items (of 4) |
| multipage-5p-de-6 | multipage | hard | 0.32 | 0.21 | invoice_number, tax, total, line_items (of 72) |
| tax-sage-subtotal-after-vat-1 | tax | hard | 0.32 | 0.5 | currency, invoice_number, subtotal, tax, total, line_items (of 4) |
| clean-gb-serif | clean | easy | 0.34 | 0.25 | invoice_number, tax, total, line_items (of 4) |
| credit-brackets-gb | credit | medium | 0.39 | 0.5 | invoice_number, tax, total, vendor_name, line_items (of 2) |
| hard-wrong-vat-amount | hard | hard | 0.39 | 0.5 | currency, invoice_number, tax, total, line_items (of 4) |
| tax-inclusive-subtotal-na | tax | hard | 0.45 | 0.75 | currency, invoice_number, subtotal, tax, total, line_items (of 4) |
| clean-gb-mono-plain | clean | medium | 0.46 | 0.5 | invoice_number, tax, total, line_items (of 4) |
| clean-us-net30-terms | clean | easy | 0.46 | 0.5 | invoice_number, tax, total, line_items (of 2) |
| freight-clearing-na-2 | freight | hard | 0.46 | 0.67 | currency, invoice_number, references, tax, total, line_items (of 6) |
| clean-us-many-line-items | clean | easy | 0.48 | 0.53 | invoice_number, tax, total, line_items (of 15) |
| multipage-2p-us-2 | multipage | medium | 0.48 | 0.54 | invoice_number, tax, total, line_items (of 24) |
| currency-nad-1 | currency | easy | 0.52 | 0.75 | currency, invoice_number, tax, total, line_items (of 4) |
| freight-trip-reference-2 | freight | medium | 0.56 | 1.0 | currency, invoice_number, references, subtotal, tax, total, vendor_name |
| currency-dual-usd-zar-1 | currency | hard | 0.57 | 1.0 | currency, invoice_number, subtotal, tax, total, vendor_name |
| clean-nl-two-column | clean | easy | 0.59 | 0.75 | invoice_number, tax, total, line_items (of 4) |
| currency-usd-1 | currency | easy | 0.59 | 0.75 | invoice_number, tax, total, line_items (of 4) |
| freight-carrier-na-6 | freight | medium | 0.62 | 1.0 | currency, invoice_number, references, subtotal, tax, total |
| freight-clearing-za-6 | freight | hard | 0.62 | 1.0 | currency, invoice_number, references, subtotal, tax, total |
| freight-trip-reference-3 | freight | medium | 0.62 | 1.0 | currency, invoice_number, references, tax, total, vendor_name |
| credit-ndc-za-ref | credit | hard | 0.64 | 1.0 | currency, invoice_number, subtotal, tax, total |
| currency-dual-usd-nad-4 | currency | hard | 0.64 | 1.0 | currency, invoice_number, subtotal, tax, total |
| currency-nad-bare-dollar | currency | hard | 0.64 | 1.0 | currency, invoice_number, tax, total, vendor_name |
| currency-zar-1 | currency | easy | 0.64 | 1.0 | currency, invoice_number, subtotal, tax, total |
| currency-zmw-1 | currency | easy | 0.64 | 1.0 | currency, invoice_number, tax, total, vendor_name |
| hard-po-beside-invoice-number-2 | hard | hard | 0.64 | 1.0 | currency, invoice_number, subtotal, tax, total |
| hard-po-beside-invoice-number-3 | hard | hard | 0.64 | 1.0 | currency, invoice_number, tax, total, vendor_name |
| hard-total-inflated | hard | hard | 0.64 | 1.0 | currency, invoice_number, subtotal, tax, total |
| hard-total-small-residual | hard | hard | 0.64 | 1.0 | currency, invoice_number, subtotal, tax, total |
| tax-exclusive-vat-column-za | tax | easy | 0.64 | 1.0 | currency, invoice_number, subtotal, tax, total |
| tax-inclusive-total-na | tax | medium | 0.64 | 1.0 | currency, invoice_number, subtotal, tax, total |
| tax-sage-subtotal-after-vat-3 | tax | hard | 0.64 | 1.0 | currency, invoice_number, subtotal, tax, total |
| tax-withholding-zm | tax | hard | 0.64 | 1.0 | currency, invoice_number, tax, total, vendor_name |
| tax-zero-rated-na | tax | medium | 0.64 | 1.0 | currency, invoice_number, subtotal, tax, total |
| freight-trip-reference-4 | freight | medium | 0.69 | 1.0 | currency, invoice_number, references, tax, total |
| clean-de-grid-eu-decimals | clean | medium | 0.71 | 1.0 | invoice_number, tax, total, vendor_name |
| clean-na-bulk-consumables | clean | easy | 0.71 | 1.0 | currency, invoice_number, tax, total |
| clean-na-center-letterhead | clean | easy | 0.71 | 1.0 | currency, invoice_number, tax, total |
| clean-zm-banner | clean | easy | 0.71 | 1.0 | currency, invoice_number, tax, total |
| credit-unsigned-na | credit | hard | 0.71 | 1.0 | currency, invoice_number, tax, total |
| currency-zar-2 | currency | easy | 0.71 | 1.0 | currency, invoice_number, tax, total |
| hard-missing-invoice-number-2 | hard | hard | 0.71 | 1.0 | currency, tax, total, vendor_name |
| hard-po-beside-invoice-number-1 | hard | hard | 0.71 | 1.0 | currency, subtotal, tax, total |
| tax-withholding-na | tax | hard | 0.71 | 1.0 | currency, invoice_number, tax, total |
| freight-carrier-us-1 | freight | medium | 0.75 | 1.0 | invoice_number, references, tax, total |
| clean-fr-right-header | clean | medium | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-credit-applied | clean | easy | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-delivery-alias | clean | easy | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-freight-alias | clean | easy | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-fuel-surcharge | clean | easy | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-handling-alias | clean | easy | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-landscape | clean | medium | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-overpaid-invoice | clean | hard | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-promo-code | clean | easy | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-service-fee | clean | easy | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-sh-abbrev | clean | easy | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-shipping-and-discount | clean | easy | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-single-service | clean | easy | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-tax-only | clean | easy | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-trade-discount | clean | easy | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-two-tax-lines | clean | easy | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-with-discount | clean | easy | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-with-shipping | clean | easy | 0.79 | 1.0 | invoice_number, tax, total |
| clean-us-zero-shipping-explicit | clean | easy | 0.79 | 1.0 | invoice_number, tax, total |
| currency-eur-1 | currency | easy | 0.79 | 1.0 | invoice_number, tax, total |
| currency-eur-2 | currency | easy | 0.79 | 1.0 | invoice_number, tax, total |
| currency-gbp-1 | currency | easy | 0.79 | 1.0 | invoice_number, tax, total |
| currency-gbp-2 | currency | easy | 0.79 | 1.0 | invoice_number, tax, total |
| currency-usd-2 | currency | easy | 0.79 | 1.0 | invoice_number, tax, total |
| hard-missing-invoice-number-1 | hard | hard | 0.79 | 1.0 | currency, tax, total |
| multipage-3p-gb-4 | multipage | medium | 0.79 | 1.0 | invoice_number, tax, total |
| tax-exclusive-vat-column-gb | tax | easy | 0.79 | 1.0 | invoice_number, tax, total |

## Failed-field counts by category (what one fix would heal)

| category | field | docs |
|---|---|---|
| clean | invoice_number | 31 |
| clean | tax | 31 |
| clean | total | 31 |
| currency | invoice_number | 19 |
| currency | tax | 19 |
| currency | total | 19 |
| freight | invoice_number | 16 |
| freight | references | 16 |
| freight | tax | 16 |
| freight | total | 16 |
| freight | currency | 14 |
| tax | invoice_number | 14 |
| tax | tax | 14 |
| tax | total | 14 |
| currency | currency | 13 |
| tax | currency | 13 |
| freight | subtotal | 11 |
| hard | currency | 11 |
| hard | tax | 11 |
| hard | total | 11 |
| credit | invoice_number | 10 |
| credit | tax | 10 |
| credit | total | 10 |
| freight | line_items | 10 |
| multipage | invoice_number | 10 |
| multipage | tax | 10 |
| multipage | total | 10 |
| tax | subtotal | 10 |
| multipage | line_items | 9 |
| clean | line_items | 8 |
| credit | line_items | 8 |
| currency | line_items | 8 |
| hard | invoice_number | 8 |
| credit | currency | 7 |
| currency | subtotal | 7 |
| hard | subtotal | 7 |
| tax | line_items | 7 |
| multipage | currency | 6 |
| clean | currency | 5 |
| credit | subtotal | 5 |
| currency | vendor_name | 5 |
| multipage | subtotal | 5 |
| hard | line_items | 4 |
| clean | vendor_name | 3 |
| credit | vendor_name | 3 |
| clean | subtotal | 2 |
| freight | vendor_name | 2 |
| hard | vendor_name | 2 |
| multipage | vendor_name | 2 |
| tax | vendor_name | 1 |
