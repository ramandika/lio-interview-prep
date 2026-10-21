"""LLM fallback tier (tier 4): rescue weak heuristic extractions.

Heuristics fail on *vocabulary*, not on understanding: a German invoice that
prints "Beschreibung" / "VK-Preis" / "Zeilenbetrag" defeats keyword schemas
but no human. This tier sends the PDF's raw text layer to an LLM and merges
the answer into the heuristic result — heuristic fields are kept where they
carry confidence, LLM items are tagged `source="llm"` so downstream review
knows which numbers were read by a model, and `_validate` re-runs so the
Σ(line totals) ≍ total checksum polices LLM output exactly as it polices the
heuristics.

Deliberately stdlib (urllib, no SDK): the tier is off unless LLM_API_KEY is
set, so offline corpus sweeps (`test_all.py`, `bench.py`) never call out.
The endpoint is Anthropic-compatible (x-api-key + /v1/messages), which also
means a local gateway works unchanged.
"""
import json
import os
import re
import urllib.request

from .pdf_extract import ExtractResult, LineItem, _validate

LLM_FIELDS = [
    "quote_number", "invoice_number", "vendor_name", "customer_name",
    "vendor_id", "customer_id", "currency", "subtotal", "tax", "total",
    "payment_terms", "valid_until", "needed_by",
]
LLM_CONF = 0.75  # model-read fields sit below keyword anchors (0.9-0.95)

_SYSTEM = (
    "You extract structured data from supplier invoices and quotes. "
    "The document text arrives as a raw text-layer dump: table columns can "
    "become separate lines, description cells may be split across several "
    "consecutive lines, and the totals row may sit after the items. "
    "Reassemble items correctly: a row's price tokens belong to the line "
    "nearest above them, and wrapped description lines fold into that same "
    "item. Never invent values; use null when a field is not on the "
    "document. Answer with ONE JSON object and nothing else."
)

_PROMPT = """Extract this document.

Return exactly this JSON shape (omit keys whose value is null):
{{"fields": {{
  "quote_number": text or null, "invoice_number": text or null,
  "vendor_name": the company that ISSUED this document (from the letterhead,
    not the customer address block),
  "customer_name": text or null, "vendor_id": text or null,
  "customer_id": text or null, "currency": 3-letter code or null,
  "subtotal": number or null, "tax": number or null, "total": number or null,
  "payment_terms": text or null,
  "valid_until": ISO date or null, "needed_by": ISO date or null}},
 "items": [{{"sku": text or null, "description": text,
             "qty": number or null, "unit_price": number or null,
             "line_total": number or null}}]}}

Document text:
{pages}"""


def available():
    """Armed only when the operator turns the tier on: LLM_TIER=on plus an
    Anthropic-compatible endpoint. Corpus sweeps (test_all.py, bench.py) run
    offline unless they explicitly arm it, so batch numbers stay comparable."""
    return (os.environ.get("LLM_TIER") == "on"
            and bool(os.environ.get("LLM_API_KEY"))
            and bool(os.environ.get("LLM_BASE_URL")))


def _call_llm(pages_text: str) -> str:
    """One Anthropic-compatible /v1/messages round-trip -> plain text reply."""
    url = os.environ["LLM_BASE_URL"].rstrip("/") + "/v1/messages"
    body = {
        "model": os.environ.get("LLM_MODEL", "claude-haiku-4-5"),
        "max_tokens": 32000,  # 5-page invoices run to 70+ items: the answer
        # must fit whole or the JSON dies mid-array (stop_reason=max_tokens)
        "system": _SYSTEM,
        "messages": [{"role": "user",
                      "content": _PROMPT.format(pages=pages_text[:24000])}],
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 method="POST", headers={
        "x-api-key": os.environ["LLM_API_KEY"],
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    })
    with urllib.request.urlopen(req, timeout=180) as r:
        reply = json.load(r)
    # content blocks: reasoning gateways emit {"type":"thinking"} first;
    # keep only text blocks (thinking carries no answer)
    text = "".join(b.get("text", "") for b in reply.get("content", [])
                   if b.get("type") == "text")
    if reply.get("stop_reason") == "max_tokens":
        raise ValueError(
            "answer truncated at max_tokens=%s" % body["max_tokens"])
    return text


def _parse_json(reply: str) -> dict:
    m = re.search(r"\{.*\}", reply, re.S)  # tolerate prose / code fences
    return json.loads(m[0]) if m else {}


def _num(v):
    """Accept '5,39' (DE) / 5.39 / 'EUR 34,73' -> float."""
    if v is None or isinstance(v, (int, float)):
        return v
    s = re.sub(r"[^\d.,\-]", "", str(v)).replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _weak(res: ExtractResult) -> bool:
    """Same definition of 'weak' the /extract endpoint uses for `partial`:
    no items, no totals read at all, or a total that was only INFERRED
    (subtotal + tax) instead of read from the document."""
    return (not res.items
            or "no monetary totals found" in res.warnings
            or "_inferred_total" in res.fields)


