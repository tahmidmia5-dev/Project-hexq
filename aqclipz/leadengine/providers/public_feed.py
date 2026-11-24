"""Keyless research provider.

Sources, all public:
* Channel page (https://www.youtube.com/@handle). Gives the channel ID, name, description,
  rounded subscriber count, and video lengths from the Videos tab.
* RSS feed for the long-form uploads playlist (UULF + id). Up to 15 latest long-form videos
  with exact publish dates and view counts.
* RSS feed for the Shorts playlist (UUSH + id). Up to 15 latest Shorts.

Country is not public without the API, so it is left for manual research.
Channel-page parsing is best effort. If YouTube changes its markup, the affected fields
come back None with a warning. Nothing is guessed.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from ..urls import ParsedYouTubeUrl
from . import ChannelResearch, ProviderError, Video

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
FEED_MAX = 15
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def _get(url: str, timeout: int = 20) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Cookie": "CONSENT=YES+1; SOCS=CAI",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ProviderError(f"Network error fetching {url}: {e}") from e


def parse_subscriber_text(text: str | None) -> int | None:
    """'5.08M subscribers' -> 5080000. Returns None when the text cannot be parsed."""
    if not text:
        return None
    m = re.search(r"([\d.,]+)\s*([KMB])?\s*subscribers?", text, re.I)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get((m.group(2) or "").upper(), 1)
    return int(round(num * mult))


def parse_duration(text: str) -> int | None:
    parts = text.split(":")
    if not all(p.isdigit() for p in parts) or not 2 <= len(parts) <= 3:
        return None
    secs = 0
    for p in parts:
        secs = secs * 60 + int(p)
    return secs


def parse_channel_page(page: str) -> dict:
    out: dict = {}
    m = re.search(r'"externalId":"(UC[0-9A-Za-z_-]{22})"', page) or re.search(
        r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[0-9A-Za-z_-]{22})"', page
    )
    if m:
        out["channel_id"] = m.group(1)
    m = re.search(r'"canonicalBaseUrl":"/(@[^"]+)"', page)
    if m:
        out["handle"] = m.group(1)
    m = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    if m:
        out["title"] = html.unescape(m.group(1))
    m = re.search(r'<meta property="og:description" content="([^"]*)"', page)
    if m:
        out["description"] = html.unescape(m.group(1))
    m = re.search(r"([\d.,]+[KMB]?) subscribers", page)
    if m:
        out["subscriber_text"] = m.group(0)
        out["subscriber_count"] = parse_subscriber_text(m.group(0))
    return out


def parse_video_durations(page: str) -> dict[str, int]:
    """Map video ID to duration from the Videos tab. Best effort.

    Each video's ID comes before its duration badge in the page JSON, so the parser
    takes the nearest preceding ID for each badge.
    """
    out: dict[str, int] = {}
    pattern = re.compile(
        r'"(?:videoId|contentId)":"([0-9A-Za-z_-]{11})"|"thumbnailBadgeViewModel":\{"text":"(\d{1,2}(?::\d{2}){1,2})"'
    )
    last_id = None
    for m in pattern.finditer(page):
        if m.group(1):
            last_id = m.group(1)
        elif last_id and last_id not in out:
            d = parse_duration(m.group(2))
            if d is not None:
                out[last_id] = d
    return out


def parse_feed(xml_text: str, kind: str) -> list[Video]:
    root = ET.fromstring(xml_text)
    videos = []
    for e in root.findall("a:entry", NS):
        vid = e.findtext("yt:videoId", default="", namespaces=NS)
        if not vid:
            continue
        title = e.findtext("a:title", default="", namespaces=NS)
        published = e.findtext("a:published", default=None, namespaces=NS)
        stats = e.find("media:group/media:community/media:statistics", NS)
        views = int(stats.get("views")) if stats is not None and stats.get("views", "").isdigit() else None
        url = f"https://www.youtube.com/shorts/{vid}" if kind == "short" else f"https://www.youtube.com/watch?v={vid}"
        videos.append(Video(vid, title, url, kind, published, views))
    videos.sort(key=lambda v: v.published_at or "", reverse=True)
    return videos


class PublicFeedProvider:
    name = "public_feed"

    def resolve_channel_from_video(self, video_id: str) -> str:
        status, page = _get(f"https://www.youtube.com/watch?v={video_id}")
        if status != 200:
            raise ProviderError(f"Video page returned HTTP {status}")
        m = re.search(r'"channelId":"(UC[0-9A-Za-z_-]{22})"', page)
        if not m:
            raise ProviderError("Could not find the channel for this video")
        return m.group(1)

    def research(self, parsed: ParsedYouTubeUrl) -> ChannelResearch:
        res = ChannelResearch(provider=self.name)
        if parsed.kind == "video":
            channel_id = self.resolve_channel_from_video(parsed.value)
            base = f"https://www.youtube.com/channel/{channel_id}"
            res.evidence.append({"url": parsed.fetch_url, "label": "Video used to find the channel"})
        else:
            base = parsed.fetch_url

        videos_url = base.rstrip("/") + "/videos"
        status, page = _get(videos_url)
        if status == 404:
            raise ProviderError("Channel not found (HTTP 404)")
        if status != 200 or not page:
            raise ProviderError(f"Channel page returned HTTP {status}")
        info = parse_channel_page(page)
        if not info.get("channel_id"):
            raise ProviderError("Could not read the channel ID from the channel page")
        res.channel_id = info["channel_id"]
        res.handle = info.get("handle")
        res.title = info.get("title")
        res.description = info.get("description")
        res.subscriber_count = info.get("subscriber_count")
        res.subscriber_text = info.get("subscriber_text")
        res.evidence.append({"url": videos_url, "label": "Channel page (Videos tab)"})
        if res.subscriber_count is None:
            res.warnings.append("Subscriber count not shown on the channel page (it may be hidden)")
        res.warnings.append("Country is not available without the YouTube API. Check it manually.")
        durations = parse_video_durations(page)

        suffix = res.channel_id[2:]
        for prefix, kind in (("UULF", "long"), ("UUSH", "short")):
            feed_url = f"https://www.youtube.com/feeds/videos.xml?playlist_id={prefix}{suffix}"
            fstatus, xml_text = _get(feed_url)
            label = "Long-form uploads feed" if kind == "long" else "Shorts feed"
            if fstatus == 404:
                # YouTube returns 404 when the channel has no videos of this kind.
                vids = []
                ok = True
                res.evidence.append({"url": feed_url, "label": f"{label} (empty: HTTP 404)"})
            elif fstatus == 200 and xml_text:
                try:
                    vids = parse_feed(xml_text, kind)
                    ok = True
                    res.evidence.append({"url": feed_url, "label": label})
                except ET.ParseError:
                    vids, ok = [], False
                    res.warnings.append(f"{label} could not be parsed")
            else:
                vids, ok = [], False
                res.warnings.append(f"{label} returned HTTP {fstatus}")
            if kind == "long":
                for v in vids:
                    v.duration_seconds = durations.get(v.video_id)
                res.long_videos, res.long_feed_ok = vids, ok
                res.long_feed_saturated = len(vids) >= FEED_MAX
            else:
                res.shorts, res.shorts_feed_ok = vids, ok
                res.shorts_feed_saturated = len(vids) >= FEED_MAX
        return res

    def search_channels(self, query: str) -> list[dict]:
        raise ProviderError("Search needs the YouTube API. Open the search link and paste channel URLs instead.")
