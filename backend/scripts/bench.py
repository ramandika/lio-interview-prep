"""Benchmark our heuristic extractor against DrewKraken/invoice-extraction-benchmark.

Runs only on the digital (text-layer) PDFs of corpus/v1 — photos and scans need
the OCR/vision tier we have not built, so they are outside scoring scope.

Field mapping (deliberately honest, no gaming):
  invoice_number <- quote_number   same slot semantics in a quote
  due_date       <- null           their due date is the payment due date;
                                   our needed_by is a delivery date
  document_type  <- null           we do not classify credit notes yet
  flagged        <- bool(warnings) our arithmetic-warning system IS the
                                   inconsistency flag

Usage:
  python scripts/bench.py <corpus_v1_dir> <out_predictions.json>
"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.extraction import pdf_extract  # noqa: E402

corpus = Path(sys.argv[1])
out_path = Path(sys.argv[2])
docs = json.loads((corpus / "manifest.json").read_text())["documents"]
out = {"system": "lio-heuristic-pipeline", "run_date": date.today().isoformat(),
       "documents": {}}
failed = 0
for d in docs:
    if not d["file"].endswith(".pdf"):
        continue
    try:
        r = pdf_extract.extract(str(corpus / d["file"]))
    except Exception as e:  # unreadable PDF: report, continue
        failed += 1
        print(f"FAIL {d['doc_id']}: {e}")
        continue
    f = {k: v["value"] for k, v in r.fields.items() if not k.startswith("_")}
    out["documents"][d["doc_id"]] = {
        "vendor_name": f.get("vendor_name"),
        "invoice_number": f.get("quote_number"),
        "invoice_date": None,
        "due_date": None,
        "currency": f.get("currency"),
        "subtotal": f.get("subtotal"),
        "tax": f.get("tax"),
        "total_amount": f.get("total"),
        "document_type": None,
        "line_items": [{"description": i.description, "amount": i.line_total}
                       for i in r.items],
        "flagged": bool(r.warnings),
        "references": [],
    }
out_path.write_text(json.dumps(out, ensure_ascii=False))
print(f"wrote {out_path}: {len(out['documents'])} docs scored, {failed} extraction failures")