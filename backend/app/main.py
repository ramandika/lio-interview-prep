"""FastAPI app: auth, PDF extraction, PR workflow with threshold approval.

Workflow (threshold-based, configurable per environment/customer):
  submit(total < threshold)        -> approved automatically, order created
  submit(total >= threshold)       -> pending_approval (buyer queues)
  buyer approve                    -> approved, order created
  buyer reject                     -> rejected
Order ids are generated deterministically: ORD-{year}-{sequence}.
The threshold is server config (APPROVAL_THRESHOLD env) — never client input
on the submit endpoint, so a PR can't be routed around the approval gate.
"""
import os
import json
import uuid
import secrets
from pathlib import Path

from fastapi import (FastAPI, UploadFile, File, Form, HTTPException,
                     Response, Request)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .extraction import pdf_extract

SAMPLES_DIR = Path(os.environ.get("SAMPLES_DIR", "/data/samples"))
UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR", "/data/uploads"))
DEFAULT_THRESHOLD = 1000.0


def _threshold() -> float:
    return float(os.environ.get("APPROVAL_THRESHOLD", DEFAULT_THRESHOLD))

app = FastAPI(title="PR Extractor")
db.seed()

def _user(request: Request) -> dict | None:
    uname = db.session_user(request.cookies.get("session", ""))
    if not uname:
        return None
    row = db.get_conn().execute(
        "SELECT username, role FROM users WHERE username=?", (uname,)).fetchone()
    return dict(row) if row else None


def _require(request: Request, role=None):
    u = _user(request)
    if not u:
        raise HTTPException(401, "not logged in")
    if role and u["role"] != role:
        raise HTTPException(403, f"requires role '{role}'")
    return u


# --------------------------------------------------------------------- auth
@app.get("/users")
def list_users():
    rows = db.get_conn().execute(
        "SELECT username, role FROM users").fetchall()
    return [dict(r) for r in rows]


@app.post("/login")
def login(username: str = Form(...), password: str = Form(None),
          response: Response = Response()):
    row = db.get_conn().execute(
        "SELECT username,password,role FROM users WHERE username=?",
        (username,)).fetchone()
    if not row or (password is not None and row["password"] != password):
        raise HTTPException(401, "invalid credentials")
    token = secrets.token_hex(16)
    db.store_session(token, username)
    response.set_cookie("session", token, httponly=True)
    return {"username": row["username"], "role": row["role"]}


@app.post("/logout")
def logout(response: Response):
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/me")
def me(request: Request):
    return _user(request) or {"username": None}


# ---------------------------------------------------------------- extraction
@app.get("/samples")
def list_samples(request: Request):
    _require(request)
    return sorted(p.name for p in SAMPLES_DIR.glob("*.pdf"))


@app.post("/extract-sample")
def extract_sample(request: Request, sample: str = Form(...)):
    """Run the pipeline against a bundled sample (demo path)."""
    _require(request)
    path = SAMPLES_DIR / sample
    if not path.exists() or ".." in sample:
        raise HTTPException(400, "no such sample")
    return pdf_extract.extract(str(path)).to_json()


@app.post("/extract")
def extract_upload(request: Request, file: UploadFile = File(...)):
    """Full path used by requests: save upload, extract, create a draft PR row.

    Every upload ALWAYS creates a row: a failed extraction is not an HTTP
    error and not a missing document — it is a row with extract_status
    'failed' or 'partial' plus an error blob, so failures stay reviewable and
    re-extractable (production: dead-letter queue + alerting on the rate).
    Statuses: extracted = items + total read; partial = some of that missing;
    failed = extractor found nothing usable."""
    u = _require(request)
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex}-{file.filename}"
    dest.write_bytes(file.file.read())
    result, err = None, None
    try:
        result = pdf_extract.extract(str(dest))
    except Exception as e:  # malformed/encrypted PDF: the row must still exist
        err = str(e)

    if result is None:
        status, data, error = "failed", None, {"exception": err or "unknown"}
    else:
        data = result.to_json()
        reasons = []
        if not result.items:
            reasons.append("item table not detected")
        if result.fields.get("_inferred_total"):
            reasons.append("total was inferred (not read from the document)")
        if reasons:
            status = "partial"
            error = {"reason": "; ".join(reasons),
                     "partial_fields": sorted(
                         k for k, v in result.fields.items()
                         if not k.startswith("_"))}
        else:
            status, error = "extracted", None

    req_id = db.create_request(
        title=file.filename.rsplit(".", 1)[0], description="",
        source_file=dest.name, extract_json=json.dumps(data or {}),
        created_by=u["username"], extract_status=status,
        extract_error=json.dumps(error) if error else None)
    payload = data or {"fields": {}, "items": [], "overall_confidence": 0.0,
                       "warnings": []}
    payload["request_id"] = req_id
    payload["extract_status"] = status
    payload["extract_error"] = error
    return payload


