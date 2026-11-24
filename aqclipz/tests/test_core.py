"""Offline tests. No network calls. Run: python3 -m unittest discover -s tests"""

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leadengine import scoring  # noqa: E402
from leadengine.db import DuplicateLead, Store  # noqa: E402
from leadengine.exporting import leads_to_csv  # noqa: E402
from leadengine.providers import ChannelResearch, ProviderError, Video  # noqa: E402
from leadengine.providers.public_feed import parse_channel_page, parse_feed, parse_subscriber_text, parse_video_durations  # noqa: E402
from leadengine.research import ResearchError, derive_metrics, research_url, upload_rate  # noqa: E402
from leadengine.urls import UrlError, parse_youtube_url  # noqa: E402

NOW = datetime(2026, 11, 24, tzinfo=timezone.utc)
CID = "UC" + "a" * 22

FULL = {
    "subscriber_count": 120_000, "country": "US", "niche": "podcast", "content_format": "podcast_interview",
    "longform_per_month": 8, "shorts_per_month": 0, "avg_longform_minutes": 60, "days_since_last_longform": 3,
    "max_longform_gap_days": 7, "quotable_moments": 1, "guest_variety": 1, "has_offer": 1, "has_sponsors": 1,
    "has_business_email": 1, "has_website": 1, "has_booking_link": 1,
}


def verified(lead):
    return {**lead, "provenance": {k: "verified" for k in lead}}


