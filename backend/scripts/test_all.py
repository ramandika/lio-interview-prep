"""Exhaustive test sweep over every corpus in SAMPLES_DIR.

Three layers of assertion, cheapest first:
  1. SURVIVAL   - extract() returns without crashing (robustness)
  2. COVERAGE   - did we find items / subtotal / total at all (recall)
  3. GROUND TRUTH - generated-invoices only: <sample>.pdf pairs a same-named
     JSON with nested English keys (invoice.number / seller.name /
     totals.{subtotal,tax,due} / items[{description,qty,unit_price,line_total}])
     consistent across all 7 document languages.
     (invoice-benchmark ground truth lives in its own repo - score it with
      bench.py + scorer.evaluate, see README "External benchmark".)

Usage:
  python scripts/test_all.py [samples_dir] [out_summary.json] [corpus-substr]
"""
import json
import re
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.extraction import pdf_extract  # noqa: E402


def lang_bucket(title: str) -> str:
    if any("一" <= c <= "鿿" for c in title):
        return "zh/han"
    if any("؀" <= c <= "ۿ" for c in title):
        return "ar"
    return "latin"


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def sweep(pdf: Path) -> dict:
    try:
        r = pdf_extract.extract(str(pdf))
        f = {k: v["value"] for k, v in r.fields.items() if not k.startswith("_")}
        return {"ok": True, "n_items": len(r.items),
                "has_subtotal": f.get("subtotal") is not None,
                "has_total": f.get("total") is not None,
                "vendor_name": f.get("vendor_name"),
                "quote_number": f.get("quote_number"),
                "subtotal": f.get("subtotal"), "tax": f.get("tax"),
                "total": f.get("total"), "n_warnings": len(r.warnings),
                "items": [{"d": i.description, "a": i.line_total}
                          for i in r.items]}
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {e}"[:120]}


# --- crafted edge-case expectations for the demo quotes ---------------------
# quote_meridian.pdf is the flagship edge case: a 2-page quote whose item
# table continues on page 2 with a repeated header row, and whose totals
# (Nettosumme / Gesamtbetrag) live on the last page. Assertions: cross-page
# accumulation keeps all items complete and the cross-page checksum passes.
# quote_sudhaus.pdf exercises wrapped-description fragments inside stream
# tables ('Pressure range 0-700 bar.' must not be lost).
EDGE_EXPECTS = {
    "quote_meridian.pdf": {
        "n_items": 6,
        "subtotal": 6906.20, "tax": 1312.18, "total": 8218.38,
        "desc_contains": ["parts package)", "Dokumentationspaket DE/EN, printed + digital"],
    },
    "quote_sudhaus.pdf": {
        "n_items": 2,
        "desc_contains": ["Pressure range 0-700 bar."],
    },
}


def demo_edge_checks(agg: dict) -> None:
    print("\n## demo-quote edge expectations")
    for fname, exp in EDGE_EXPECTS.items():
        _expect_check(agg, fname, exp, "demo")


# --- real-offer expectations (challenge1 corpus) ----------------------------
# Four real supplier quotes (the interview corpus, docs/Challenge 1). These
# are the layouts that drove the word-geometry tier: merged/no-grid headers
# (GgG), stacked label/amount totals (Apple), carry-forward rows (Übertrag),
# alternative positions excluded from the supplier's own sum. Vendor nulls
# stay honest: AN-OF prints no VAT id and no company-suffix letterhead line.
CH1_EXPECTS = {
    "AngebotA0492_23.Pdf": {
        "n_items": 4, "subtotal": 1758.00, "tax": 334.02, "total": 2092.02,
        "quote_number": "A0492/23", "vendor_name": "Gärtner Gregg",
        "desc_contains": ["Alternativ zu vorstehender Position"],
    },
    "AN-4120-Kdnr-14918.pdf": {
        "n_items": 3, "subtotal": 1337.26, "tax": 294.93, "total": 1847.19,
        "quote_number": "4120", "vendor_name": "Dream in Green GmbH",
        "desc_contains": []},
    "AN-OF2312380-Kdnr-57692.pdf": {
        "n_items": 3, "subtotal": 1186.14, "tax": 247.00, "total": 1546.99,
        "quote_number": "OF2312380",
        "desc_contains": ["styleGREEN INDIVIDUAL"],
    },
    "Quote_1__Lio_Technologies_GmbH__1x_MBA___2212618452.pdf": {
        "n_items": 1, "subtotal": 1467.61, "tax": 291.40, "total": 1759.01,
        "quote_number": "2212618452", "vendor_name": "Apple Business Team",
        "desc_contains": ["MacBook Air"],
    },
}