# ---------------------------------------------------------------- PR workflow
@app.get("/config")
def config():
    """Client-facing config so the UI can show the routing rule it obeys."""
    return {"approval_threshold": _threshold()}
@app.get("/samples/{name}/pdf")
def sample_pdf(name: str, request: Request):
    """Stream a bundled sample PDF so the UI can show it next to the extraction."""
    _require(request)
    path = SAMPLES_DIR / name
    if not path.exists() or ".." in name:
        raise HTTPException(400, "no such sample")
    return FileResponse(path, media_type="application/pdf", filename=name,
                        content_disposition_type="inline",
                        headers={"Cache-Control": "no-store"})


@app.get("/prs")
def list_prs(request: Request):
    _require(request)
    rows = db.get_conn().execute(
        """SELECT id,title,status,extract_status,extract_error,total_val,
                  currency,needed_by,vendor_name,
                  created_by,order_id,created_at,extract_json
           FROM requests ORDER BY created_at DESC, id DESC""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d.pop("extract_json")
        e = json.loads(r["extract_json"] or "{}")
        # overall score as produced by the pipeline, plus the weakest single
        # field/line-item score — a low minimum is what a reviewer needs to see
        d["overall_conf"] = e.get("overall_confidence")
        confs = [f.get("confidence") for f in e.get("fields", {}).values()]
        confs += [i.get("confidence") for i in e.get("items", [])]
        d["min_conf"] = min((c for c in confs if c is not None), default=None)
        out.append(d)
    return out


@app.get("/prs/{rid}")
def get_pr(rid: int, request: Request):
    _require(request)
    r = db.get_conn().execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not r:
        raise HTTPException(404)
    d = dict(r)
    d["extract"] = json.loads(d.pop("extract_json") or "{}") or {
        "fields": {}, "items": [], "overall_confidence": 0.0, "warnings": []}
    if "fields" not in d["extract"]:  # failed rows store only {}
        d["extract"].update({"fields": {}, "items": [],
                             "overall_confidence": 0.0, "warnings": []})
    try:
        d["extract_error"] = json.loads(d.pop("extract_error") or "null")
    except ValueError:
        d["extract_error"] = None
    try:
        d["corrections"] = json.loads(d.pop("corrections") or "null")
    except ValueError:
        d["corrections"] = None
    return d


@app.get("/prs/{rid}/pdf")
def pr_pdf(rid: int, request: Request):
    """Stream the source PDF of a PR (always an upload — sample previews have no PR row)."""
    _require(request)
    r = db.get_conn().execute("SELECT source_file FROM requests WHERE id=?",
                              (rid,)).fetchone()
    if not r or not r["source_file"]:
        raise HTTPException(404, "no source PDF")
    path = UPLOADS_DIR / r["source_file"]
    if not path.exists():
        raise HTTPException(404, "source PDF missing on disk")
    orig = r["source_file"].split("-", 1)[-1]  # strip the uuid- prefix
    return FileResponse(path, media_type="application/pdf", filename=orig,
                        content_disposition_type="inline",
                        headers={"Cache-Control": "no-store"})


@app.patch("/prs/{rid}")
def update_draft(rid: int, patch: dict, request: Request):
    u = _require(request)
    c = db.get_conn()
    r = c.execute(
        "SELECT status, created_by FROM requests WHERE id=?", (rid,)).fetchone()
    if not r:
        raise HTTPException(404)
    allowed = {"title", "description", "needed_by", "vendor_name"}
    if r["status"] != "draft":
        raise HTTPException(409, "only drafts can be edited")
    if r["created_by"] and r["created_by"] != u["username"]:
        raise HTTPException(403, "only the author can edit this PR")
    sets = {k: v for k, v in patch.items() if k in allowed}
    if sets:
        c.execute(
            f"UPDATE requests SET {','.join(f'{k}=?' for k in sets)} WHERE id=?",
            (*sets.values(), rid))
        c.commit()
    return {"ok": True}


@app.post("/prs/{rid}/submit")
def submit(rid: int, request: Request):
    """Two guards — role/state, per the docs: only the author can submit, and
    only from draft. On the way out, the immutable raw extraction is diffed
    against the human-corrected official columns and stored as `corrections`:
    free per-field eval labels, and the buyer approves knowing what changed."""
    u = _require(request)
    c = db.get_conn()
    r = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not r:
        raise HTTPException(404)
    if r["status"] != "draft":
        raise HTTPException(409, f"cannot submit from status {r['status']}")
    if r["extract_status"] == "failed":
        raise HTTPException(
            409, "extraction failed — re-upload the PDF or create the "
            "request manually; there is nothing to submit against")
    if r["created_by"] and r["created_by"] != u["username"]:
        raise HTTPException(403, "only the author can submit this PR")
    try:
        fields = json.loads(r["extract_json"] or "{}").get("fields", {})
    except ValueError:
        fields = {}
    diff = {}
    for k in ("title", "needed_by", "vendor_name"):
        raw = (fields.get(k) or {}).get("value")
        final = r[k]
        if (raw not in (None, "") and final not in (None, "")
                and str(raw) != str(final)):
            diff[k] = {"raw": raw, "final": final}
    c.execute("UPDATE requests SET status='submitted', corrections=? WHERE id=?",
              (json.dumps(diff), rid))
    total = r["total_val"] or 0.0
    threshold = _threshold()
    if total < threshold:
        _create_order(c, rid)
        c.execute("UPDATE requests SET status='approved' WHERE id=?", (rid,))
        new_status = "approved"   # auto-approved below threshold
    else:
        c.execute("UPDATE requests SET status='pending_approval' WHERE id=?",
                  (rid,))
        new_status = "pending_approval"
    c.commit()
    return {"status": new_status, "threshold": threshold, "total": total,
            "n_corrections": len(diff)}


def _create_order(c, rid: int) -> str:
    order_id = f"ORD-{db.now_iso()[:4]}-{rid:04d}"
    c.execute("UPDATE requests SET order_id=? WHERE id=?", (order_id, rid))
    return order_id


@app.post("/prs/{rid}/approve")
def approve(rid: int, request: Request):
    _require(request, role="buyer")
    c = db.get_conn()
    r = c.execute("SELECT status FROM requests WHERE id=?", (rid,)).fetchone()
    if not r or r["status"] != "pending_approval":
        raise HTTPException(409, f"cannot approve from status {r['status'] if r else '?'}")
    _create_order(c, rid)
    c.execute("UPDATE requests SET status='approved' WHERE id=?", (rid,))
    c.commit()
    return {"status": "approved"}


@app.post("/prs/{rid}/reject")
def reject(rid: int, request: Request):
    _require(request, role="buyer")
    c = db.get_conn()
    r = c.execute("SELECT status FROM requests WHERE id=?", (rid,)).fetchone()
    if not r or r["status"] != "pending_approval":
        raise HTTPException(409, "cannot reject: not pending_approval")
    c.execute("UPDATE requests SET status='rejected' WHERE id=?", (rid,))
    c.commit()
    return {"status": "rejected"}


# ---------------------------------------------------------------- static UI
app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "static",
                           html=True), name="static")