def _sku_starved(res: ExtractResult) -> bool:
    """Every item is present yet not one carries a sku: the document prints a
    Code column somewhere tier 2 could not claim and the description lift
    (pdf_extract._lift_codes) could not reclaim. Most documents have no code
    column at all — those return skus of null from a model that was told to
    use null when absent, so the sweep pays at most one call per doc while
    armed, and a sku-less answer changes nothing."""
    return bool(res.items) and not any(i.sku for i in res.items)


def rescue(texts: list[str], res: ExtractResult) -> ExtractResult:
    """Merge LLM output into a weak heuristic result. Never raises: on any
    failure the heuristic result is returned with an honest warning — a bad
    LLM answer must not make an already-partial row invisible."""
    if not available():
        return res
    weak, skuonly = _weak(res), _sku_starved(res)
    if not (weak or skuonly):
        return res
    solved = {"no line items detected", "no monetary totals found"}
    try:
        parsed = _parse_json(_call_llm("\n\n".join(texts)))
    except Exception as e:  # network/model/JSON: keep the heuristic result
        res.warnings.append(f"LLM fallback failed: {e.__class__.__name__}")
        return res
    if not isinstance(parsed.get("fields"), dict):
        res.warnings.append("LLM fallback returned no usable fields")
        return res

    # fields the heuristics already read with conviction are never overwritten
    for name in LLM_FIELDS:
        e = parsed["fields"].get(name)
        v = e.get("value") if isinstance(e, dict) else e
        if v in (None, "", "null"):
            continue
        if name in ("subtotal", "tax", "total"):
            v = _num(v)
        if v in (None, ""):
            continue
        have = res.fields.get(name)
        if (not have or have.get("value") in (None, "")
                or have.get("confidence", 1.0) < LLM_CONF):
            res.fields[name] = {"value": v, "confidence": LLM_CONF,
                                "source": "llm"}

    # a model's convenient habit: echoing `total` into the empty `subtotal`
    # slot. The checksum would then "pass" against evidence the model itself
    # invented — so when a model-read subtotal equals the total and no tax
    # sits between them, keep only the total the document actually printed
    sv = res.fields.get("subtotal", {})
    tv = res.fields.get("total", {})
    if sv.get("source") == "llm" and sv.get("value") == tv.get("value") \
            and res.fields.get("tax", {}).get("value") is None:
        res.fields.pop("subtotal")

    # an inferred total (subtotal + tax) is arithmetic, not evidence: when the
    # model reports a total of its own — it is told to return null rather than
    # invent, so any total it returns is a document read — the inferred flag
    # is cleared and /extract stops degrading the row to `partial`
    if res.fields.get("_inferred_total") and \
            res.fields.get("total", {}).get("source") == "llm" and \
            res.fields["total"].get("value") is not None:
        res.fields.pop("_inferred_total")

    backfilled = 0
    if not res.items and isinstance(parsed.get("items"), list):
        res.items = [
            LineItem(
                sku=(str(it.get("sku")) if it.get("sku") else None),
                description=" ".join(str(it.get("description") or "").split()),
                qty=_num(it.get("qty")), unit_price=_num(it.get("unit_price")),
                line_total=_num(it.get("line_total")), confidence=LLM_CONF,
                source="llm")
            for it in parsed["items"]
            if isinstance(it, dict) and (
                it.get("description") or it.get("line_total") is not None)]

    # heuristics found item rows but left the sku null (a `Code` column
    # outside the header dictionary): per-row sku backfill from the model
    # answer, matched on the numbers both sides read — line text stays
    # heuristic, and only the missing slot is filled
    elif res.items and isinstance(parsed.get("items"), list) \
            and any(not i.sku for i in res.items):
        llm_items = [it for it in parsed["items"] if isinstance(it, dict)
                     and it.get("sku")]
        used = set()
        for i in res.items:
            if i.sku:
                continue
            for k, it in enumerate(llm_items):
                if k in used:
                    continue
                lq, lt = _num(it.get("qty")), _num(it.get("line_total"))
                if lq == i.qty and lt is not None \
                        and i.line_total is not None \
                        and abs(lt - i.line_total) < 0.01:
                    i.sku = str(it["sku"]).strip() or None
                    if i.sku:
                        i.confidence = max(i.confidence, LLM_CONF)
                        used.add(k)
                    break
        backfilled = len(used)

    res.warnings = [w for w in res.warnings if w not in solved]
    if weak:
        res.fields["_llm_rescue"] = {"value": True, "confidence": 1.0}
        res.warnings.append(
            "weak heuristic result rescued by the LLM tier — verify item "
            "lines and totals before approving")
    elif backfilled:
        res.fields["_llm_rescue"] = {"value": True, "confidence": 1.0}
        res.warnings.append(
            "sku slots backfilled by the LLM tier (items are heuristic) — "
            "verify item codes before approving")
    elif skuonly:
        res.warnings.append(
            "LLM tier consulted for item codes — the document names none")
    _validate(res)  # the checksums police model output too
    res.warnings = list(dict.fromkeys(res.warnings))  # re-run may re-append
    return res