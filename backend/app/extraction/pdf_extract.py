"""PDF quote extraction pipeline.

Design (the algorithmic story, in three tiers):

1. PRIMARY - native text layer. If the embedder wrote a text layer, word-level
   geometry is available, so layout is reliable and no OCR is needed.
2. STRUCTURE - table detection via pdfplumber (lattice/stream). Tables are
   matched to a schema by column-header keywords, not fixed column positions,
   which is what makes it survive different supplier layouts.
3. HEURISTIC FILL - remaining fields come from label-anchored regex on the
   full text. Label dictionaries cover EN/DE variants ("Valid Until",
   "Gueltig bis", "Liefertermin") and are easy to extend.
3b. GEOMETRY FALLBACKS. Two word-geometry readers close the gaps the string
   tiers mangle (the four supplier offers of the Challenge-1 corpus exposed
   all of them): _band_items() rebuilds the item table from the header row's
   column x-positions (stream tables with no grid, merged header words like
   'EinzelpreisGesamtpreisSt-Kz', wrapped cells that text-strategy tables
   shred into phantom columns); _totals_from_words() reads the totals block
   as (label, rightmost money token on the same visual line) pairs - which
   handles stacked label/amount blocks ('Zwischensumme' ... '€1.467,61' on
   one visual row) and 'USt. ... auf X € ... tax €' rows where the base and
   the tax share the line. It wins over the label-anchored reader for
   subtotal/tax/total/shipping and only falls back to it.

Every field gets a confidence score:
  0.95  label-anchored match (quote no., vendor id, subtotal, total)
  0.9   table rows (schema-matched or data-inferred), VAT, delivery date
  0.8   addresses / commercial terms (multi-line block capture)
  0.85  vendor name (trailing parentheses stripped heuristically)
  0.7   partial column match
  0.6   inferred by arithmetic (sub + tax) or validity-as-needed-by fallback
  0.55  inferred unit price (line_total / qty)

Amount parsing is locale-tolerant: "1.480,50" (DE) vs "1,480.50" (EN) are
disambiguated by which separator appears last (that is the decimal one).
"""
import re
import json
from dataclasses import dataclass, asdict, field

import pdfplumber
import fitz  # PyMuPDF

# ---------------------------------------------------------------- constants
LABEL_PATTERNS = {
    "quote_number": [
        r"quot(?:e|ation)[\s\-]*(?:reference|no\.?|number)",
        r"(?:anbots?|angebots?)-?nr", r"quotation\s*no", r"ref(?:erence)?\.?\s*:?",
        r"\bangebot\b[\s:.]*",  # 'Angebot 4120' / 'Angebot A0492/23'
    ],
    "needed_by": [
        r"requested\s+delivery\s+date", r"liefertermin", r"needed\s*by",
        r"delivery\s+date", r"expected\s+delivery",
    ],
    "valid_until": [
        r"g(?:ü|u|ue)ltig\s*bis", r"valid\s*until", r"offer\s*expiry",
    ],
    "payment_terms": [r"payment\s+terms", r"zahlungsbeding"],
    "incoterms": [r"incoterms?\s*:?", r"delivery\s+terms", r"lieferbedingung"],
    "customer": [r"customer\s*:", r"kunde\s*:", r"buyer\s*reference\s*:?",
                 r"erstellt\s+f(?:ü|ue)r", r"created\s+for"],
    "subtotal": [r"sub\s*-?\s*total", r"net\s*-?\s*total", r"nettosumme",
                 r"zwischensumme", r"seitensumme", r"positionen\s+netto",
                 r"nettobetrag"],
    "tax": [r"vat\s*\(\s*[\d.]+\s*%\)", r"mwst", r"\bvat\b",
            r"umsatzsteuer", r"urheberrechtsabgabe",
            # USt. must not swallow 'USt.-ID:' (the VAT number label)
            r"\bust\.?(?!\s*-?\s*id)",
            r"\btax(?:es)?\b(?!\s*(?:id|no|ref|reg|number|ident))"],
    "total": [r"grand\s*total", r"total\s*payable", r"gesamtbetrag",
              r"total\s*amount", r"endsumme", r"gesamtsumme", r"endbetrag",
              r"rechnungsbetrag"],
    "shipping": [r"versandkosten", r"shipping\s+costs?", r"frachtkosten"],
    "vendor_name": [r"vendor\s*:", r"supplier\s*:", r"lieferant\s*:",
                    r"erstellt\s*von", r"ausgestellt\s*von"],
    "vendor_id": [r"vendor\s*id", r"supplier\s*id", r"lieferanten-?nr\.?",
                  r"ust\.?-?\s*id\s*(?:nr)?\.?", r"\buid\b",
                  r"vat\s*(?:id|nr|no)"],
    "customer": [r"customer\s*:", r"kunde\s*:", r"buyer\s*reference\s*:?",
                 r"erstellt\s+f(?:ü|ue)r", r"created\s+for"],
}

CUR_SYMS = {"€": "EUR", "EUR": "EUR", "£": "GBP", "GBP": "GBP",
            "$": "USD", "USD": "USD"}

# address blocks are multi-line, so single-line label capture can't reach the
# street/city lines; a block reader takes the label line's remainder plus the
# following lines and stops at the next labelled field or a blank line
ADDRESS_LABELS = {
    "bill_to": ["bill(?:ing)?\\s+to", "rechnungsadresse", "invoice address"],
    "ship_to": ["ship\\s+to", "deliver\\s+to", "lieferadresse"],
}

# block capture stops here: another labelled field, or footer noise
STOP_LABEL_RE = re.compile(
    r"^\s*(datum\b|date\b|quote|anbots?|angebot|quotation|valid\b|g(?:ü|u)ltig|"
    r"liefertermin|delivery\b|lead time|vendor|supplier|lieferant|customer\b|"
    r"buyer\s*ref|contact\b|payment|zahlungsbedingungen|incoterms|bank\b|vat\b|"
    r"bill(?:ing)?\s+to|ship\s+to|deliver\s+to|lieferadresse|rechnungsadresse)",
    re.I)


def _norm_company(s):
    """Loose company-text key for matching a VAT-block line against an email
    domain stem: umlauts folded, punctuation/space dropped, lowercased."""
    s = s.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]", "", s)


def _company_line(s):
    """Does this line name a company (suffix, proprietor marker 'Inh.')?"""
    return bool(re.search(
        r"\b(?:gmbh|gbr|ohg|inc|ltd|llc|e\.?\s?v|b\.?v|s\.?a\.?r?\.?l"
        r"|s\.?p\.?a|e\.?\s?u|kg)\b|\bkg\b|gmbh\s*&\s*co|\binh\.|\b&sco\b|"
        r"\bag\.?\b",
        s, re.I))


def _trim_vendor_segments(name):
    """German letterhead lines ride with a street/city tail ('Gärtner Gregg,
    Berger 7, 59394 Nordkirchen'): trailing address-like segments are cut
    while real company suffixes (', GmbH', ', Inc.') survive."""
    segs = [s.strip(" |·") for s in name.split(",")]
    kept = []
    for s in segs:
        if re.match(r"^\s*\d{4,6}\b", s) \
                or re.search(r"\b\d{1,4}[a-z]?\s*$", s, re.I) \
                or re.search(r"\b(?:str(?:a[sß]e)?\.?|weg|bogen|allee|gasse"
                             r"|platz)\b", s, re.I):
            break
        kept.append(s)
    return (", ".join(kept) or name.strip())[:80]


