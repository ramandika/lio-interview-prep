# Design Decisions — the product level

> Complement to [`README.md`](README.md) (run/demo, extraction *algorithm*) and
> [`backlog.md`](backlog.md) (engineering work). This file records the **product-level**
> decisions — what the UI does, what it refuses to do silently, and *why* — i.e. the
> answers to "usable product" questions an evaluator can ask in a live demo.

---

## 0. The one stance everything follows

**Extraction pre-fills a review flow; it never creates a request by itself.**

The user is in charge; the machine accelerates. Every consequence below is a
specialization of this: per-field confidence, source highlighting, corrections
captured as data, submit gating, low-confidence warnings instead of silent best guesses.

Why: the target user is a procurement team processing formal requests. A silently wrong
VAT ID or total costs more (rework downstream, audit trouble) than the two clicks of
review costs. The interview feedback in the Challenge description says requestors reach
for the vendor offer first — so the upload path is a *shortcut into the form*, not a
replacement for the form.

---

## 1. Decisions, with prepared follow-up answers

### D1 — Upload lands as a **draft PR**, never as a submitted one

- **What:** `POST /extract` creates the PR in `status='draft'` with an `extract_status`
  (`extracted` / `partial` / `failed`) and per-field confidence. Only an explicit,
  user-initiated *Submit* moves it out of draft — and submit is blocked behind the
  same validation rules a manually-created request faces.
- **Why:** README: "Ensure that only valid requests can be submitted." Extraction can be
  wrong (see the benchmark numbers in the README) — so the machine's output enters the
  same human-owned state machine as a hand-typed request, at the *lowest* privilege level.
- **If asked "what if extraction is wrong?"** → The form is the source of truth, extraction
  is a pre-fill service; `partial`/`failed` rows keep the PDF (`source_file`) and expose a
  clean error, so the worst case is manual fill with the document already open.

### D2 — Every extracted value shows where it came from

- **What:** In the Review & submit panel, clicking a field chip or an item row jumps the
  embedded PDF viewer (pdf.js text layer) to the page/position of the source text and
  highlights it.
- **Why:** Reviewing means "spot-check the claims", not "re-read the PDF". That converts
  trust from an article of faith into something verifiable per value — the same reason
  the extractor itself carries a provenance-friendly shape.
- **If asked about cost** → Nothing extra persisted per request: highlight positions come
  from searching the PDF's own text layer at click time; matching is separator-normalized
  (`5.763,60` and `5,763.60` both hit).

### D3 — Corrections are not "fixed quietly" — they are recorded and fed to the approver

- **What:** On submit, the server diffs the human-corrected official columns against the
  raw extraction and stores the diff as `corrections`. The buyer's approval view shows
  it; the PR detail card shows it thereafter.
- **Why:** Two consumers, one write. (a) An **informed approval**: the buyer sees that the
  vendor name was hand-corrected before saying yes. (b) **Evaluation data**: the
  corrections are the *upload-side ground truth* the benchmark corpora already give us
  on the batch side — which fields humans correct most is a direct quality metric for
  the extractor (tracked as a backlog item).
- **If asked "why not just overwrite?"** → Overwriting deletes the only eval signal that
  exists for real uploads with unknown layouts.

### D4 — Inferred values are visible as inferred — never silent