class UrlTests(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(parse_youtube_url("https://www.youtube.com/@Foo").key, "handle:@foo")
        self.assertEqual(parse_youtube_url("youtube.com/@foo/videos").key, "handle:@foo")
        self.assertEqual(parse_youtube_url("m.youtube.com/@foo").key, "handle:@foo")
        self.assertEqual(parse_youtube_url(f"https://youtube.com/channel/{CID}").key, f"id:{CID}")
        self.assertEqual(parse_youtube_url("https://youtu.be/abcdefghijk").kind, "video")
        self.assertEqual(parse_youtube_url("https://www.youtube.com/watch?v=abcdefghijk&t=3").value, "abcdefghijk")
        self.assertEqual(parse_youtube_url("https://www.youtube.com/shorts/abcdefghijk").kind, "video")
        self.assertEqual(parse_youtube_url("@bar").kind, "handle")

    def test_rejects(self):
        for bad in ["", "https://evil.com/@foo", "https://youtube.com/", "https://youtube.com/feed/trending"]:
            with self.assertRaises(UrlError):
                parse_youtube_url(bad)


class ScoringTests(unittest.TestCase):
    def test_perfect_lead(self):
        q = scoring.qualify(verified(FULL))
        self.assertEqual(q["score"], 100)
        self.assertEqual(q["tier"], "Excellent")
        self.assertEqual(q["stage"], "qualified")

    def test_part_maxima_never_exceeded(self):
        q = scoring.qualify(verified(FULL))
        for part in q["breakdown"]:
            self.assertLessEqual(part["points"], part["max"])
        self.assertEqual(sum(scoring.MAX_POINTS.values()), 100)

    def test_unknowns_score_zero(self):
        q = scoring.qualify({})
        self.assertEqual(q["score"], 0)
        self.assertEqual(q["stage"], "pending")
        self.assertIn("Missing", q["explanation"])

    def test_estimates_stay_pending(self):
        lead = verified(FULL)
        lead["provenance"]["country"] = "estimate"
        q = scoring.qualify(lead)
        self.assertEqual(q["stage"], "pending")
        self.assertEqual(q["verification_status"], "partial")
        self.assertIn("estimate", q["explanation"])

    def test_tiers(self):
        self.assertEqual(scoring.tier_for(85), "Excellent")
        self.assertEqual(scoring.tier_for(84), "Qualified")
        self.assertEqual(scoring.tier_for(70), "Qualified")
        self.assertEqual(scoring.tier_for(69), "Possible")
        self.assertEqual(scoring.tier_for(55), "Possible")
        self.assertEqual(scoring.tier_for(54), "Reject")

    def test_low_verified_is_rejected(self):
        lead = verified({**FULL, "country": "UK", "niche": "tech", "shorts_per_month": 40,
                         "has_offer": 0, "has_sponsors": 0, "has_business_email": 0, "has_website": 0,
                         "has_booking_link": 0, "content_format": "vlog", "quotable_moments": 0, "guest_variety": 0})
        q = scoring.qualify(lead)
        self.assertLess(q["score"], 55)
        self.assertEqual(q["stage"], "rejected")

    def test_shorts_gap_needs_longform(self):
        p = scoring.score_shortform_gap({"shorts_per_month": 0, "longform_per_month": 0})
        self.assertEqual(p.points, 0)

    def test_stale_channel_capped(self):
        p = scoring.score_longform({"longform_per_month": 10, "days_since_last_longform": 80})
        self.assertEqual(p.points, 6)

    def test_manual_reject(self):
        q = scoring.qualify({**verified(FULL), "manual_reject": 1})
        self.assertEqual(q["stage"], "rejected")


class MetricsTests(unittest.TestCase):
    def test_rate_window(self):
        dates = [NOW - timedelta(days=d) for d in (1, 10, 20, 100, 200)]
        self.assertAlmostEqual(upload_rate(dates, False, NOW), round(3 / (90 / 30.44), 1))

    def test_rate_saturated(self):
        dates = [NOW - timedelta(days=d) for d in range(1, 16)]  # 15 uploads in 15 days
        self.assertGreater(upload_rate(dates, True, NOW), 25)

    def test_failed_feed_gives_none(self):
        res = ChannelResearch(provider="t", long_feed_ok=True, shorts_feed_ok=False,
                              long_videos=[Video("v" * 11, "t", "u", "long", (NOW - timedelta(days=2)).isoformat())])
        m = derive_metrics(res, NOW)
        self.assertNotIn("shorts_per_month", m)
        self.assertIn("longform_per_month", m)


FEED = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/">
<entry><yt:videoId>aaaaaaaaaaa</yt:videoId><title>Old</title><published>2026-01-01T00:00:00+00:00</published>
<media:group><media:community><media:statistics views="10"/></media:community></media:group></entry>
<entry><yt:videoId>bbbbbbbbbbb</yt:videoId><title>New</title><published>2026-11-20T00:00:00+00:00</published>
<media:group><media:community><media:statistics views="99"/></media:community></media:group></entry></feed>"""


class ParserTests(unittest.TestCase):
    def test_feed(self):
        vids = parse_feed(FEED, "short")
        self.assertEqual([v.video_id for v in vids], ["bbbbbbbbbbb", "aaaaaaaaaaa"])
        self.assertEqual(vids[0].views, 99)
        self.assertTrue(vids[0].url.startswith("https://www.youtube.com/shorts/"))

    def test_subscribers(self):
        self.assertEqual(parse_subscriber_text("5.08M subscribers"), 5_080_000)
        self.assertEqual(parse_subscriber_text("12.3K subscribers"), 12_300)
        self.assertEqual(parse_subscriber_text("850 subscribers"), 850)
        self.assertIsNone(parse_subscriber_text("hidden"))

    def test_channel_page(self):
        page = (f'<link rel="canonical" href="https://www.youtube.com/channel/{CID}">'
                '<meta property="og:title" content="Jo &amp; Co">"canonicalBaseUrl":"/@joco" 41.2K subscribers')
        info = parse_channel_page(page)
        self.assertEqual(info["channel_id"], CID)
        self.assertEqual(info["title"], "Jo & Co")
        self.assertEqual(info["handle"], "@joco")
        self.assertEqual(info["subscriber_count"], 41_200)

    def test_durations(self):
        page = '"contentId":"aaaaaaaaaaa" x "thumbnailBadgeViewModel":{"text":"1:02:03"} "contentId":"bbbbbbbbbbb" "thumbnailBadgeViewModel":{"text":"9:30"}'
        self.assertEqual(parse_video_durations(page), {"aaaaaaaaaaa": 3723, "bbbbbbbbbbb": 570})


class FakeProvider:
    name = "fake"

    def __init__(self, fail=False):
        self.fail = fail

    def research(self, parsed):
        if self.fail:
            raise ProviderError("boom")
        recent = lambda d: (NOW - timedelta(days=d)).isoformat()  # noqa: E731
        return ChannelResearch(
            provider="fake", channel_id=CID, handle="@joco", title="Jo Co", subscriber_count=41_200,
            long_videos=[Video(f"v{i:010d}", f"Ep {i}", f"https://www.youtube.com/watch?v=v{i:010d}", "long",
                               recent(i * 7), views=i * 100, duration_seconds=3600) for i in range(1, 6)],
            long_feed_ok=True, shorts_feed_ok=True,
            evidence=[{"url": "https://www.youtube.com/@joco/videos", "label": "Channel page"}],
        )


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.s = Store(":memory:")

    def test_research_and_duplicates(self):
        r = research_url(self.s, "https://www.youtube.com/@joco", provider=FakeProvider())
        lead = self.s.get_lead(r["lead_id"])
        self.assertEqual(lead["subscriber_count"], 41_200)
        self.assertEqual(lead["provenance"]["subscriber_count"], "verified")
        self.assertEqual(lead["shorts_per_month"], 0)
        self.assertEqual(lead["stage"], "pending")  # country/niche/format unknown
        self.assertEqual(len(lead["evidence"]), 1)
        self.assertEqual(len(lead["videos"]), 5)
        self.assertTrue(lead["best_sample_video_url"])
        # same handle, different casing -> duplicate before any fetch
        with self.assertRaises(DuplicateLead):
            research_url(self.s, "https://m.youtube.com/@JoCo", provider=FakeProvider())
        # channel-ID URL -> duplicate
        with self.assertRaises(DuplicateLead):
            research_url(self.s, f"https://www.youtube.com/channel/{CID}", provider=FakeProvider())
        kinds = [h["outcome"] for h in self.s.history()]
        self.assertEqual(kinds.count("duplicate"), 2)

    def test_manual_edit_defaults_to_estimate(self):
        r = research_url(self.s, "@joco", provider=FakeProvider())
        lead = self.s.update_lead(r["lead_id"], {"country": "US", "subscriber_count": 50_000})
        self.assertEqual(lead["provenance"]["country"], "estimate")
        self.assertEqual(lead["provenance"]["subscriber_count"], "estimate")
        lead = self.s.update_lead(r["lead_id"], {
            "niche": "podcast", "content_format": "podcast_interview"},
            {"country": "verified", "subscriber_count": "verified", "niche": "verified", "content_format": "verified"})
        self.assertEqual(lead["verification_status"], "verified")
        self.assertIn(lead["stage"], ("qualified", "rejected"))

    def test_provider_error_logged(self):
        with self.assertRaises(ResearchError):
            research_url(self.s, "@nobody", provider=FakeProvider(fail=True))
        self.assertEqual(self.s.history()[0]["outcome"], "error")
        self.assertEqual(self.s.list_leads(), [])

    def test_negative_rejected(self):
        r = research_url(self.s, "@joco", provider=FakeProvider())
        with self.assertRaises(ValueError):
            self.s.update_lead(r["lead_id"], {"subscriber_count": -5})

    def test_csv_and_snapshot(self):
        r = research_url(self.s, "@joco", provider=FakeProvider())
        self.s.update_lead(r["lead_id"], {"notes": "=HYPERLINK(\"x\")"})
        out = leads_to_csv(self.s.list_leads(), {r["lead_id"]: ["https://a"]})
        self.assertIn("Creator name", out.splitlines()[0])
        self.assertIn("'=HYPERLINK", out)
        snap = self.s.export_snapshot()
        s2 = Store(":memory:")
        s2.import_snapshot(json.loads(json.dumps(snap, default=str)))
        self.assertEqual(len(s2.list_leads()), 1)
        with self.assertRaises(RuntimeError):
            s2.import_snapshot(snap)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        os.environ["AQ_QUIET"] = "1"
        from leadengine.server import make_handler
        cls.store = Store(":memory:")
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.store))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method,
                                   headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_flow(self):
        st, body = self.req("POST", "/api/research", {"url": "https://www.youtube.com/@manualone", "fetch": False})
        self.assertEqual(st, 201, body)
        lid = json.loads(body)["lead_id"]
        st, body = self.req("POST", "/api/research", {"url": "youtube.com/@ManualOne", "fetch": False})
        self.assertEqual(st, 409)
        self.assertEqual(json.loads(body)["lead_id"], lid)
        st, body = self.req("PATCH", f"/api/leads/{lid}", {"fields": {"country": "US"}, "provenance": {"country": "verified"}})
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["provenance"]["country"], "verified")
        st, body = self.req("POST", f"/api/leads/{lid}/evidence", {"url": "javascript:alert(1)"})
        self.assertEqual(st, 400)
        st, body = self.req("POST", f"/api/leads/{lid}/evidence", {"url": "https://example.com/about", "label": "About"})
        self.assertEqual(st, 201)
        st, body = self.req("GET", "/api/leads?stage=pending")
        self.assertEqual(len(json.loads(body)), 1)
        st, body = self.req("GET", "/api/export.csv?stage=pending")
        self.assertEqual(st, 200)
        self.assertIn(b"https://example.com/about", body)
        st, body = self.req("POST", "/api/search", {"query": "business podcast"})
        self.assertEqual(json.loads(body)["mode"], "manual")
        st, body = self.req("GET", "/api/history")
        self.assertTrue(any(h["kind"] == "search" for h in json.loads(body)))
        st, body = self.req("GET", "/")
        self.assertIn(b"AQ Clipz", body)
        st, body = self.req("GET", "/../leadengine/db.py")
        self.assertNotIn(b"import sqlite3", body)  # falls back to index.html, never serves source


if __name__ == "__main__":
    unittest.main()
