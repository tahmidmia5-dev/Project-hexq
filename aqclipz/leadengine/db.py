"""SQLite storage for leads, evidence, videos and search history."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import scoring
from .urls import normalise_name

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "aqclipz.db"

# Lead fields a user may edit. Value: type for coercion.
EDITABLE = {
    "creator_name": str,
    "channel_url": str,
    "subscriber_count": int,
    "niche": str,
    "country": str,
    "longform_per_month": float,
    "shorts_per_month": float,
    "days_since_last_longform": int,
    "max_longform_gap_days": int,
    "avg_longform_minutes": float,
    "content_format": str,
    "quotable_moments": int,
    "guest_variety": int,
    "has_offer": int,
    "has_sponsors": int,
    "has_business_email": int,
    "has_website": int,
    "has_booking_link": int,
    "website_url": str,
    "best_sample_video_url": str,
    "manual_reject": int,
    "notes": str,
}
# Fields that carry verified/estimate provenance.
PROVENANCE_FIELDS = [
    "subscriber_count", "niche", "country", "longform_per_month", "shorts_per_month",
    "days_since_last_longform", "max_longform_gap_days", "avg_longform_minutes", "content_format",
    "quotable_moments", "guest_variety", "has_offer", "has_sponsors", "has_business_email",
    "has_website", "has_booking_link",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_key TEXT NOT NULL UNIQUE,
    channel_id TEXT UNIQUE,
    handle TEXT,
    creator_name TEXT,
    name_key TEXT,
    channel_url TEXT NOT NULL,
    subscriber_count INTEGER,
    niche TEXT,
    country TEXT,
    longform_per_month REAL,
    shorts_per_month REAL,
    days_since_last_longform INTEGER,
    days_since_last_short INTEGER,
    max_longform_gap_days INTEGER,
    avg_longform_minutes REAL,
    content_format TEXT,
    quotable_moments INTEGER,
    guest_variety INTEGER,
    has_offer INTEGER,
    has_sponsors INTEGER,
    has_business_email INTEGER,
    has_website INTEGER,
    has_booking_link INTEGER,
    website_url TEXT,
    best_sample_video_url TEXT,
    score INTEGER NOT NULL DEFAULT 0,
    tier TEXT NOT NULL DEFAULT 'Reject',
    stage TEXT NOT NULL DEFAULT 'pending',
    verification_status TEXT NOT NULL DEFAULT 'unverified',
    score_breakdown TEXT,
    qualification_explanation TEXT,
    provenance TEXT NOT NULL DEFAULT '{}',
    manual_reject INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    research_provider TEXT,
    research_warnings TEXT,
    research_date TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leads_stage ON leads(stage);
CREATE INDEX IF NOT EXISTS idx_leads_handle ON leads(handle);
CREATE INDEX IF NOT EXISTS idx_leads_name_key ON leads(name_key);

CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    label TEXT,
    source TEXT NOT NULL,          -- 'fetched' | 'manual'
    captured_at TEXT NOT NULL,
    UNIQUE(lead_id, url)
);

CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    video_id TEXT NOT NULL,
    kind TEXT NOT NULL,            -- 'long' | 'short'
    title TEXT,
    url TEXT NOT NULL,
    published_at TEXT,
    views INTEGER,
    duration_seconds INTEGER,
    UNIQUE(lead_id, video_id)
);

CREATE TABLE IF NOT EXISTS search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,            -- 'search' | 'research' | 'refresh' | 'manual'
    query TEXT NOT NULL,
    outcome TEXT NOT NULL,         -- 'ok' | 'duplicate' | 'error' | 'logged'
    detail TEXT,
    lead_id INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class DuplicateLead(Exception):
    def __init__(self, lead_id: int, reason: str):
        super().__init__(reason)
        self.lead_id = lead_id
        self.reason = reason


class Store:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = str(path or os.environ.get("AQ_DB_PATH") or DEFAULT_DB)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------- helpers ----------
    def _row_to_lead(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        d["provenance"] = json.loads(d.get("provenance") or "{}")
        d["score_breakdown"] = json.loads(d["score_breakdown"]) if d.get("score_breakdown") else []
        d["research_warnings"] = json.loads(d["research_warnings"]) if d.get("research_warnings") else []
        return d

    def _rescore(self, lead_id: int) -> None:
        lead = self.get_lead(lead_id, with_children=False)
        q = scoring.qualify(lead)
        self.conn.execute(
            """UPDATE leads SET score=?, tier=?, stage=?, verification_status=?,
               score_breakdown=?, qualification_explanation=? WHERE id=?""",
            (q["score"], q["tier"], q["stage"], q["verification_status"],
             json.dumps(q["breakdown"]), q["explanation"], lead_id),
        )

    # ---------- duplicates ----------
    def find_duplicate(self, channel_key: str | None = None, channel_id: str | None = None,
                       handle: str | None = None, exclude_id: int | None = None) -> tuple[int, str] | None:
        checks = []
        if channel_id:
            checks.append(("channel_id = ?", channel_id, "same channel ID"))
            checks.append(("channel_key = ?", f"id:{channel_id}", "same channel ID"))
        if handle:
            checks.append(("lower(handle) = lower(?)", handle, "same @handle"))
            checks.append(("channel_key = ?", f"handle:{handle.lower()}", "same @handle"))
        if channel_key:
            checks.append(("channel_key = ?", channel_key, "same channel URL"))
        for where, val, reason in checks:
            sql = f"SELECT id FROM leads WHERE {where}"
            args: list = [val]
            if exclude_id:
                sql += " AND id != ?"
                args.append(exclude_id)
            row = self.conn.execute(sql, args).fetchone()
            if row:
                return row["id"], reason
        return None

    def similar_names(self, name: str | None, exclude_id: int | None = None) -> list[dict]:
        key = normalise_name(name)
        if len(key) < 4:
            return []
        rows = self.conn.execute(
            "SELECT id, creator_name, channel_url FROM leads WHERE name_key = ? AND id != ?",
            (key, exclude_id or -1),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------- leads ----------
    def create_lead(self, channel_key: str, channel_url: str, *, channel_id: str | None = None,
                    handle: str | None = None, creator_name: str | None = None) -> int:
        with self._lock:
            dup = self.find_duplicate(channel_key, channel_id, handle)
            if dup:
                raise DuplicateLead(*dup)
            ts = now_iso()
            cur = self.conn.execute(
                """INSERT INTO leads (channel_key, channel_id, handle, creator_name, name_key, channel_url,
                   research_date, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                (channel_key, channel_id, handle, creator_name, normalise_name(creator_name),
                 channel_url, ts[:10], ts, ts),
            )
            lead_id = cur.lastrowid
            self._rescore(lead_id)
            self.conn.commit()
            return lead_id

    def get_lead(self, lead_id: int, with_children: bool = True) -> dict | None:
        row = self.conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        lead = self._row_to_lead(row)
        if lead and with_children:
            lead["evidence"] = [dict(r) for r in self.conn.execute(
                "SELECT * FROM evidence WHERE lead_id = ? ORDER BY id", (lead_id,))]
            lead["videos"] = [dict(r) for r in self.conn.execute(
                "SELECT * FROM videos WHERE lead_id = ? ORDER BY published_at DESC", (lead_id,))]
            lead["similar"] = self.similar_names(lead.get("creator_name"), lead_id)
        return lead

    def list_leads(self, stage: str | None = None, q: str | None = None, tier: str | None = None,
                   sort: str = "score") -> list[dict]:
        sql = "SELECT * FROM leads WHERE 1=1"
        args: list = []
        if stage:
            sql += " AND stage = ?"
            args.append(stage)
        if tier:
            sql += " AND tier = ?"
            args.append(tier)
        if q:
            sql += " AND (creator_name LIKE ? OR channel_url LIKE ? OR niche LIKE ? OR handle LIKE ?)"
            args += [f"%{q}%"] * 4
        order = {
            "score": "score DESC, updated_at DESC",
            "recent": "updated_at DESC",
            "name": "creator_name COLLATE NOCASE ASC",
            "subscribers": "subscriber_count DESC",
        }.get(sort, "score DESC")
        sql += f" ORDER BY {order}"
        return [self._row_to_lead(r) for r in self.conn.execute(sql, args)]

    def update_lead(self, lead_id: int, fields: dict, provenance: dict | None = None) -> dict:
        with self._lock:
            lead = self.get_lead(lead_id, with_children=False)
            if not lead:
                raise KeyError(lead_id)
            sets, args = [], []
            for k, v in fields.items():
                if k not in EDITABLE:
                    continue
                if v == "" or v is None:
                    v = None
                else:
                    try:
                        v = EDITABLE[k](v)
                    except (TypeError, ValueError):
                        raise ValueError(f"Invalid value for {k}")
                    if EDITABLE[k] in (int, float) and v < 0:
                        raise ValueError(f"{k} cannot be negative")
                sets.append(f"{k} = ?")
                args.append(v)
                if k == "creator_name":
                    sets.append("name_key = ?")
                    args.append(normalise_name(v))
            prov = dict(lead["provenance"])
            provenance = provenance or {}
            # A value changed by hand with no provenance given is an estimate, never verified.
            for k in fields:
                if k in PROVENANCE_FIELDS and k not in provenance:
                    new = args[sets.index(f"{k} = ?")]
                    if new is None:
                        prov.pop(k, None)
                    elif new != lead.get(k):
                        prov[k] = "estimate"
            for k, v in provenance.items():
                if k in PROVENANCE_FIELDS and v in ("verified", "estimate"):
                    prov[k] = v
            sets.append("provenance = ?")
            args.append(json.dumps(prov))
            sets.append("updated_at = ?")
            args.append(now_iso())
            args.append(lead_id)
            self.conn.execute(f"UPDATE leads SET {', '.join(sets)} WHERE id = ?", args)
            self._rescore(lead_id)
            self.conn.commit()
            return self.get_lead(lead_id)

    def apply_research(self, lead_id: int, research: dict, metrics: dict) -> None:
        """Write fetched data. Fetched values are marked verified because they come from
        public YouTube pages, which are saved as evidence. Fields the user set by hand
        stay as they are unless fresh fetched data replaces them."""
        with self._lock:
            lead = self.get_lead(lead_id, with_children=False)
            prov = dict(lead["provenance"])
            fields: dict = {}
            mapping = {
                "creator_name": research.get("title"),
                "subscriber_count": research.get("subscriber_count"),
                "country": research.get("country"),
                **metrics,
            }
            for k, v in mapping.items():
                if v is None:
                    continue
                fields[k] = v
                if k in PROVENANCE_FIELDS:
                    prov[k] = "verified"
            if research.get("channel_id"):
                fields["channel_id"] = research["channel_id"]
            if research.get("handle"):
                fields["handle"] = research["handle"]
            if not lead.get("best_sample_video_url") and metrics.get("_best_sample"):
                fields["best_sample_video_url"] = metrics["_best_sample"]
            fields.pop("_best_sample", None)
            if "creator_name" in fields:
                fields["name_key"] = normalise_name(fields["creator_name"])
            ts = now_iso()
            fields.update(
                provenance=json.dumps(prov),
                research_provider=research.get("provider"),
                research_warnings=json.dumps(research.get("warnings") or []),
                research_date=ts[:10],
                updated_at=ts,
            )
            cols = ", ".join(f"{k} = ?" for k in fields)
            self.conn.execute(f"UPDATE leads SET {cols} WHERE id = ?", [*fields.values(), lead_id])
            for ev in research.get("evidence") or []:
                self.conn.execute(
                    "INSERT OR IGNORE INTO evidence (lead_id, url, label, source, captured_at) VALUES (?,?,?,?,?)",
                    (lead_id, ev["url"], ev.get("label"), "fetched", ts),
                )
            self.conn.execute("DELETE FROM videos WHERE lead_id = ?", (lead_id,))
            for v in (research.get("long_videos") or []) + (research.get("shorts") or []):
                self.conn.execute(
                    """INSERT OR IGNORE INTO videos (lead_id, video_id, kind, title, url, published_at, views,
                       duration_seconds) VALUES (?,?,?,?,?,?,?,?)""",
                    (lead_id, v["video_id"], v["kind"], v["title"], v["url"], v["published_at"],
                     v.get("views"), v.get("duration_seconds")),
                )
            self._rescore(lead_id)
            self.conn.commit()

    def delete_lead(self, lead_id: int) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
            self.conn.commit()
            return cur.rowcount > 0

    # ---------- evidence ----------
    def add_evidence(self, lead_id: int, url: str, label: str | None) -> dict:
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO evidence (lead_id, url, label, source, captured_at) VALUES (?,?,?,?,?)",
                (lead_id, url, label, "manual", now_iso()),
            )
            self.conn.execute("UPDATE leads SET updated_at = ? WHERE id = ?", (now_iso(), lead_id))
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM evidence WHERE lead_id = ? AND url = ?", (lead_id, url)).fetchone()
            return dict(row)

    def delete_evidence(self, evidence_id: int) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM evidence WHERE id = ? AND source = 'manual'", (evidence_id,))
            self.conn.commit()
            return cur.rowcount > 0

    # ---------- history ----------
    def log(self, kind: str, query: str, outcome: str, detail: str | None = None,
            lead_id: int | None = None) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO search_history (kind, query, outcome, detail, lead_id, created_at) VALUES (?,?,?,?,?,?)",
                (kind, query, outcome, detail, lead_id, now_iso()),
            )
            self.conn.commit()

    def history(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(
            """SELECT h.*, l.creator_name FROM search_history h LEFT JOIN leads l ON l.id = h.lead_id
               ORDER BY h.id DESC LIMIT ?""", (limit,))
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        r = self.conn.execute(
            """SELECT COUNT(*) total,
                      SUM(stage='qualified') qualified, SUM(stage='pending') pending,
                      SUM(stage='rejected') rejected, SUM(tier='Excellent' AND stage='qualified') excellent,
                      ROUND(AVG(CASE WHEN stage='qualified' THEN score END), 1) avg_qualified_score
               FROM leads"""
        ).fetchone()
        out = {k: (r[k] or 0) for k in r.keys()}
        out["searches"] = self.conn.execute("SELECT COUNT(*) FROM search_history").fetchone()[0]
        return out

    # ---------- snapshot (for committing data to git) ----------
    def export_snapshot(self) -> dict:
        tables = {}
        for t in ("leads", "evidence", "videos", "search_history"):
            tables[t] = [dict(r) for r in self.conn.execute(f"SELECT * FROM {t} ORDER BY id")]
        return {"version": 1, "exported_at": now_iso(), "tables": tables}

    def import_snapshot(self, snap: dict) -> None:
        with self._lock:
            if self.conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]:
                raise RuntimeError("Database is not empty. Refusing to import over existing data.")
            for t in ("leads", "evidence", "videos", "search_history"):
                for row in snap["tables"].get(t, []):
                    cols = ", ".join(row)
                    qs = ", ".join("?" for _ in row)
                    self.conn.execute(f"INSERT INTO {t} ({cols}) VALUES ({qs})", list(row.values()))
            self.conn.commit()