- **What:** Missing unit prices are inferred (`line_total / qty`, e.g. the per-lot
  `VT-2287` row priced "on request") but presented at **low confidence with a warning**;
  `needed_by` falls back to the validity date only with a visible warning ("no delivery
  date on the document"). A passing Σ(lines) ≍ subtotal checksum *raises* confidence;
  a failing checksum keeps the row flagged — checksum status cannot upgrade a row the
  arithmetic already suspects.
- **Why:** The dangerous failures are the confident-looking wrong ones. The demo
  (`quote_vantorre.pdf`) keeps one red row among green rows on purpose: the buyer should
  look at exactly that row, and the UI says which numbers were *derived, not read*.
- **If asked "why not mark it and auto-fix?"** → An auto-fix that nobody notices is the
  historical bug in procurement tools. Infer → flag → let the human accept is the same
  contract as D1, applied one level down.

### D5 — Dates that mean different things are different fields

- **What:** `valid_until` (the offer's validity) and `needed_by` (when the requestor needs
  it) are separate columns. No silent conflation.
- **Why:** Conflating them is *the* classic quote-parsing bug — the offer's validity date
  is the most prominent date on the document and the wrong thing to copy. When a
  document names no delivery date, the fallback exists but is labelled (D4).
- **If asked "isn't needed-by the user's job to type?"** → It is — the field is editable
  in the review panel; the fallback is a convenience with a warning, not a claim.

### D6 — Money is validated by cross-check, and cross-checks are user-visible

- **What:** Validation re-derives Σ(line totals) ≍ subtotal, subtotal + tax ≍ total and
  exposes pass/fail as part of the review state, rather than trusting any one printed
  number. Totals are stored with currency (`currency` column), amounts keep the
  document's own precision.
- **Why:** German/EN formats (`1.480,50` vs `1,480.50`) and labels like `MwSt. 19%: 2.977,68`
  make single-guess parsing fragile; the checksum is the only detector that is
  format-agnostic. When checksum fails, the user sees *which* sum disagrees — the
  number mismatch is the message.
- **If asked "why not normalize to EUR at ingestion?"** → The offer's currency and its
  arithmetic are ground truth of the *document*; conversion is a later, explicit concern
  of the procurement process, not of extraction.

### D7 — The approval threshold is server config, not client input

- **What:** `APPROVAL_THRESHOLD` is an environment/server setting. A total below it
  auto-approves with an order id; at/above it goes to the buyer's approval queue
  (buyer role only; requester cannot approve their own request).
- **Why:** A threshold the client sends is a threshold the client controls — every
  requester would then send `0.01`. The demo drives this point: changing the workflow is
  a server restart, not a UI toggle.
- **If asked "who edits the threshold?"** → Whoever deploys (env), i.e. the
  procurement/tooling operator — the role that owns process policy, not the requestor.

### D8 — The buyer reviews corrections *before* approving

- **What:** The approval queue row carries the `corrections` note from D3.
- **Why:** The human who signs off on spending money should know the vendor name on the
  form was hand-edited. Zero-cost to display, real difference in trust.

### D9 — Roles that match the process, no more

- **What:** Two roles — `requester` (intake) and `buyer` (approval) — session-cookie
  auth, SQLite-backed. A requester sees their PRs and the submit button; only buyers
  see the approval queue and carry approve/reject.
- **Why:** The README's evaluation is intake + overview + status tracking; a
  full RBAC/auth stack is a non-goal (see §3). Two roles cover the whole state
  machine without inventing actors the process doesn't have.
- **If asked "production auth?"** → SSO/AD is the answer for a real customer; the
  boundary (who may submit vs. approve) is already server-side, so swapping the
  identity source doesn't move the permissions.

### D10 — State transitions are enforced where they can't be lied about

- **What:** The machine is `draft → submitted → (approved | pending_approval → approved | rejected)`,
  enforced *server-side* per transition *and* mirrored in a DB CHECK constraint.
  Every event that moves a PR is attributable (who/when/what diff).
- **Why:** README: "retain every status update accurately." A client can always lie;
  the server transition table plus the CHECK constraint is what makes the history
  an audit trail rather than a UI convention.

---

## 2. The five places the product decisions become visible in a demo

*(These are the prepared demo/Q&A beats — where the decisions above stop being words.)*

1. **Empty state / first screen** — login lands on the intake flow with the sample
   documents one click away; the fastest path of the product (`upload → review → submit`)
   needs no instruction text.
2. **The red row** (`quote_vantorre.pdf`) — D4 and the checksum in D6 visible in one
   screenshot: derived numbers flagged, the bad row stays red.
3. **The correction trail** (`quote_atlas.pdf`) — edit vendor name inline, submit, and
   the detail card + buyer queue both show what changed from the raw extraction (D3/D8).
4. **The approval gate** — bob (buyer only) sees the queue; threshold behavior is
   configured server-side (D7), shown by restarting with a different `APPROVAL_THRESHOLD`.
5. **Multi-merge** (`quote_meridian.pdf`) — a 2-page quote merges its item tables and
   grand-total checksums across pages, so the review panel is one list, not two pages.

---

## 3. Known gaps vs. the Challenge README (on the record)

- **Commodity group is not implemented yet.** The Challenge asks for the 50-group
  category on the intake; today's schema has no `commodity_group` column and the
  UI does not offer the selection. The planned shape matches the product stance above:
  the group is **suggested** (against the 50-group table) on the review panel, shown
  with a short reason, always editable, never silently chosen; keyword/TF-IDF ranking
  as the cheap baseline, LLM classification as the suggestion layer (the rescue-tier
  plumbing from `app/extraction/llm_tier.py` is reused). This is the first item to
  add before the deadline — it is deliberately recorded here as a gap, not hidden.
- **Status model is richer than asked** (approval workflow beyond "Open / In Progress /
  Closed") — kept because the README's "retain every status update accurately" is
  stronger under an approval gate; the asked-for statuses map onto it (`Open ≈ draft`,
  `In Progress ≈ submitted/pending`, `Closed ≈ approved/rejected`).
- **OCR tier absent** — scans/photos are out of scope of tiers 1–3 by design; the
  pipeline leaves the slot open (tier 0) and the LLM/vision tier is where that
  answer lives (README "Known limits").

---

## 4. Non-goals, on purpose

- No ERP/PurchaseOrder integration — intake + review + approval + order-id stub is the README's ask.
- No multi-tenant/auth stack beyond the two process roles (D9).
- No in-house OCR; no vision model in the loop by default (LLM tier is opt-in via env).
- No formal multi-step approval chains; the buyer step + threshold covers the asked-for
  transparency ("In Progress") without inventing workflow the customer didn't describe.

---

## 5. Where 2026 industry research agrees (deep-research, 2026-10-20)

A deep-research pass (~25 adversarially verified sources: arXiv, OmniDocBench
CVPR 2025, production engineering blogs — Grab, NVIDIA, AWS — and extraction
startups) checked this product's stance against how document extraction is
actually built today. Three findings map directly onto decisions above:

- **Cheapest-first + LLM-at-the-edge is the industry shape, not a niche
  opinion.** In production pipelines the parse/OCR stage — not the model —
  dominates cost and latency (~⅔ of processing time), and hybrid
  local-process + cloud-fallback designs cut per-page cost ~10× at ~96%
  accuracy (arXiv 2605.18818). That is D1's stance one level down: the free
  deterministic layer does the volume, the model is an opt-in rescue.
- **Specialized beats general on printed structured layouts.** Head-to-head
  evaluations (OmniDocBench CVPR 2025, arXiv 2412.07626; IP&M 2026) show
  pipeline parsers winning on tables/structured text while general-purpose
  VLMs run 22–30 s/page — the reason tier 3b is geometry rather than a model
  call, and the LLM stays rescue-only (E-8/D6: checksums police model output,
  not trust it).
- **The human-review contract is where accuracy claims get grounded.**
  Research caveats: most published accuracy figures are self-reported or
  measured on synthetic benchmarks, and leaderboards move fast. D3's
  `corrections`-as-eval-data and the per-corpus sweeps in
  `scripts/test_all.py` are the local answer — our numbers come from humans
  and our own documents, never from a vendor card. (Production datapoint in
  the same spirit: Grab's vision-LLM team fixed hallucination and P99 tail
  latency by fine-tuning on their own verified corpus — the same loop D3
  creates, at a scale this project deliberately does not reach.)

*Last updated: 2026-10-20.*