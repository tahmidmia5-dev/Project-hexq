"""HTTP server: JSON API plus the static dashboard. Python standard library only."""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import re
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import scoring
from .db import DuplicateLead, Store
from .exporting import leads_to_csv
from .providers import ProviderError, get_provider, youtube_api_enabled
from .research import ResearchError, research_url, youtube_search_url

STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_BODY = 256 * 1024
MAX_BULK = 25


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


def make_handler(store: Store):
    token = os.environ.get("AQ_ACCESS_TOKEN")  # optional shared secret, for hosting later

    class Handler(BaseHTTPRequestHandler):
        server_version = "AQClipz/1.0"

        def log_message(self, fmt, *args):
            if os.environ.get("AQ_QUIET") != "1":
                sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

        # ---------- plumbing ----------
        def _send(self, status: int, body: bytes, ctype: str, extra_headers: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, data):
            self._send(status, json.dumps(data, default=str).encode(), "application/json; charset=utf-8",
                       {"Cache-Control": "no-store"})

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ApiError(413, "Request too large")
            if not n:
                return {}
            try:
                data = json.loads(self.rfile.read(n))
            except json.JSONDecodeError:
                raise ApiError(400, "Invalid JSON")
            if not isinstance(data, dict):
                raise ApiError(400, "Expected a JSON object")
            return data

        def _authorised(self) -> bool:
            if not token:
                return True
            got = self.headers.get("X-Access-Token") or ""
            cookie = self.headers.get("Cookie") or ""
            m = re.search(r"aq_token=([^;]+)", cookie)
            if m:
                got = got or m.group(1)
            return hmac.compare_digest(got, token)

        def _dispatch(self, method: str):
            url = urlparse(self.path)
            path = url.path
            qs = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if path.startswith("/api/"):
                    if not self._authorised():
                        raise ApiError(401, "Access token required")
                    return self._api(method, path, qs)
                if method not in ("GET", "HEAD"):
                    raise ApiError(405, "Method not allowed")
                return self._static(path)
            except ApiError as e:
                self._json(e.status, {"error": e.message, **e.extra})
            except DuplicateLead as e:
                self._json(409, {"error": f"Duplicate lead ({e.reason})", "lead_id": e.lead_id})
            except Exception as e:  # pragma: no cover
                self.log_message("ERROR %r", e)
                self._json(500, {"error": "Internal error"})

        def do_GET(self):
            self._dispatch("GET")

        def do_HEAD(self):
            self._dispatch("HEAD")

        def do_POST(self):
            self._dispatch("POST")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def _static(self, path: str):
            if path in ("", "/"):
                path = "/index.html"
            target = (STATIC / path.lstrip("/")).resolve()
            if STATIC not in target.parents or not target.is_file():
                target = STATIC / "index.html"  # SPA fallback
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype, {"Cache-Control": "no-cache"})

        # ---------- API ----------
        def _lead_id(self, s: str) -> int:
            try:
                return int(s)
            except ValueError:
                raise ApiError(404, "Not found")

        def _api(self, method: str, path: str, qs: dict):
            parts = path.strip("/").split("/")[1:]  # drop "api"
            route = (method, parts[0] if parts else "")

            if route == ("GET", "config"):
                return self._json(200, {
                    "provider": get_provider().name,
                    "youtube_api_enabled": youtube_api_enabled(),
                    "niches": scoring.ALL_NICHES,
                    "target_niches": list(scoring.TARGET_NICHES),
                    "content_formats": {k: v[0] for k, v in scoring.CONTENT_FORMATS.items()},
                    "critical_fields": scoring.CRITICAL_FIELDS,
                    "max_points": scoring.MAX_POINTS,
                })
            if route == ("GET", "stats"):
                return self._json(200, store.stats())

            if parts[:1] == ["leads"]:
                return self._leads(method, parts[1:], qs)

            if route == ("POST", "research"):
                body = self._body()
                url = (body.get("url") or "").strip()
                if not url:
                    raise ApiError(400, "url is required")
                try:
                    out = research_url(store, url, fetch=bool(body.get("fetch", True)))
                except ResearchError as e:
                    raise ApiError(422, str(e))
                return self._json(201, out)

            if route == ("POST", "research-bulk"):
                body = self._body()
                urls = [u.strip() for u in re.split(r"[\s,]+", body.get("urls") or "") if u.strip()]
                urls = list(dict.fromkeys(urls))
                if not urls:
                    raise ApiError(400, "Paste at least one URL")
                if len(urls) > MAX_BULK:
                    raise ApiError(400, f"At most {MAX_BULK} URLs per batch")
                results = []
                for u in urls:
                    try:
                        r = research_url(store, u, fetch=bool(body.get("fetch", True)))
                        results.append({"url": u, "ok": True, **r})
                    except DuplicateLead as e:
                        results.append({"url": u, "ok": False, "duplicate": True, "lead_id": e.lead_id,
                                        "error": f"Duplicate ({e.reason})"})
                    except ResearchError as e:
                        results.append({"url": u, "ok": False, "error": str(e)})
                return self._json(200, {"results": results})

            if route == ("POST", "search"):
                body = self._body()
                query = (body.get("query") or "").strip()
                if not query:
                    raise ApiError(400, "query is required")
                link = youtube_search_url(query)
                if youtube_api_enabled():
                    try:
                        results = get_provider().search_channels(query)
                    except ProviderError as e:
                        store.log("search", query, "error", str(e))
                        raise ApiError(502, str(e))
                    for r in results:
                        dup = store.find_duplicate(channel_id=r["channel_id"])
                        r["existing_lead_id"] = dup[0] if dup else None
                    store.log("search", query, "ok", f"{len(results)} channels via YouTube API")
                    return self._json(200, {"mode": "api", "search_url": link, "results": results})
                store.log("search", query, "logged", "Manual search link opened (no API key)")
                return self._json(200, {"mode": "manual", "search_url": link, "results": []})

            if route == ("GET", "history"):
                return self._json(200, store.history())

            if route == ("GET", "duplicates"):
                from .urls import UrlError, parse_youtube_url
                try:
                    p = parse_youtube_url(qs.get("url", ""))
                except UrlError as e:
                    raise ApiError(400, str(e))
                dup = store.find_duplicate(channel_key=p.key,
                                           channel_id=p.value if p.kind == "channel_id" else None,
                                           handle=p.value if p.kind == "handle" else None)
                return self._json(200, {"duplicate": bool(dup), "lead_id": dup[0] if dup else None})

            if route == ("GET", "export.csv"):
                stage = qs.get("stage") or None
                if stage not in (None, "qualified", "pending", "rejected"):
                    raise ApiError(400, "Invalid stage")
                leads = store.list_leads(stage=stage, sort="score")
                ev: dict[int, list[str]] = {}
                for row in store.conn.execute("SELECT lead_id, url FROM evidence ORDER BY id"):
                    ev.setdefault(row["lead_id"], []).append(row["url"])
                csv_text = leads_to_csv(leads, ev)
                name = f"aqclipz-leads-{stage or 'all'}-{date.today().isoformat()}.csv"
                store.log("manual", f"CSV export: {stage or 'all'}", "ok", f"{len(leads)} rows")
                return self._send(200, ("﻿" + csv_text).encode(), "text/csv; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="{name}"',
                                   "Cache-Control": "no-store"})

            if route == ("DELETE", "evidence") and len(parts) == 2:
                if not store.delete_evidence(self._lead_id(parts[1])):
                    raise ApiError(404, "Not found (fetched evidence cannot be deleted)")
                return self._json(200, {"ok": True})

            raise ApiError(404, "Not found")

        def _leads(self, method: str, rest: list[str], qs: dict):
            if not rest:
                if method == "GET":
                    stage = qs.get("stage") or None
                    return self._json(200, store.list_leads(stage=stage, q=qs.get("q") or None,
                                                            tier=qs.get("tier") or None,
                                                            sort=qs.get("sort") or "score"))
                raise ApiError(405, "Method not allowed")
            lead_id = self._lead_id(rest[0])
            if not store.get_lead(lead_id, with_children=False):
                raise ApiError(404, "Lead not found")
            if len(rest) == 1:
                if method == "GET":
                    return self._json(200, store.get_lead(lead_id))
                if method == "PATCH":
                    body = self._body()
                    try:
                        lead = store.update_lead(lead_id, body.get("fields") or {}, body.get("provenance") or {})
                    except ValueError as e:
                        raise ApiError(400, str(e))
                    return self._json(200, lead)
                if method == "DELETE":
                    store.delete_lead(lead_id)
                    return self._json(200, {"ok": True})
            if rest[1:] == ["refresh"] and method == "POST":
                lead = store.get_lead(lead_id, with_children=False)
                try:
                    out = research_url(store, lead["channel_url"], refresh_lead_id=lead_id)
                except ResearchError as e:
                    raise ApiError(422, str(e))
                return self._json(200, out)
            if rest[1:] == ["evidence"] and method == "POST":
                body = self._body()
                url = (body.get("url") or "").strip()
                if not re.match(r"^https?://\S+$", url):
                    raise ApiError(400, "Evidence must be an http(s) URL")
                return self._json(201, store.add_evidence(lead_id, url, (body.get("label") or "").strip() or None))
            raise ApiError(404, "Not found")

    return Handler


def serve(host: str | None = None, port: int | None = None, db_path: str | None = None):
    store = Store(db_path)
    host = host or os.environ.get("HOST", "127.0.0.1")
    port = port or int(os.environ.get("PORT", "8000"))
    httpd = ThreadingHTTPServer((host, port), make_handler(store))
    print(f"AQ Clipz Lead Engine on http://{host}:{port}  (db: {store.path}, provider: {get_provider().name})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