@dataclass
class LineItem:
    sku: str | None
    description: str
    qty: float | None
    unit_price: float | None
    line_total: float | None
    confidence: float
    # which tier read this line: "heuristic" (tiers 1-3) or "llm" (tier 4
    # rescue) — the review UI can then show which numbers a model produced
    source: str = "heuristic"
    # unit of measure as printed ('Stk.', 'qm', 'Lfm') — free-form extra from
    # the geometry band reader
    unit: str | None = None


@dataclass
class ExtractResult:
    fields: dict          # name -> {"value": ..., "confidence": float}
    items: list[LineItem]
    overall_confidence: float
    warnings: list[str] = field(default_factory=list)

    def to_json(self):
        d = asdict(self)
        d["items"] = [asdict(i) for i in self.items]
        return d


# ------------------------------------------------------------------ amounts
def parse_amount(s):
    """Locale-tolerant money string -> float, else None.

    Handles 1,480.50 (EN), 1.480,50 (DE), 1.480,50 EUR, trailing symbols.
    Heuristic: when both separators appear, the LAST one is the decimal.
    When only one appears: if exactly 2 digits after it -> decimal; else grouping.
    """
    if s is None:
        return None
    s = re.sub(r"[^\d.,\-]", "", str(s)).strip().strip(".,").replace(" ", "")
    if not s:
        return None
    comma, dot = "," in s, "." in s
    if comma and dot:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        other = "." if dec == "," else ","
        cleaned = s.replace(other, "").replace(dec, ".")
    elif comma:
        frac = len(s.rsplit(",", 1)[1])
        cleaned = s.replace(",", ".") if frac == 2 else s.replace(",", "")
    elif dot:
        frac = len(s.rsplit(".", 1)[1])
        cleaned = s if frac == 2 else s.replace(".", "")
    else:
        cleaned = s
    try:
        return float(cleaned)
    except ValueError:
        return None


_MONTHS = {
    "jan": 1, "feb": 2, "mär": 3, "mar": 3, "apr": 4, "mai": 5, "may": 5,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "okt": 10, "oct": 10,
    "nov": 11, "dez": 12, "dec": 12,
}


def parse_date(s):
    """DD.MM.YYYY / YYYY-MM-DD / DD.MM.YY / '20 März 2024' -> ISO 8601."""
    if not s:
        return None
    # month-name form ('Gültig bis: Mittwoch 20 März 2024'); a weekday word
    # may prefix the day, so search anywhere in the string. Runs BEFORE the
    # space-stripping below, which would glue '20März2024' together.
    m = re.search(r"(\d{1,2})\.?\s*([a-zäöü]{3,})\.?\s+(\d{4})", s.lower())
    if m:
        mo = _MONTHS.get(m[2][:3])
        if mo:
            return f"{m[3]}-{mo:02d}-{int(m[1]):02d}"
    s = s.replace(" ", "")
    m = re.match(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", s)
    if m:
        return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}"
    m = re.match(r"(\d{1,2})[-./](\d{1,2})[-./](\d{2,4})", s)
    if m:
        y = m[3]
        if len(y) <= 2:
            y = ("20" + y) if int(y) < 70 else ("19" + y)
        return f"{y}-{int(m[2]):02d}-{int(m[1]):02d}"
    return None


# ------------------------------------------------------------------ text tier
def read_page_texts(path):
    with fitz.open(path) as doc:
        return [p.get_text("text") for p in doc]


def _last_amount(s):
    """Money value in a labeled line = LAST numeric token that is not a
    percentage ('MwSt. 19%: 2.977,68' -> 2977.68, while 'VAT 19,00 %' on a
    line whose amount is elsewhere yields None, not 19 or 1900)."""
    if not s:
        return None
    cands = [m for m in re.finditer(r"\d[\d.,]*\d|\d", s)
             if not s[m.end():m.end() + 2].lstrip().startswith("%")]
    return parse_amount(cands[-1][0]) if cands else None


def find_after_label(text, patterns, ctx=200):
    """First pattern (priority order) that anchors -> the value text following
    the label: rest of that line, or the next line if the line ends at the label."""
    if not text:
        return None
    for pat in patterns:
        for m in re.finditer(pat + r"[\s.:—-]*", text, re.IGNORECASE):
            start = m.end()
            line = text[start:start + ctx].split("\n")[0].strip()
            if not line:  # value on the next line
                nxt = text[start:start + ctx].split("\n")
                line = nxt[1].strip() if len(nxt) > 1 else ""
            if line:
                return line.strip(" <>|")
    return None


# a single-line bill/ship-to is immediately followed by the item table, whose
# header row is not a labelled field; the block reader must stop before it
TABLE_JUNK_RE = re.compile(
    r"\b(item|description|bezeichnung|pos\.?|lines?\b|sku\b|qty\b|menge|"
    r"unit price|einzelpreis|gesamt|line total|part no|art\.-?\s*nr)\b", re.I)


def _looks_table_header(s):
    """Header words may arrive as separate text lines (PyMuPDF splits wide
    table rows at column gaps), so a single hit is enough on a continuation
    line; a false stop only truncates the block, it never corrupts data."""
    return bool(TABLE_JUNK_RE.search(s))


def _address_block(text, patterns, max_lines=3):
    """Multi-line capture for address blocks: the labelled line's remainder
    plus up to max_lines-1 following lines, stopping at a blank line, the next
    labelled field (STOP_LABEL_RE), or a table-header-looking line."""
    for pat in patterns:
        for m in re.finditer(pat + r"[\s.:—-]*", text, re.IGNORECASE):
            start = m.end()
            lines = text[start:start + 200].split("\n")
            first = lines[0].strip()
            if not first:  # value starts on the next line
                lines = lines[1:]
                first = lines[0].strip() if lines else ""
            if not first or STOP_LABEL_RE.match(first):
                continue
            parts = [first]
            for nxt in lines[1:max_lines]:
                nxt = nxt.strip()
                if not nxt or STOP_LABEL_RE.match(nxt) or _looks_table_header(nxt):
                    break
                parts.append(nxt)
            return " · ".join(parts)
    return None