def ch1_checks(agg: dict) -> None:
    print("\n## challenge-1 offer expectations (4 real supplier quotes)")
    for fname, exp in CH1_EXPECTS.items():
        _expect_check(agg, fname, exp, "ch1")


def _expect_check(agg: dict, fname: str, exp: dict, tag: str) -> None:
    s = agg.get(fname)
    if not s or not s.get("ok"):
        print(f"  FAIL {fname}: extraction failed")
        return
    got_items = [i["d"] for i in (s.get("items") or [])]
    checks = [(f"n_items=={exp['n_items']}", s["n_items"] == exp["n_items"])]
    if tag == "demo":  # our own PDFs must extract warning-free; real supplier
        checks.append((  # quotes legitimately carry alt/fallback warnings
            "warnings==0", s.get("n_warnings") == 0))
    for k in ("subtotal", "tax", "total"):
        if k in exp:
            checks.append((f"{k}=={exp[k]}", _close(s.get(k), exp[k])))
    if "quote_number" in exp:
        checks.append((f"quote=={exp['quote_number']}",
                       s.get("quote_number") == exp["quote_number"]))
    if "vendor_name" in exp:
        checks.append((f"vendor=={exp['vendor_name'][:24]}",
                       _name_match(s.get("vendor_name"),
                                   exp.get("vendor_name"))))
    for needle in exp.get("desc_contains", ()):
        checks.append((f"desc has {needle[:28]!r}",
                       any(needle in d for d in got_items)))
    verdict = "PASS" if all(ok for _, ok in checks) else "FAIL"
    print(f"  {verdict} ({tag}) {fname}: " + "; ".join(
        f"{name}={'y' if ok else 'n'}" for name, ok in checks))


# --- the generated-invoices ground-truth scorer ----------------------------

def _close(a, b):
    return a is not None and b is not None and abs(a - b) < 0.05


def _name_match(got, exp):
    if not exp:
        return None
    g = " ".join(re.findall(r"[a-z0-9]+", str(got or "").lower()))
    e = " ".join(re.findall(r"[a-z0-9]+", str(exp).lower()))
    if not g or not e:
        return False  # non-latin names normalise to "" -> never a match
    if g == e:
        return True
    short, long_ = sorted((g, e), key=len)
    if short in long_ and len(short) >= 0.6 * len(long_):
        return True
    return SequenceMatcher(None, g, e).ratio() >= 0.85


def _norm_no(s):
    return re.sub(r"[^0-9a-z]", "", str(s or "").lower())


def _greedy_match(got, exp):
    """greedy amount(±0.05)+description(≥0.6) match -> matched count."""
    used, m = set(), 0
    for e in exp:
        best, best_sim = None, 0.6
        for i, g in enumerate(got):
            if i in used or not _close(g["a"], e["a"]):
                continue
            sim = SequenceMatcher(None, g["d"].lower(), e["d"].lower()).ratio()
            if sim >= best_sim:
                best, best_sim = i, sim
        if best is None:  # amount-only match still counts
            for i, g in enumerate(got):
                if i not in used and _close(g["a"], e["a"]):
                    best = i
                    break
        if best is not None:
            used.add(best)
            m += 1
    return m


def gt_score(samples: Path, agg: dict) -> None:
    for pdf in sorted(samples.glob("*.pdf")):
        s = agg.get(pdf.name, sweep(pdf))
        gt_file = pdf.with_suffix(".json")
        if not s.get("ok") or not gt_file.exists():
            s["gt"] = None
            continue
        try:
            gt = json.loads(gt_file.read_text())
        except Exception:
            s["gt"] = None
            continue
        data = gt.get("data", {})
        if not isinstance(data, dict):
            s["gt"] = None
            continue
        inv = data.get("invoice") if isinstance(data.get("invoice"), dict) else {}
        tot = data.get("totals") if isinstance(data.get("totals"), dict) else {}
        seller = data.get("seller") if isinstance(data.get("seller"), dict) else {}
        exp_items = [{"d": str(i.get("description", "")),
                      "a": num(i.get("line_total"))}
                     for i in (data.get("items") or [])]
        m = _greedy_match(s.get("items") or [], exp_items)
        p, e = s.get("n_items") or 0, len(exp_items)
        s["gt"] = {
            "lang": lang_bucket(gt.get("template", {}).get("title", "")),
            "vendor": _name_match(s.get("vendor_name"), seller.get("name")),
            "number": (None if not inv.get("number") else
                       _norm_no(s.get("quote_number")) ==
                       _norm_no(inv.get("number"))),
            "subtotal": _close(s.get("subtotal"), num(tot.get("subtotal"))),
            "tax": _close(s.get("tax"), num(tot.get("tax"))),
            "total": _close(s.get("total"), num(tot.get("due"))),
            "items_f1": (1.0 if not p and not e else
                         0.0 if m == 0 else
                         2 * m / max(p + e, 1)),
        }


