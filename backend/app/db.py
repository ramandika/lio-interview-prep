"""SQLite persistence. Deliberately stdlib-only: no ORM setup for a 5-hour build,
schema mirrors the domain model so an ORM migration is mechanical later.
Status lifecycle: draft -> submitted -> (pending_approval | approved) -> approved
                  pending_approval -> approved | rejected
"""
import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", "/data/uploads/app.db")
DDL = """
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  username TEXT NOT NULL REFERENCES users(username)
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('requester','buyer'))
);
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT,
  needed_by TEXT,
  vendor_name TEXT,
  vendor_id TEXT,
  currency TEXT,
  subtotal REAL,
  tax REAL,
  total_val REAL,
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK(status IN ('draft','submitted','pending_approval','approved','rejected')),
  created_by TEXT,
  source_file TEXT,
  extract_json TEXT,
  -- extraction status is a DIFFERENT axis from the workflow `status`:
  -- every upload always creates a row, so a failed parse is not a missing
  -- document, only a row with extract_status='failed' and partial extract_json
  extract_status TEXT NOT NULL DEFAULT 'extracted'
    CHECK(extract_status IN ('extracted','partial','failed')),
  extract_error TEXT,
  order_id TEXT,
  created_at TEXT NOT NULL
);
"""

SEED_USERS = [("alice", "alice", "requester"), ("bob", "bob", "buyer"),
              ("pranamulia", "pranamulia", "requester")]

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(DDL)
        # pre-existing DBs don't get DDL's new columns; migrate them in place
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(requests)")}
        if "corrections" not in cols:
            _conn.execute("ALTER TABLE requests ADD COLUMN corrections TEXT")
        if "extract_status" not in cols:
            _conn.execute(
                "ALTER TABLE requests ADD COLUMN extract_status TEXT"
                " NOT NULL DEFAULT 'extracted'")
        if "extract_error" not in cols:
            _conn.execute("ALTER TABLE requests ADD COLUMN extract_error TEXT")
        _conn.commit()
    return _conn


def seed():
    c = get_conn()
    if not c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        c.executemany("INSERT INTO users(username,password,role) VALUES(?,?,?)",
                      SEED_USERS)
        c.commit()


def now_iso():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def create_request(title, description, source_file, extract_json: str,
                   created_by: str = "", extract_status: str = "extracted",
                   extract_error: str | None = None) -> int:
    """Derive normalized columns from the extraction JSON so the list view
    can sort/filter in SQL; full result stays in extract_json for the review UI.

    Failed parses are still rows — `extract_status='failed'` with a JSONB
    error blob and whatever extract_json is available — not silent drops.
    """
    c = get_conn()
    f = json.loads(extract_json).get("fields", {})

    def val(name, cast=None):
        e = f.get(name)
        if not e or e.get("value") in (None, ""):
            return None
        try:
            return cast(e["value"]) if cast else e["value"]
        except (TypeError, ValueError):
            return None

    cur = c.execute(
        """INSERT INTO requests(title, description, needed_by, vendor_name,
             vendor_id, currency, subtotal, tax, total_val, status,
             created_by, source_file, extract_json, extract_status,
             extract_error, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (title or "Untitled request", description or "", val("needed_by"),
         val("vendor_name") or val("customer_text"), val("vendor_id"),
         val("currency"),
         val("subtotal", float), val("tax", float), val("total", float),
         "draft", created_by, source_file, extract_json, extract_status,
         extract_error, now_iso()))
    c.commit()
    return cur.lastrowid

def store_session(token: str, username: str):
    c = get_conn()
    c.execute("INSERT OR REPLACE INTO sessions(token,username) VALUES(?,?)",
              (token, username))
    c.commit()


def session_user(token: str) -> str | None:
    if not token:
        return None
    row = get_conn().execute(
        "SELECT username FROM sessions WHERE token=?", (token,)).fetchone()
    return row["username"] if row else None