def _extract_fields_text(text):
    """Tier 3: label-anchored header fields -> {name: (value, confidence)}."""
    if not text:
        return {}
    flat = "\n".join(text) if isinstance(text, list) else text
    f = {}

    # every labeled occurrence is tried, because prose can also use the word
    # 'Angebot' ('… unser Angebot für das angefragte Moosbild.') — the first
    # occurrence followed by a digit-bearing token wins
    for qp in LABEL_PATTERNS["quote_number"]:
        for lm in re.finditer(qp + r"[\s.:—-]*", flat, re.IGNORECASE):
            tail = flat[lm.end():lm.end() + 80].split("\n")
            cand = tail[0].strip() or (tail[1].strip() if len(tail) > 1 else "")
            cand = cand.strip(" <>|")
            m = re.search(r"[A-Z0-9][A-Z0-9/\-\.]{2,}", cand)
            if m and re.search(r"\d", m[0]):
                f["quote_number"] = {"value": m[0], "confidence": 0.95}
                break
        if "quote_number" in f:
            break

    # validity and delivery are semantically different dates: validity binds
    # the offer's lifespan, delivery is what the PR's needed-by actually wants.
    # Extracted separately so any fallback below is a visible decision, not a
    # silent conflation
    v = find_after_label(flat, LABEL_PATTERNS["valid_until"])
    d = parse_date(v)
    if d:
        f["valid_until"] = {"value": d, "confidence": 0.9}

    v = find_after_label(flat, LABEL_PATTERNS["needed_by"])
    d = parse_date(v)
    if d:
        f["needed_by"] = {"value": d, "confidence": 0.9}
    elif "valid_until" in f:
        # flagged marker -> warning in _validate; low confidence because the
        # document names no delivery date at all
        f["needed_by"] = dict(f["valid_until"], confidence=0.6)
        f["_needed_by_from_validity"] = {"value": True, "confidence": 1.0}

    v = find_after_label(flat, LABEL_PATTERNS["vendor_id"])
    if v:
        m = re.search(r"[A-Z0-9][A-Z0-9/\-\.]{2,}", v)
        if m:
            vid = m[0]
            # DE 198 570 491 style spaced VAT ids collapse; only when the
            # value has a 2-letter country prefix (an invoice number must
            # never lose its internal spaces)
            if re.fullmatch(r"[A-Z]{2}[\d /]{7,}", vid):
                vid = vid.replace(" ", "")
            f["vendor_id"] = {"value": vid, "confidence": 0.95}
    v = find_after_label(flat, LABEL_PATTERNS["vendor_name"])
    if v:
        name = _trim_vendor_segments(re.sub(r"\s*\(.*$", "", v).strip(" ()|"))
        if name:
            f["vendor_name"] = {"value": name, "confidence": 0.85}
        # 'Erstellt von: Joshua Martens' names the quote's AUTHOR, not the
        # vendor company — the company sits in the VAT-id block below. A
        # two-token person name behind an author label triggers the override.
        if re.search(r"erstellt\s*von|ausgestellt\s*von", flat, re.I) \
                and re.fullmatch(r"[A-ZÄÖÜ][\wäöüß]+\.?\s+[A-ZÄÖÜ][\wäöüß]+",
                                 name):
            f["_vendor_from_author"] = {"value": True, "confidence": 1.0}

    # real documents rarely label the vendor. Ladder (all German-offer shapes
    # seen in the challenge corpus); the VAT-block reader runs even over an
    # author-labeled value:
    if "vendor_name" not in f or f.get("_vendor_from_author"):
        m = re.search(r"ust\.?-?\s*id|umst(?:atz)?steuer-?\s*id|\buid\b|"
                      r"vat\s*(?:id|nr|no)", flat, re.I)
        if m:
            ctx = flat[max(0, m.start() - 300): m.end() + 60]
            # the contact email NEAREST the VAT line names the vendor's
            # domain; the first email in the window may belong to the customer
            emails = re.findall(r"[\w.\-]+@([\w.\-]+)", ctx)
            lines = [l.strip() for l in ctx.split("\n")
                     if l.strip() and "@" not in l
                     and not _looks_table_header(l)]
            pick = None
            if emails:
                stem = _norm_company(emails[-1].split(".")[0])
                for l in lines:
                    if stem and stem in _norm_company(l):
                        pick = l
                        break
            if pick is None:
                for l in reversed(lines):
                    if _company_line(l):
                        pick = l
                        break
            if pick:
                f["vendor_name"] = {
                    "value": _trim_vendor_segments(
                        re.sub(r"^inh\.\s*", "", pick, flags=re.I)),
                    "confidence": 0.7}
    if "vendor_name" not in f and isinstance(text, list) and text:
        # letterhead pipe line ('Dream in Green GmbH | Pleidelsheimer …')
        p1_lines = [l.strip() for l in text[0].split("\n")][:8]
        for line in p1_lines:
            if "|" in line and _company_line(line.split("|")[0]):
                f["vendor_name"] = {
                    "value": _trim_vendor_segments(line.split("|")[0]),
                    "confidence": 0.7}
                break
    # first-line fallback, but NOT when the opening block is an addressee
    # (company name followed by a street/city line)
    if "vendor_name" not in f and isinstance(text, list) and text:
        first = text[0].strip().split("\n")[0].strip()
        after = [l.strip() for l in text[0].split("\n")[1:4]]
        addrish = any(re.search(r"\d{2,}|\bstr\.|straße|strasse|bogen|weg\b|"
                                r"\d{4,5}\s", l, re.I) for l in after)
        if len(first) >= 4 and not STOP_LABEL_RE.match(first) and not addrish:
            f["vendor_name"] = {"value": first[:80], "confidence": 0.7}

    v = find_after_label(flat, LABEL_PATTERNS["subtotal"])
    a = _last_amount(v)
    if a is not None:
        f["subtotal"] = {"value": a, "confidence": 0.95}

    v = find_after_label(flat, LABEL_PATTERNS["tax"])
    a = _last_amount(v)
    if a is not None:
        f["tax"] = {"value": a, "confidence": 0.9}

    # customer/vendor line: keep raw text for the UI to confirm
    v = find_after_label(flat, LABEL_PATTERNS["customer"])
    if v:
        f["customer_text"] = {"value": v, "confidence": 0.8}

    # multi-line contact/address blocks
    for fld, pats in ADDRESS_LABELS.items():
        v = _address_block(flat, pats)
        if v:
            f[fld] = {"value": v, "confidence": 0.8}

    # commercial terms from the footer, cut at the '|' column separator
    for fld in ("payment_terms", "incoterms"):
        v = find_after_label(flat, LABEL_PATTERNS[fld])
        if v:
            v = v.split("|")[0].strip(" .;")
            if v:
                f[fld] = {"value": v, "confidence": 0.8}

    # total: labeled first, else arithmetic inference from subtotal + tax
    v = find_after_label(flat, LABEL_PATTERNS["total"])
    a = _last_amount(v)
    sub = f.get("subtotal", {}).get("value")
    tax = f.get("tax", {}).get("value")
    if a is not None and (sub is None or a >= sub):
        f["total"] = {"value": a, "confidence": 0.95}
    elif sub is not None:
        f["total"] = {"value": round(sub + (tax or 0), 2), "confidence": 0.6}
        f["_inferred_total"] = {"value": True, "confidence": 1.0}

    cur = re.search(r"(EUR|GBP|USD|€|£|\$)", flat)
    if cur:
        f["currency"] = {"value": CUR_SYMS.get(cur[0], cur[0]), "confidence": 0.9}
    return f


# ------------------------------------------------------------- table tier
TABLE_HDR = {
    "qty": r"qty|menge",
    "unit_price": r"unit\s*price|einzel(?:preis)?|price|\bunit\b\s*\(",
    "line_total": r"total|gesam",
    "sku": r"sku|art\.?-?\s*nr|part\s*no|artikel|\bcode\b",
    "description": r"description|bezeichnung|item|name",
}