# ---------------------------------------------------------------------------

def main(samples_dir: str, out_path: str | None, only: str | None):
    root = Path(samples_dir)
    corpora = {
        "ours (demo quotes)": sorted(root.glob("*.pdf")),
        "challenge1 (real offers)": sorted(
            list((root / "challenge1-offers").glob("*.pdf"))
            + list((root / "challenge1-offers").glob("*.Pdf"))),
        "invoice-benchmark": sorted((root / "invoice-benchmark").glob("*.pdf")),
        "camelot (table edge cases)": sorted((root / "camelot").glob("*.pdf")),
        "invoice2data (real vendors)": sorted((root / "invoice2data").glob("*.pdf")),
        "generated-invoices (7 lang)": sorted((root / "generated-invoices").glob("*.pdf")),
    }
    if only:
        corpora = {k: v for k, v in corpora.items() if only in k}
    summary = {}
    for name, files in corpora.items():
        agg = {}
        for pdf in files:
            agg[pdf.name] = sweep(pdf)
        if name.startswith("generated-invoices"):
            gt_score(root / "generated-invoices", agg)
        if "ours" in name:
            demo_edge_checks(agg)
        if name.startswith("challenge1"):
            ch1_checks(agg)
        summary[name] = agg
        n = len(agg)
        ok = sum(1 for s in agg.values() if s.get("ok"))
        items = sum(1 for s in agg.values() if s.get("ok") and s["n_items"] > 0)
        sub = sum(1 for s in agg.values() if s.get("ok") and s["has_subtotal"])
        tot = sum(1 for s in agg.values() if s.get("ok") and s["has_total"])
        print(f"\n## {name}  ({n} pdf)")
        print(f"  extract ok      {ok}/{n} ({ok / max(n, 1) * 100:.0f}%)")
        print(f"  items detected  {items}/{n} ({items / max(n, 1) * 100:.0f}%)")
        print(f"  subtotal found  {sub}/{n} ({sub / max(n, 1) * 100:.0f}%)")
        print(f"  total found     {tot}/{n} ({tot / max(n, 1) * 100:.0f}%)")
        crashes = [k for k, s in agg.items() if not s.get("ok")]
        if crashes:
            print(f"  CRASHES ({len(crashes)}):", ", ".join(crashes[:8]))
        gts = [s["gt"] for s in agg.values() if s.get("gt")]
        if gts:
            def pct(field):
                v = sum(1 for g in gts if g[field] is True)
                return f"{v}/{len(gts)} ({v / len(gts) * 100:.0f}%)"
            f1s = [g["items_f1"] for g in gts]
            print(f"  ground truth    vendor {_name_pct(gts)}, "
                  f"number {pct('number')}, subtotal {pct('subtotal')}, "
                  f"tax {pct('tax')}, total {pct('total')}")
            print(f"  line-item mean F1 {sum(f1s) / len(f1s):.2f}")
            by_lang = defaultdict(list)
            for g in gts:
                by_lang[g["lang"]].append(g)
            for lang, gs in sorted(by_lang.items()):
                def p2(field):
                    v = sum(1 for g in gs if g[field] is True)
                    return f"{v}/{len(gs)} ({v / len(gs) * 100:.0f}%)"
                print(f"    [{lang:7s}] vendor {_name_pct(gs)}, number {p2('number')}, "
                      f"sub {p2('subtotal')}, tax {p2('tax')}, total {p2('total')}, "
                      f"F1 {sum(g['items_f1'] for g in gs) / len(gs):.2f}")

    if out_path:
        Path(out_path).write_text(json.dumps(summary, ensure_ascii=False, default=str))
        print(f"\nfull per-file detail -> {out_path}")


def _name_pct(gts):
    v = sum(1 for g in gts if g["vendor"] is True)
    sk = sum(1 for g in gts if g["vendor"] is None)
    return f"{v}/{len(gts)} ({v / len(gts) * 100:.0f}%)" + (f", n/a {sk}" if sk else "")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/data/samples",
         sys.argv[2] if len(sys.argv) > 2 else None,
         sys.argv[3] if len(sys.argv) > 3 else None)