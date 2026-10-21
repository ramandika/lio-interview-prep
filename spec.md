# Specification — Purchase Request Tool (Challenge 1)

Normative requirements for the Challenge 1 system
(`docs/interview-developer-main/Challenge 1/README.md` is the normative input),
with a traceability matrix against the current implementation.

Companion documents: [`README.md`](README.md) (run/demo, extraction algorithm),
[`DESIGN-DECISIONS.md`](DESIGN-DECISIONS.md) (product rationale D1–D10),
[`backlog.md`](backlog.md) (open engineering items).

## 1. Purpose

Employees submit formal procurement requests; the procurement department
reviews, tracks, and processes them. Two accelerators are required by the
challenge: (a) upload a vendor offer → the request is auto-filled for review;
(b) the commodity group (one of 50) is suggested for the user rather than hand-picked.

## 2. Users & process context

| Role | Capability |
|---|---|
| Requester | Create PRs (manual or via upload), review/submit own drafts |
| Buyer (procurement) | See submitted PRs, approve/reject, record outcome |

Auth is session-cookie based with the two roles above (D9 in DESIGN-DECISIONS);
the boundary "who may submit vs. approve" is enforced server-side.

## 3. Functional requirements

Status tag per FR: `[done]` shipped · `[gap]` not yet built · `[partial]`.
Each FR cites the challenge-README item it serves.

### 3.1 Intake

- **FR-1** `[done]` A request can be created by typing a form, or by uploading a
  vendor offer which pre-fills it. *(Challenge: intake + "auto-fill from document")*
- **FR-2** `[done]` Upload produces a **draft** PR — never a submitted one. Only an
  explicit user-submit moves it. Drafts are editable; submitted PRs are not.
- **FR-3** `[done]` Upload capture exceeds the asked-for fields: besides
  vendor/VAT/items/totals, it extracts validity date, delivery/needed-by date,
  currency, addresses, payment terms. *(README example shows vendor, VAT ID,
  lines, total — the superset is the product stance D4/D5.)*
- **FR-4** `[done]` Every saved request remains retrievable with its source PDF,
  immutable raw extraction (`extract_json`), and human-corrected official data
  as separate layers. *(Challenge: "every request saved for future reference".)*

### 3.2 Validation — "only valid requests can be submitted"

- **FR-5** `[done]` Required fields present; at least one item; positive quantities;
  two-decimal money.
- **FR-6** `[done]` Arithmetic cross-checks are enforced and user-visible:
  Σ(line totals) ≍ subtotal, subtotal + tax ≍ total (D6). A failing checksum does
  not block submit but is displayed as a warning on the row and to the approver.
- **FR-7** `[done]` Submit is blocked for a draft whose extraction status is
  `failed`; `partial` submits carry the warning forward.

### 3.3 Extraction pipeline

- **FR-8** `[done]` Tiered, cheapest-first: PyMuPDF text layer → pdfplumber
  tables → label-anchored regex (EN/DE) → **word-geometry band reader**
  (tier 3b: header row gives column x-anchors, body words join columns by
  glyph center; arbitrated document-wide against the stream reader by a
  junk-penalty score + subtotal-coherence bonus) → opt-in LLM rescue
  (`LLM_TIER=on`, endpoint-agnostic via env). *(Challenge encourages
  LLM/tooling.)*
- **FR-9** `[done]` Per-field confidence and source highlighting: clicking a value
  in the review panel jumps + highlights the source text in the PDF (D2).
- **FR-10** `[done]` Derived values are labelled as derived, never silent
  (e.g. missing unit price inferred at low confidence, D4).
- **FR-11** `[done]` On submit, the human-vs-extraction diff is stored as
  `corrections` and shown to the approver (D3) — corrections double as eval data.
- **FR-12** `[done]` Unreadable/unsupported documents surface as a clean
  per-document error (`failed` + message), never a crash or silent empty result.

#### 3.3.1 PDF extraction — requirement definition (E-1…E-12)