# cells that are pure scaffolding, never description text: checked against
# whitespace-stripped text because stream tables split words mid-token
# ('Tota l Exclusive' -> 'totalexclusive')
_SCAFFOLD_RE = re.compile(
    r"netto|mwst|gesamtbetr|gesammt|zwischensumme|subtotal|total|"
    r"vat|tax|zahlungsbeding|incoterm|quantit|menge|qty", re.I)

# money tokens: a currency code may abut the amount ('USD3,500' -> no word
# boundary mid-token, so a digit/boundary lookahead instead of \b...\b)
_MONEY_TOKEN_RE = re.compile(
    r"""(?:USD|EUR|GBP|CHF|AUD|CAD|NZD|JPY|CNY|SEK|NOK|DKK|PLN|CZK|HUF|ZAR|INR)
        (?=$|[\d.,%$€£\-+\s])
      |(?:USD|EUR|GBP|CHF|AUD|CAD|NZD|JPY|CNY|SEK|NOK|DKK|PLN|CZK|HUF|ZAR|INR)\b
      |[\d.,]+[%$€£]? |[$€£]\s*\d+ |[$€£] |^N\$ |@\s*\S+""", re.X)


def _scaffold(t):
    return bool(_SCAFFOLD_RE.search(re.sub(r"\s+", "", str(t)).lower()))


def _descworthy(t):
    """An unclaimed cell is description text only when >= 3 letters survive
    stripping money tokens ('Pressure range 0-700 bar.', 'parts package)' and
    'bar.' qualify; 'USD3,500' / 'EUR120.00' / 'N$' / '(40 hrs @ $245)' /
    'Qty Un' stay out)."""
    if re.search(r"[$€£]\s*\d|\d\s*(?:USD|EUR|GBP|CHF|JPY|AUD|CAD|NZD|SEK|"
                 r"NOK|DKK)|\b(?:USD|EUR|GBP|CHF|JPY|AUD|CAD|NZD|SEK|NOK|DKK"
                 r"|PLN|CZK|HUF|ZAR|INR)\s*\d", str(t)):
        return False
    leftover = _MONEY_TOKEN_RE.sub(" ", str(t))
    leftover = re.sub(r"\d", " ", leftover)
    return bool(re.search(r"[A-Za-zÄÖÜäöüß]{3,}", leftover))


def _table_to_items(tables):
    """Schema-match pdfplumber tables by header keywords, not column position,
    returning per-table items in reading order without fold-back (the wrapped
    description merge happens once, globally, in extract()).

    Stream-table wrinkle (continuation pages have no lattice, so rows come from
    the text-strategy fallback): a wrapped cell's lines can split into
    neighbouring unclaimed columns ('Ersatzteilpaket (spare' | 'parts package)')
    or into their own numberless row above the row that carries the price trio.
    The first is re-joined in column order; the second is held back and
    prepended to the next numbered row (or folded upward when none follows).
    Guardrails: numbered rows keep the claimed description column as their
    anchor (rescued neighbour text only enriches it), only cells carrying real
    words qualify as neighbours, and totals/terms/header rows never leak into
    descriptions."""
    items = []
    for tb in tables:
        if not tb or len(tb[0]) < 3:
            continue
        hdr, idx = _map_header(tb[:4])
        data_start = (tb.index(hdr) + 1) if hdr is not None else 0
        body = tb[data_start:]
        if idx is None:
            idx = _idx_by_data(body)
        if not idx or ("description" not in idx and "sku" not in idx):
            continue  # not an item table
        pending: list[str] = []
        for row in body:
            if not row or all(v in (None, "") for v in row):
                continue
            def get(key):
                ci = idx.get(key)
                return row[ci] if ci is not None and ci < len(row) else None
            # real words in unclaimed neighbouring columns belong to the
            # wrapped description; money-bearing cells stay out
            pieces = []
            for ci, cell in enumerate(row):
                if ci in idx.values():
                    continue
                t = " ".join(str(cell or "").split())
                if t and _descworthy(t) and not _scaffold(t) \
                        and not TABLE_JUNK_RE.fullmatch(t):
                    pieces.append((ci, t))
            dci = idx.get("description")
            anchored = bool(
                dci is not None and dci < len(row)
                and str(row[dci] or "").strip())
            if anchored:
                pieces.append(
                    (dci, re.sub(r"\s+", " ", str(row[dci])).strip()))
            desc = " ".join(t for _, t in sorted(pieces))
            sku = (str(get("sku")).strip() if get("sku") is not None else "")
            sku = sku or None
            qty = parse_amount(get("qty"))
            unit = parse_amount(get("unit_price"))
            tot = parse_amount(get("line_total"))
            if not anchored:
                if not (desc and qty is None and unit is None and tot is None) \
                        or _scaffold(desc):
                    continue  # totals rows rescued from stray cells stay dropped
                pending.append(desc)  # wrapped-desc fragment: fold when numbered row turns up
                continue
            if not desc and not sku:
                continue
            conf = 0.9 if (qty is not None and unit is not None) or tot is not None else 0.7
            if qty is None and unit is None and tot is None:
                if _scaffold(desc) or sku or TABLE_JUNK_RE.search(desc):
                    continue  # scaffolding or a stray column-header row
                pending.append(desc)  # wrapped-desc fragment: fold when numbered row turns up
                continue
            if pending:
                desc = " ".join([*pending, desc])
                pending = []
                conf = min(conf, 0.85)  # reconstructed from split rows
            items.append(LineItem(sku=sku, description=desc, qty=qty,
                                  unit_price=unit, line_total=tot,
                                  confidence=conf))
        # fragments trailing the last numbered row belong to it: fold upward
        if items and pending:
            items[-1].description = (
                items[-1].description + " " + " ".join(pending)).strip()
            items[-1].confidence = min(items[-1].confidence, 0.85)
        if items:
            break  # first item table on the page wins; merging stray tables is a stretch goal
    return items


# sku/code tokens: letters AND digits, no spaces — hyphenated 'XXX-123' and
# bare 'NIT112' both qualify; integer or money columns hold no letters and
# description cells hold spaces, so neither qualifies
def _sku_token(v: str) -> bool:
    s = v.strip()
    return bool(re.fullmatch(r"[A-Za-z\d][A-Za-z\d\-/]{1,12}", s)
                and re.search(r"[A-Za-z]", s) and re.search(r"\d", s))


def _lift_codes(items, res):
    """Second chance for the sku column: when the Code column's header was
    mangled/merged, its cells land inside the description ('NIT112 Nitrile
    work gloves'). When the majority of sku-less items lead with a code-shaped
    token, lift the token back into the sku slot — deterministic, no LLM.
    The majority gate keeps a single item that merely begins with a part
    number ('3M tape') from rewriting the whole set."""
    cands = []
    for it in items:
        if not it.sku and it.description:
            head = it.description.split(" ", 1)[0]
            if _sku_token(head):
                cands.append((it, head))
    skuless = sum(1 for it in items if not it.sku)
    if skuless and len(cands) >= max(2, round(0.6 * skuless)):
        for it, head in cands:
            it.sku = head
            rest = it.description[len(head):].strip()
            it.description = rest or it.description
        res.warnings.append(
            f"sku lifted from description text on {len(cands)}/{len(items)} "
            f"items (Code column lost its header)")


