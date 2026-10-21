"""Per-document failure table from the DrewKraken benchmark baseline run.

Usage:
  python3 failure_table.py /tmp/bench-repo /tmp/predictions.json /tmp/zar3/bench-failures.md

Scores every document with the benchmark's own scorer, keeps only docs that
are NOT fully correct, sorts by score, and emits a markdown triage table with
category / difficulty / failed fields, so heuristic fixes can be grouped by
the document family they would heal.
"""
import json
import sys
from collections import Counter
from pathlib import Path

repo = Path(sys.argv[1])
pred_path = Path(sys.argv[2])
out_path = Path(sys.argv[3])

sys.path.insert(0, str(repo))
from scorer.score import score_document, line_f1  # noqa: E402

docs = json.loads((repo / "corpus" / "v1" / "manifest.json").read_text())["documents"]
meta = {d["doc_id"]: d for d in docs}

run = json.loads(pred_path.read_text())
raw_preds = run["documents"] if "documents" in run else run


def normalise_prediction(p):
    p = dict(p) if p else p
    if p:
        if p.get("total_amount") is None and p.get("total") is not None:
            p["total_amount"] = p["total"]
        refs = p.get("references") or []
        p["references"] = [r if isinstance(r, dict) else {"value": r} for r in refs]
        p["flagged"] = bool(p.get("flagged"))
    return p


preds = {k: normalise_prediction(v) for k, v in raw_preds.items() if v is not None}

# fields bench.py deliberately leaves null (documented mapping decisions), and
# 'flag' (true for every doc because any warning — even "needed-by not found" —
# trips it): excluding these from the failure display keeps the table honest
# about actual extraction failures
SHOW = {"vendor_name", "invoice_number", "currency", "subtotal", "tax",
        "total", "line_items", "references"}

rows = []
for ans in docs:
    if ans.get("render") != "pdf":
        continue  # scan/photo/handwriting docs are the OCR tier, out of scope
    doc_id = ans["doc_id"]
    exp = ans["expected"]
    got = preds.get(doc_id)
    results = score_document(exp, got)
    line_score = line_f1(results)
    failed = sorted({r.field for r in results if not r.correct} & SHOW)
    band = sorted({r.field for r in results if r.correct and r.within_band is False} & SHOW)
    shown = [r for r in results if r.field in SHOW]
    score = (sum(r.correct for r in shown) / max(1, len(shown))
             + (line_score or 0.0)) / 2.0
    if not failed and (line_score or 1.0) == 1.0:
        continue  # fully correct -> not a failure row
    rows.append({
        "doc": doc_id, "m": meta.get(doc_id, {}), "score": round(score, 2),
        "failed": failed, "band": band,
        "line_f1": round(line_score, 2) if line_score is not None else None,
        "n_lines": len(exp.get("lines") or []),
    })

rows.sort(key=lambda r: (r["score"], r["doc"]))

total = len([d for d in docs if d.get("render") == "pdf"])

out = []
out.append("# Per-document failure table — baseline run (2026-10-19)")
out.append("")
out.append("Scored by the benchmark's own scorer against ground truth. "
           f"{len(rows)} of {total} digital-PDF docs fail at least one field "
           "(line_items wrong if F1 < 1.0; credit-note money compares magnitude; "
           "±1 cent noted beside money fields).")
out.append("")
out.append("| Doc | C | D | score | line F1 | failed fields |")
out.append("|---|---|---|---|---|---|")
for r in rows:
    m = r["m"]
    fields = ", ".join(sorted(set(r["failed"]) - {"line_items"}))
    lf1 = r["line_f1"]
    line_note = ""
    if lf1 is not None and lf1 < 1.0:
        extra = "line_items"
        fields = (fields + ", " + extra) if fields else extra
        line_note = f" (of {r['n_lines']})"
    band = "".join(f" · {f} ±1ct" for f in r["band"])
    out.append(f"| {r['doc']} | {m.get('category', '?')} | {m.get('difficulty', '?')} | "
               f"{r['score']:.2f} | {lf1 if lf1 is not None else '—'} | {fields}{line_note}{band} |")

out.append("")
out.append("## Failed-field counts by category (what one fix would heal)")
out.append("")
out.append("| category | field | docs |")
out.append("|---|---|---|")
cat_fields = Counter()
for r in rows:
    c = r["m"].get("category", "?")
    for f_name in sorted(set(r["failed"]) & SHOW):
        cat_fields[(c, f_name)] += 1
for (c, f_name), n in sorted(cat_fields.items(), key=lambda t: (-t[1], t[0])):
    out.append(f"| {c} | {f_name} | {n} |")

out_path.write_text("\n".join(out) + "\n")
print(f"wrote {out_path} — {len(rows)} failing docs of {total} digital PDFs")