Normative source: challenge README ("upload a document, which then auto-fills
the request", incl. its *Vendor Offer → Extracted Information* example) plus
product decisions D1–D6. These sub-requirements detail FR-8–FR-12; identifiers
`E-n` are referable from tests and code.

**Input scope**

- **E-1** `[done]` Digital PDFs with an embedded text layer are in scope; the
  extractor must also work on plain-text offers (same parsing path from text).
- **E-2** `[gap]` Scanned / image-only PDFs and phone photos are **out of scope
  by design** — they must surface as a clean, explicit `failed` (OCR/vision-LLM
  tier is a named future tier, README "Known limits"), never as empty output
  masquerading as extraction.
- **E-3** `[done]` English **and** German documents must extract. Money follows
  both decimal conventions; a token like `1.480,50` vs `1,480.50` is
  disambiguated by *which separator appears last*, and percent labels
  (`MwSt. 19%: …`) must never capture the percent as money.

**Output contract** (the shape `/extract` returns and `extract_json` stores)

- **E-4** `[done]` Structured draft: `vendor_name`, `vendor_vat_id`, `currency`,
  items[] with `sku, description, qty, unit, unit_price, line_total` (per item:
  `confidence` + `source: heuristic|llm|derived`), `subtotal`, `tax_total`,
  `total`, `valid_until`, `needed_by`, addresses, payment terms — plus a
  document-level `extract_status`: `extracted | partial | failed` with
  per-field confidence. Items merge across pages (repeated header rows are
  header, never a second item list).
- **E-5** `[done]` Extraction is a transformation service: `POST /extract`
  persists nothing by itself; it produces the draft that enters the same
  human-owned review flow as a hand-typed request (D1). Every extracted value
  carries page/position provenance for click-to-highlight (D2).

**Quality & trust requirements**

- **E-6** `[done]` Arithmetic checksums run on every extract:
  Σ(line totals) ≍ subtotal and subtotal + tax ≍ total. A passing checksum
  *raises* item confidence; a failing one flags rows with a visible warning —
  checksum status may never upgrade a row the arithmetic suspects (D6).
- **E-7** `[done]` Derived values are labelled derived and shown at low
  confidence: missing unit price inferred as `line_total / qty`; `needed_by`
  falls back to `valid_until` only with an explicit warning (D4/D5).
- **E-8** `[done]` The LLM tier is additive only: off unless
  `LLM_TIER=on` + key; it fires on weak heuristic results (no items / no
  totals / sku starved), merges without overwriting heuristic fields, and its
  output passes through the same checksums as heuristic output.

**Failure modes**

- **E-9** `[done]` Corrupt, password-protected, or unsupported files produce a
  per-document error state with a human-readable message. Survival over the
  whole `samples/` corpus (933 docs at last sweep) must remain crash-free.
- **E-10** `[done]` A wrong but plausible extraction must remain *visible as
  suspect* — via low confidence, warnings, or checksum flags — so the review
  flow can catch it; silent best-guess filling is a spec violation.

**Performance & testability**

- **E-11** `[done]` The heuristic path is deterministic, seconds-fast per
  document, and runs offline (no LLM key required) — the LLM tier is a rescue
  layer, not a dependency (NFR-2).
- **E-12** `[done]` Extraction is regression-tested by `scripts/test_all.py`
  over `samples/` (survival → coverage → ground truth where present) and
  scored externally on the DrewKraken benchmark corpus; the bundled demo
  quotes and the four real interview offers (`samples/challenge1-offers/`,
  per-field ground truth incl. quote number, vendor, VAT id and per-line
  amounts) must extract fully. Extracted corrections captured at submit
  (FR-11) are the eval data for real uploads.

### 3.4 Commodity group

- **FR-13** `[gap]` The request carries a **primary** commodity group from the
  50-group, 7-category table in the challenge README (seeded data + FK column).
  One group per request — the challenge schema's single field is kept.
- **FR-14** `[gap]` Classification runs **per order line** (from its description
  and the request title), then rolls up to the primary group chosen by **value
  share** (Σ line totals per group), not by line count. Baseline: keyword/TF-IDF
  ranking; LLM classification as the suggestion layer, reusing
  `app/extraction/llm_tier.py` plumbing.
- **FR-15** `[gap]` The suggestion is shown on the review panel with a short
  rationale and — when lines disagree — the value breakdown per group; always
  editable, never silently chosen (answers: "they always pick the wrong one").
- **FR-16** `[gap]` **Multi-group requests** (one document with items from
  different groups): a visible multi-group warning with the breakdown
  (lines/total per group) is shown; the primary group remains the highest-value
  group; the user may accept or override. Per-line classifications are persisted
  on the line rows either way — the request field is the rollup, the data stays
  group-accurate for reporting. *(Decision 2026-10-20: no request splitting.)*
- **FR-17** `[gap]` The store keeps the suggested primary group and the final
  group separately, so suggestion quality is measurable.

### 3.5 Overview & status tracking

- **FR-18** `[done]` An overview lists requests with current status; buyers get
  an approval queue of submitted PRs.
- **FR-19** `[done]` Status lifecycle, enforced server-side per transition and by
  a DB CHECK constraint; every event is recorded (who/when/from→to, D10):
  `draft → submitted → (approved | pending_approval → approved | rejected)`.
  `approved`/`rejected` are terminal.
- **FR-20** `[done]` Mapping to the challenge's three statuses is preserved and
  documented: Open ≈ draft/submitted, In Progress ≈ pending_approval, Closed ≈
  approved/rejected. *(Challenge asks Open/In Progress/Closed; the richer model
  covers the stated intent — process the request, keep transparency — while
  making "retain every status update" auditable.)*
- **FR-21** `[done]` Approvals can be gated by a server-side `APPROVAL_THRESHOLD`
  (below → auto-approve with order id); threshold is config, never client input (D7).

## 4. Data model (implemented)

```
users(id, username, role[requester|buyer])
sessions(sid, user_id, expires)
requests(id, user_id, status, source_file, extract_json, extract_status,
         corrections, currency, vendor_name, vendor_vat_id, valid_until,
         needed_by, subtotal, tax_total, total, approver_id, order_id,
         created_at, …)      — status CHECK-constrained per FR-19
items(id, request_id, position, sku, description, qty, unit, unit_price,
      line_total, confidence, source, …)
```

Extraction-layer fields live in `extract_json` (immutable); official PR data is
the normalized editable columns — three data layers, one path each.

## 5. API contract

```
POST /login /logout                 GET  /me /users
GET  /samples                       POST /extract            (auth: requester)
POST /extract-sample                GET  /prs?…              (scoped per role)
GET  /prs/{id}                      GET  /prs/{id}/pdf
POST /prs/{id}/submit               POST /prs/{id}/approve   (auth: buyer)
POST /prs/{id}/reject               GET  /config             (threshold visibility)
```

Illegal transitions and unauthorized role actions return 4xx with an
explanatory message; invalid submits never mutate state.

## 6. Non-functional requirements

- **NFR-1** Runs with `docker compose up -d` → `http://localhost:8020`, one container, SQLite in a volume.
- **NFR-2** Extraction works offline (no LLM key): the heuristic tiers must cover the bundled demo corpus; LLM is additive, never a hard dependency.
- **NFR-3** Multilingual by design: EN/DE documents (German comma-decimal money) handled at every tier that can see them.
- **NFR-4** Append-only provenance: source file, raw extraction, and status events are never edited or deleted by any API.
- **NFR-5** All local corpora in `samples/` are exercised by `backend/scripts/test_all.py` (survival → coverage → ground-truth scoring); extraction changes must not regress the sweep table in README.

## 7. Out of scope

Auth beyond the two process roles (no SSO/RBAC), OCR of scanned/image PDFs,
multi-currency conversion, ERP/PO integration, formal approval chains,
production hosting.

## 8. Traceability — challenge requirement → implementation

| Challenge README item | FRs | Status |
|---|---|---|
| Intake form with all fields | FR-1, FR-5 | done |
| Upload offer → auto-fill request | FR-1–FR-4, FR-8–FR-12 | done |
| "Only valid requests can be submitted" | FR-5–FR-7 | done |
| "Ensure every request is saved" | FR-4 | done |
| "Every status update retained accurately" | FR-18–FR-21 | done |
| Status Open / In Progress / Closed | FR-20 | done (superset) |
| Web front-end, usable product | review UI, overview, queue, highlighting | done |
| Commodity group (auto, not user-picked) | FR-13–FR-17 | **gap** — first item to build |
| LLM usage encouraged | FR-8 (opt-in rescue tier + planned suggestion) | done |

## 9. Acceptance criteria (demo-verifiable)

1. alice (requester) uploads a sample quote PDF → draft with extracted fields,
   confidence per field, and click-to-highlight provenance.
2. Submitting is impossible while a required field/line is missing or a
   checksum fails with blocking severity.
3. bob (buyer) sees submitted PRs with corrections noted; approve/reject works
   only for buyers and only from states that allow it.
4. Threshold behavior switches by server env only (D7).
5. `test_all.py` sweep over `samples/` shows no crashes on 100% of docs.
6. *(once FR-13–17 ship)* A German offer upload pre-selects the commodity group
   with a rationale; changing it stores both values.

*Last updated: 2026-10-20.*