def _fold_wrapped(items):
    """Wrapped description rows carry no numbers -> fold into previous item.
    Runs once over all pages so a continuation line at the top of page 2
    still finds its parent item from page 1."""
    merged = []
    for it in items:
        if not merged:
            merged.append(it)
            continue
        prev = merged[-1]
        has_numbers = any(v is not None for v in (it.qty, it.unit_price, it.line_total))
        if not has_numbers and it.description and not it.sku:
            prev.description = (prev.description + " " + it.description).strip()
            prev.confidence = min(prev.confidence, 0.85)
        else:
            merged.append(it)
    return merged


def _map_header(head_rows):
    """Try rows 0..3 as the header; require the price trio + some identity col."""
    for hrow in head_rows:
        idx = {}
        for ci, cell in enumerate(hrow):
            t = " ".join(str(cell or "").lower().split())
            for key, pat in KEY_RES:
                if re.search(pat, t):
                    idx.setdefault(key, ci)
                    break  # each cell claims at most one role
        if {"qty", "unit_price", "line_total"} <= idx.keys() \
                and {"sku", "description"} & idx.keys():
            return hrow, idx
    return None, None


KEY_RES = [
    ("line_total", r"total|gesam"),
    ("unit_price", r"price|einzel|\bunit\b"),
    ("qty", r"qty|menge"),
    ("sku", r"\bsku\b|art\b|art\.|part|artikel|lieferanten|\bcode\b"),
    ("description", r"description|bezeichnung|item|name|material"),
]


def _frac(rows, n, pred):
    out = []
    for ci in range(n):
        vals = [str(r[ci]) for r in rows if ci < len(r) and r[ci] not in (None, "")]
        out.append(sum(1 for v in vals if pred(v)) / len(vals) if vals else 0.0)
    return out


def _idx_by_data(rows):
    """Infer column roles from cell VALUES alone. Logic:
    - money columns = cells dominated by 2-decimal numbers; the rightmost is
      the line total, the one before it the unit price
    - sku = highest fraction of code-like tokens (XXX-123 or XXX123)
    - qty = rightmost integer-ish column left of the unit column
    - description = the mostly-text column (longest average content)
    """
    # only rows that carry at least one money-like cell: headers and wrapped
    # description fragments would otherwise dilute the fractions
    rows = [r for r in rows if r and any(
        v not in (None, "") and re.fullmatch(r"[\d .,]*\d{2}", str(v).strip())
        for v in r)]
    if not rows:
        return None
    n = max(len(r) for r in rows)
    money_f = _frac(rows, n, lambda v: bool(re.fullmatch(r"[\d .,]*\d{2}", v.strip())))
    # sku: code-like tokens (letters AND digits, no spaces) — hyphenated
    # 'XXX-123' and bare 'NIT112' both qualify; qty/money columns hold no
    # letters, description cells hold spaces, so they stay out
    sku_f = _frac(rows, n, _sku_token)
    int_f = _frac(rows, n, lambda v: bool(re.fullmatch(r"\d{1,5}", v.strip())))
    text_f = _frac(rows, n, lambda v: not re.fullmatch(r"[\d .,]*", v.strip()))
    avg_len = [sum(len(str(r[ci])) for r in rows if ci < len(r)) / max(1, len(rows))
               for ci in range(n)]

    idx = {}
    money_cols = [ci for ci in range(n) if money_f[ci] >= 0.6]
    if len(money_cols) >= 2:
        idx["line_total"] = money_cols[-1]
        idx["unit_price"] = money_cols[-2]
    elif money_cols:
        idx["line_total"] = money_cols[-1]

    sku_col = max(range(n), key=lambda c: sku_f[c], default=None)
    if sku_col is not None and sku_f[sku_col] >= 0.6:
        idx["sku"] = sku_col

    if "unit_price" in idx:
        qty_cands = [ci for ci in range(idx["unit_price"]) if int_f[ci] >= 0.7]
        if qty_cands:
            idx["qty"] = qty_cands[-1]

    desc_cands = [ci for ci in range(n) if ci not in idx.values() and text_f[ci] >= 0.8]
    if desc_cands:
        idx["description"] = max(desc_cands, key=lambda c: avg_len[c])
    if "description" not in idx and "sku" in idx:
        idx.pop("sku")  # a lone sku column with no text is not an item table
        return None
    return idx


# ------------------------------------------------------------ validation
def _validate(res: ExtractResult):
    """Cross-checks that either raise or lower trust, and last-resort inference
    of missing unit prices from line_total / qty (flagged, not silent)."""
    items, fdb = res.items, res.fields

    for i in items:
        if i.line_total is not None and i.qty and i.unit_price is None:
            inferred = round(i.line_total / i.qty, 2)
            i.unit_price = inferred
            i.confidence = min(i.confidence, 0.55)
            res.warnings.append(
                f"item '{i.sku or i.description}': unit price inferred "
                f"as {inferred} (line_total / qty)")
        # a priced row whose unit price × qty misses the line total is
        # discounted or corrected downstream — readable, never silent
        if i.qty and i.unit_price is not None and i.line_total is not None \
                and abs(i.qty * i.unit_price - i.line_total) > 0.06:
            res.warnings.append(
                f"item '{(i.sku or i.description)[:40]}': unit price × qty "
                f"({round(i.qty * i.unit_price, 2)}) ≠ line total "
                f"({i.line_total}) — discount or rounding?")

    # alternative positions ('Alternativ zu vorstehender Position', '(Alt.)')
    # are priced options the supplier excludes from its own sum — keep them as
    # lines, but out of the arithmetic, visibly
    alts = [i for i in items if _ALT_RE.search(i.description or "")]
    alt_ids = {id(i) for i in alts}
    core = [i for i in items if id(i) not in alt_ids]
    if alts:
        res.warnings.append(
            "alternative position(s) excluded from the sum check: "
            + "; ".join(i.description[:40] for i in alts[:3]))

    line_sum = round(sum((i.line_total or (i.qty or 0) * (i.unit_price or 0))
                         for i in core), 2)
    sub = fdb.get("subtotal", {}).get("value")
    tot = fdb.get("total", {}).get("value")
    tax = fdb.get("tax", {}).get("value")
    ship = fdb.get("shipping", {}).get("value")

    if items and sub is not None and abs(line_sum - sub) < 0.05:
        for i in res.items:
            if i.line_total is not None and id(i) not in alt_ids:
                i.confidence = max(i.confidence, 0.92)
    elif items and sub is not None:
        res.warnings.append(
            f"line-total sum {line_sum} does not match subtotal {sub}")
    elif items and tot is not None:
        # no subtotal on the document: the grand total is the sum check's
        # only anchor (German hosting bills print just "Total EUR x")
        if abs(line_sum - tot) < 0.05:
            for i in res.items:
                if i.line_total is not None and id(i) not in alt_ids:
                    i.confidence = max(i.confidence, 0.92)
        else:
            res.warnings.append(
                f"line-total sum {line_sum} does not match total {tot}")

    # grossing-up: subtotal + shipping + tax ≍ total (freight is its own net
    # position with its own USt line on German offers)
    if sub is not None and tax is not None and tot is not None:
        expect = round(sub + (ship or 0) + tax, 2)
        if abs(expect - tot) > 0.05:
            res.warnings.append(
                f"subtotal + shipping + tax ({expect}) != total ({tot})")
    if sub is None and tot is None:
        res.warnings.append("no monetary totals found")
    if not items:
        res.warnings.append("no line items detected")
    if "needed_by" not in fdb:
        res.warnings.append("needed-by date not found")
    if fdb.get("_needed_by_from_validity"):
        res.warnings.append(
            "needed-by falls back to the validity date (offer expiry) — "
            "the document names no delivery date, confirm with the buyer")


# ---------------------------------------------------- geometry tier (3b)
# Column roles the header row can name. Patterns anchor on the FIRST word
# characters (\b at the left edge only), because supplier headers merge words
# mid-token: 'EinzelpreisGesamtpreisSt-Kz' must yield BOTH a unit_price
# anchor at 'Einzelpreis' and a line_total anchor at 'Gesamtpreis'.
HDR_ROLE_RES = [
    ("pos", r"\bpos\.?"),
    ("sku", r"art\.?[\s.]*-?\s*nr\.?|artikel-?\s*nr\.?|\bartikel\b|\bcode\b"
            r"|\bsku\b|\bpart\b|\blf-?nr"),
    ("description", r"\bbezeichnung|\bbeschreibung|\bdescription|\bprodukt"
                    r"|\bitem\b|\bmaterial\b|\bdesignation"),
    ("qty", r"\bmenge|\bqty\b|\banzahl|\bme\b"),
    ("unit", r"\beinheit|\bunit\b|\bme\b"),
    # \b is omitted where supplier headers concatenate labels mid-word
    # ('EinzelpreisGesamtpreisSt-Kz' — no word boundary before 'Gesamtpreis'):
    # the overlap walk below prevents double-claims
    ("unit_price", r"einzelpreis|\bvk-?\s?preis|\bpreis|\bprice\b|\brate\b"),
    ("discount", r"\brabatt|\bzuschlag|\bdiscount"),
    ("line_total", r"gesamtpreis|\bgesamtpreis|\bgesamtpre\b|\bgesamt"
                   r"|\btotal\b|\bsumme\b"),
]

# a line inside the band that names a totals entry ends the item table - the
# check runs BEFORE the data-row test, because totals rows are data-shaped
# (label words + money) and would otherwise join the items. Freight printed
# as a real position ('Versandkosten' with its own qty/price) survives: it
# never carries the totals qualifiers (netto/USt/%)
_BAND_TOTALS_RE = re.compile(
    r"nettosumme|zwischensumme|seitensumme|endsumme|gesamtsumme|gesamtbetrag|"
    r"endbetrag|rechnungsbetrag|nettobetrag|positionen\s+(?:netto|ust|mwst)|"
    r"umsatzsteuer|\bust\.?\s|mwst|summe\s*$"
    # vendor footer block ('USt.-IdNr.: / Steuer-Nr.: IBAN: BIC: …') - a bank
    # column fragment ('62') otherwise reads as a priced data row
    r"|\bust\.?-?\s*id|\biban\b|\bbic\b|steuer-?\s?nr\b|amt?sgericht")
_BAND_FREIGHT_TOT_RE = re.compile(
    r"versandkosten|frachtkosten|shipping\s*cost")
_BAND_FREIGHT_QLF_RE = re.compile(r"netto|ust\.?\s|mwst|\d\s*%|\bgrosso\b")

_CARRY_RE = re.compile(r"^\s*übertrag\b", re.I)
_MONEY_PURE_RE = re.compile(r"^[-+€$£]?[\d.,]+[%€$£]?$")


def _word_lines(words):
    """Words -> ordered visual lines [(y_top, [word dicts sorted by x])]."""
    rows = {}
    for w in words:
        rows.setdefault(round(w["top"] / 3.0), []).append(w)
    return [(min(w["top"] for w in ws), sorted(rows[k], key=lambda w: w["x0"]))
            for k, ws in sorted(rows.items())]


def _header_anchors(line_words):
    """Map header words -> {role: x}. The header line's words are joined into
    one character string with each character carrying an interpolated x;
    role regexes then run over that string and a left-to-right walk keeps the
    first match per role, skipping overlaps (a 'preis' inside 'einzelpreis',
    a second 'gesamt' inside 'gesamtpreis') and enforcing column order."""
    chars, xs = [], []
    for i, w in enumerate(line_words):
        t = str(w["text"])
        if i:
            chars.append(" ")
            xs.append(w["x0"] - 1)
        n = len(t) or 1
        for j, ch in enumerate(t):
            chars.append(ch)
            xs.append(w["x0"] + (w["x1"] - w["x0"]) * j / n)
    s = "".join(chars)
    raw = []
    for role, pat in HDR_ROLE_RES:
        for m in re.finditer(pat, s, re.I):
            raw.append((m.start(), m.end(), role))
    raw.sort()
    anchors, last_end = {}, -1
    for a, b, role in raw:
        if a >= last_end and role not in anchors:
            anchors[role] = xs[a]
            last_end = b
    return anchors


def _col_role(cx, roles, xs):
    role = None
    for r, x in zip(roles, xs):
        if cx >= x - 4:
            role = r
    return role


_CUR_CODE_RE = re.compile(r"\b(?:eur|usd|gbp|chf)\b", re.I)


def _pick_money(toks):
    """Best money token in a column: prefer the first token with a decimal
    part (the total column may carry '€' + '1.438,00' + '€24' fragments from
    the merged St-Kz column, and '24' must never win)."""
    vals = []
    for t in toks:
        t2 = _CUR_CODE_RE.sub("", t.replace("€", "").replace("$", "")
                              .replace("£", "")).strip()
        if re.fullmatch(r"-?[\d .,]+%?", t2) and not t2.endswith("%"):
            vals.append(t2.replace(" ", ""))
    if not vals:
        return None
    dec = [v for v in vals if re.search(r"[.,]\d{2}$", v)] or vals
    return parse_amount(dec[0])


_ALT_RE = re.compile(r"\(alt\.?\)|^alternativ\w*\b|\balternativ\w*\s+zu\b", re.I)
_ALT_WORD_RE = re.compile(r"alternativ|\(alt", re.I)
# a line that OPENS an alternative block is the head of the next numbered
# row, never a continuation of the previous one
_ALT_HEAD_RE = re.compile(r"^\s*alternativ\w*\s+zu", re.I)


def _items_score(items):
    """Cheaper-beats-fancier arbitration between the stream-table reader and
    the band reader: priced items count double, items whose description is
    actually letterhead/footer/totals text count nothing. The penalty is
    comparative only - a document whose real items include freight rows
    loses the same points from BOTH readers, so it cannot flip the choice."""
    s = 0.0
    for it in items:
        bad = bool(re.search(
            r"amt?sgericht|hr-?\s?nr|iban|bic\b|steuer-?\s?nr|ust\.?-?\s?id"
            r"|[^@\n]@|tel\.|fax|geschäftsführung|e-mail|web:|^\s*\d{4,5}\s+\w"
            r"|\bust\b|mwst|versandk|positions?\s+netto|endsumm|zwischensumm"
            r"|\blieferbeding", it.description or "", re.I))
        priced = it.line_total is not None or (
            it.qty is not None and it.unit_price is not None)
        s += (2.0 if priced else 0.4) * (0.0 if bad else 1.0)
    return s


def _band_items(page):
    """Rebuild the item table from word geometry: the header row supplies the
    column x-anchors, every body word joins the column its center falls in,
    and numberless lines fold as wrapped description (pending until a data
    row arrives, appended to the open item otherwise). Carries the shapes the
    string tiers shred: no grid lines, merged header words, wrapped cells,
    'Übertrag' carry-forward rows, discount columns, price-only rows."""
    words = page.extract_words()
    if not words:
        return []
    lines = _word_lines(words)
    hdr_i = None
    for i, (_, ws) in enumerate(lines):
        t = " ".join(w["text"] for w in ws).lower()
        if sum(bool(re.search(p, t, re.I)) for _, p in HDR_ROLE_RES) >= 3:
            hdr_i = i
            break
    if hdr_i is None:
        return []
    anchors = _header_anchors(lines[hdr_i][1])
    # not an items table without a description or the money trio
    if "description" not in anchors and "line_total" not in anchors:
        return []
    order = sorted(anchors.items(), key=lambda kv: kv[1])
    roles = [r for r, _ in order]
    xs = [x for _, x in order]

    items, pending, closed = [], [], False
    # descriptions kept while 'closed': the carry row's trailing words and
    # wrapped lines between the carry row and the next numbered row
    pending_closed = []

    _CUR_TOK_RE = re.compile(r"[€$£](?!\d)|^(?:eur|usd|gbp|chf)$|^\s*$",
                             re.I)  # flags at compile time - compiled
    # .fullmatch(t, re.I) would pass re.I as the *pos* argument, silently
    # keeping lone currency tokens like '€'

    def append_text(cols, wtexts):
        # continuation line: keep its words; only drop bare currency tokens
        return [t for t in wtexts if not _CUR_TOK_RE.fullmatch(t)]

    for _, ws in lines[hdr_i + 1:]:
        line_text = " ".join(w["text"] for w in ws)
        low = line_text.lower()
        # totals block terminates the table BEFORE any data-row logic: totals
        # rows are data-shaped (label + money in the price/total columns) and
        # would otherwise join the items. Freight as a real position survives
        # (it carries no totals qualifier)
        if _CARRY_RE.match(low):
            # carry-forward row ('Übertrag 1.438,00 €'): the page's ORIGINAL
            # items are done, but page-2 table tops carry on in numbered rows
            # after it - so 'closed' only gates rows without a position
            # number. Words right of the carry word that are not the carried
            # money ('Übertrag Alternativ zu vorstehender Position 1.1 …')
            # stay pending for the next numbered row.
            closed = True
            rest = [w["text"] for w in ws[1:]]  # carry word itself left out
            rest = [t for t in append_text(None, rest)
                    if not _MONEY_PURE_RE.fullmatch(t)]
            if rest:
                pending_closed.append(" ".join(rest))
            continue
        if _BAND_TOTALS_RE.search(low) or (
                _BAND_FREIGHT_TOT_RE.search(low)
                and _BAND_FREIGHT_QLF_RE.search(low)):
            break
        cols = {r: [] for r in roles}
        for w in ws:
            r = _col_role((w["x0"] + w["x1"]) / 2, roles, xs)
            if r:
                cols[r].append(str(w["text"]))
        price = _pick_money(cols.get("unit_price", []))
        total = _pick_money(cols.get("line_total", []))
        qty = _pick_money(cols.get("qty", []))
        named = bool(cols.get("description") or cols.get("sku"))
        is_data = (price is not None or total is not None) and named
        if not named and price is None and total is None and qty is None:
            continue
        if is_data:
            # after a carry row only a genuinely numbered row reopens the
            # table ('1.1a 1,00 00009 Wie Pos. …'); footer/letterhead rows
            # with stray money in a column stay closed out
            pos_num = any(re.fullmatch(r"\d{1,3}(?:\.\d+)?[a-z]?", t)
                          for t in cols.get("pos", []))
            if closed:
                if not pos_num:
                    continue
                closed = False
                pending = pending_closed + pending
                pending_closed = []
        if not is_data:
            text = " ".join(append_text(cols, [w["text"] for w in ws]))
            if not text:
                continue
            if items and not closed and not _ALT_HEAD_RE.match(text):
                items[-1].description = (
                    items[-1].description + " " + text).strip()
            else:  # head before the first priced row / alt block / after carry
                frag = " ".join(t for t in text.split(" ")
                                if not re.fullmatch(r"\d{1,3}\.?", t))
                (pending_closed if closed else pending).append(frag.strip())
            continue
        desc = " ".join(cols.get("description", [])).strip()
        # a wrapped sku cell splits into fragments that end in a separator
        # ('sG-IN-OB-Logo-' / 'Acrylglas'); when every sku-column token is
        # code-shaped they re-join into the single code they were
        sku_toks = [t.strip() for t in cols.get("sku", []) if t.strip()]
        sku = None
        if sku_toks and all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-/]*", t)
                            for t in sku_toks) \
                and sum(len(t) for t in sku_toks) <= 24:
            sku = "".join(sku_toks) if len(sku_toks) > 1 else sku_toks[0]
        if pending:
            head = " ".join(pending)
            # a numbered-but-unpriced row repeating the item name must not
            # duplicate it ('Moosbild Mix-Moos 160x80 cm' twice): near-equal
            # prefixes collapse into one description
            n_pend, n_desc = _norm_company(head), _norm_company(desc)
            if not (n_desc and (n_desc in n_pend or n_pend in n_desc)):
                desc = (head + " " + desc).strip()
            pending = []
        unit = " ".join(cols.get("unit", [])).strip() or None
        item = LineItem(sku=str(sku).strip() if sku else None,
                        description=desc, qty=qty, unit_price=price,
                        line_total=total,
                        confidence=0.9 if (qty is not None
                                           and (price is not None
                                                or total is not None)) else 0.7)
        item.unit = unit  # free-form extra, kept on the item
        items.append(item)
    if items and pending and not closed:
        items[-1].description = (
            items[-1].description + " " + " ".join(pending)).strip()
    return items


# stacked/inline totals: (label, rightmost money token on the same visual
# line). Guard 'USt.-ID:' etc. from the tax role: the VAT-number line is
# identification, never a tax amount
_TOT_ROLES = [
    ("total", r"endsumme|gesamtsumme|gesamtbetrag|rechnungsbetrag|endbetrag|"
              r"grand\s*total|total\s*payable|amount\s*due|total\s*amount|"
              r"gesamts\w*\s*\(\s*\w+\s*\)"),
    ("subtotal", r"zwischensumme|seitensumme|nett(oseitensumme|nettobetrag)|"
                 r"\bnettosumme|positionen\s+netto|net\s*total|sub\s*-?\s*total"),
    ("tax", r"umsatzsteuer|urheberrechtsabgabe|mwst|\bust\.?[^\w-]|\bvat\b|"
            r"\btax\b"),
    ("shipping", r"versandkosten|shipping\s*(?:cost|fee|amount)|frachtkosten|"
                 r"delivery\s*cost"),
]
_TOT_NOISE_RE = re.compile(r"\bid\b|iban\b|bic\b|steuer-?\s*nr|hr-?\s*nr", re.I)
_RATE_RE = re.compile(r"(\d{1,2}(?:[.,]\d+)?)\s*%")


def _totals_from_words(pages_words):
    """Geometry totals reader: for every visual line carrying a totals label,
    the value is the rightmost money token ON THAT LINE (handles both the
    inline and the stacked label/amount layout, and 'USt. … auf X € … tax €'
    rows where the base precedes the tax). Multiple tax components (MwSt +
    Urheberrechtsabgabe, USt on goods + USt on freight) SUM into `tax`. Later
    subtotal/total lines win (carry-forward 'Seitensumme' then 'Positionen
    netto' print the same or a refined value)."""
    out = {}
    tax_parts = []
    seen_totals = False
    for words in pages_words:
        for y, ws in _word_lines(words):
            low = " ".join(w["text"] for w in ws).lower()
            if _TOT_NOISE_RE.search(low) or _CARRY_RE.match(low):
                continue
            role = None
            for r, pat in _TOT_ROLES:
                if re.search(pat, low, re.I):
                    role = r
                    break
            if role is None:
                continue
            if role == "tax" and not seen_totals:
                # tax components live in the totals block: a 'VAT:' line on
                # the letterhead ('… 85055 Ingolstadt, DE - VAT: DE987654321')
                # may otherwise donate its postal code as a tax component
                continue
            if role in ("subtotal", "total"):
                seen_totals = True
            vals = []
            for w in ws:
                t = str(w["text"])
                m = re.fullmatch(r"[-+€$£]?\d[\d .,]*\d|[-+€$£]?\d", t.strip())
                if not m:
                    continue
                # percent values are rates, not amounts ('MwSt. 19%: 2.977,68')
                if re.match(r"\d[\d .,]*%", t.strip()):
                    continue
                vals.append(parse_amount(t))
            if not vals:
                continue
            v = vals[-1]
            if role == "tax":
                tax_parts.append(v)
            else:
                # same value repeated across pages/sections: keep last
                out[role] = v
    if tax_parts:
        s = round(sum(tax_parts), 2)
        out["tax"] = s
        out["_tax_components"] = tax_parts
    return out


def _region_tables(page):
    """Fallback for grid-less (stream) tables whose whitespace alignment pdfplumber's
    global sweep mangles: detect the item-table header row and, if present, the
    totals row, and search for a table only inside that band. Restricting the
    canvas to the band is what keeps the title/letterhead text from turning into
    phantom columns."""
    if not (words := page.extract_words()):
        return []
    rows: dict[int, list] = {}
    for w in words:
        rows.setdefault(round(w["top"] / 3.0), []).append(w)
    ordered = sorted(rows.items())
    hdr_y = tot_y = None
    for _, wr in ordered:
        t = " ".join(w["text"] for w in wr).lower()
        if hdr_y is None \
                and sum(kw in t for kw in ["menge", "qty", "einzel", "price",
                                           "gesam", "total", "sku", "art-",
                                           "bezeichnung", "description", "part"]) >= 3:
            hdr_y = min(w["top"] for w in wr)
            continue
        if hdr_y is not None and re.search(
                r"nettosumme|net\s*total|sub\s*total|grand\s*total|"
                r"gesamtbetrag|total\s*payable", t):
            tot_y = min(w["top"] for w in wr)
            break
    if hdr_y is None:
        return []
    region = page.crop((0, hdr_y - 3, page.width, tot_y or page.height))
    return [t.extract() for t in region.find_tables(
        {"vertical_strategy": "text", "horizontal_strategy": "text",
         "min_words_vertical": 2, "min_words_horizontal": 1})]


def extract(path):
    """Full pipeline entrypoint. Header fields from the text layer; items from
    lattice tables across ALL pages (multi-page tables merge, so a table that
    continues on page 2 with a repeated header row is handled); if a page has
    no lattice table, the word-geometry band reader and the stream-table
    fallback compete on extraction quality and the better one wins. Totals
    come from the geometry totals reader (visual-line label/value pairs),
    with the label-anchored reader as fallback."""
    texts = read_page_texts(path)
    fields = _extract_fields_text(texts)
    pages_words = []
    lattice_pages, band_pages, region_pages = [], [], []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages_words.append(page.extract_words())
            lattice_pages.append(
                _table_to_items([t.extract() for t in page.find_tables()]))
            band_pages.append(_band_items(page))
            region_pages.append(_table_to_items(_region_tables(page)))

    # method arbitration is document-wide: a quote's items may span pages, so
    # the band and stream readers must never win on different pages of the
    # same document (page-2 totals rows would otherwise join page-1 items)
    use_band = False
    if any(not lat for lat in lattice_pages):
        def doc_items(pagelist):
            return [i for pg in pagelist for i in pg]

        sub = fields.get("subtotal", {}).get("value")

        def score(pagelist):
            its = doc_items(pagelist)
            s = _items_score(its)
            if sub is not None and its:
                core = sum((i.line_total or 0) for i in its
                           if not _ALT_RE.search(i.description or ""))
                if abs(round(core, 2) - sub) < 0.05:
                    s += 10.0  # the doc's own arithmetic endorses these rows
            return s

        use_band = score(band_pages) > score(region_pages)
        if use_band:
            all_items = [lat if lat else bnd
                         for lat, bnd in zip(lattice_pages, band_pages)]
        else:
            all_items = [lat if lat else reg
                         for lat, reg in zip(lattice_pages, region_pages)]
    else:
        all_items = lattice_pages
    items = _fold_wrapped([i for pg in all_items for i in pg])

    # totals: the geometry reader wins (visual-line anchoring corrects the
    # label reader's base-vs-tax confusion on 'USt. … auf X €' rows and reads
    # stacked label/amount blocks); label values only keep the field alive
    # when no labelled totals line exists at all
    geo = _totals_from_words(pages_words)
    for key, val in geo.items():
        if key.startswith("_"):
            continue
        fields[key] = {"value": val,
                       "confidence": 0.9 if key in ("tax", "shipping") else 0.95}
    if geo.get("_tax_components") and len(geo["_tax_components"]) > 1:
        fields["_tax_components"] = {
            "value": geo["_tax_components"], "confidence": 0.9}

    res = ExtractResult(fields=fields, items=items,
                        overall_confidence=0.0, warnings=[])
    _lift_codes(res.items, res)
    _validate(res)
    # tier 4 (LLM rescue): a no-op unless LLM_API_KEY is set; it only fires
    # when the heuristics came back weak, and re-runs _validate so the
    # Σ(line totals) ≍ total checksums police model output too
    from .llm_tier import rescue
    rescue(texts, res)

    confs = [v["confidence"] for k, v in fields.items() if not k.startswith("_")] \
        + [i.confidence for i in items]
    res.overall_confidence = round(sum(confs) / len(confs), 2) if confs else 0.0
    return res


if __name__ == "__main__":
    import sys
    r = extract(sys.argv[1])
    print(json.dumps(r.to_json(), indent=2, ensure_ascii=False))