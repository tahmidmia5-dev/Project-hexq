"""YouTube Data API v3 provider. OFF by default.

To enable it:
1. Create an API key in Google Cloud and enable "YouTube Data API v3". The free quota is
   10,000 units/day. A search costs 100 units; each other call here costs 1.
2. Set YOUTUBE_API_KEY in the environment. Never commit it or send it to the browser.
3. If you run in Claude Code Cloud, the network policy must allow www.googleapis.com.

When enabled, this provider replaces public_feed. The API adds an exact subscriber count,
the channel's self-reported country, exact durations, and real creator search.
This provider has NOT been run against the live API yet, because no key was available
when Phase 1 was built. Test it before you rely on it.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from ..urls import ParsedYouTubeUrl
from . import ChannelResearch, ProviderError, Video

API = "https://www.googleapis.com/youtube/v3"


def iso_duration_to_seconds(text: str) -> int | None:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", text or "")
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


class YouTubeApiProvider:
    name = "youtube_api"

    def __init__(self, key: str):
        self._key = key

    def _call(self, endpoint: str, **params) -> dict:
        params["key"] = self._key
        url = f"{API}/{endpoint}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            # Never echo the URL: it contains the key.
            raise ProviderError(f"YouTube API {endpoint} returned HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ProviderError(f"YouTube API network error: {type(e).__name__}") from None

    def _channel_id(self, parsed: ParsedYouTubeUrl) -> str:
        if parsed.kind == "channel_id":
            return parsed.value
        if parsed.kind == "handle":
            data = self._call("channels", part="id", forHandle=parsed.value)
        elif parsed.kind == "user":
            data = self._call("channels", part="id", forUsername=parsed.value)
        elif parsed.kind == "video":
            data = self._call("videos", part="snippet", id=parsed.value)
            items = data.get("items") or []
            if not items:
                raise ProviderError("Video not found")
            return items[0]["snippet"]["channelId"]
        else:
            raise ProviderError("/c/ custom URLs cannot be resolved by the API. Use the @handle URL.")
        items = data.get("items") or []
        if not items:
            raise ProviderError("Channel not found")
        return items[0]["id"]

    def _playlist(self, playlist_id: str, kind: str) -> tuple[list[Video], bool]:
        try:
            data = self._call("playlistItems", part="contentDetails,snippet", playlistId=playlist_id, maxResults=50)
        except ProviderError as e:
            if "HTTP 404" in str(e):
                return [], True
            raise
        vids = []
        for it in data.get("items", []):
            vid = it["contentDetails"]["videoId"]
            url = f"https://www.youtube.com/shorts/{vid}" if kind == "short" else f"https://www.youtube.com/watch?v={vid}"
            vids.append(Video(vid, it["snippet"].get("title", ""), url, kind, it["contentDetails"].get("videoPublishedAt")))
        return vids, len(vids) >= 50

    def research(self, parsed: ParsedYouTubeUrl) -> ChannelResearch:
        cid = self._channel_id(parsed)
        data = self._call("channels", part="snippet,statistics", id=cid)
        items = data.get("items") or []
        if not items:
            raise ProviderError("Channel not found")
        ch = items[0]
        sn, st = ch.get("snippet", {}), ch.get("statistics", {})
        res = ChannelResearch(provider=self.name, channel_id=cid)
        res.title = sn.get("title")
        res.description = sn.get("description")
        res.handle = sn.get("customUrl") if (sn.get("customUrl") or "").startswith("@") else None
        res.country = sn.get("country")  # self-reported by the creator; may be absent
        if not st.get("hiddenSubscriberCount") and st.get("subscriberCount"):
            res.subscriber_count = int(st["subscriberCount"])
        res.evidence.append({"url": f"https://www.youtube.com/channel/{cid}", "label": "Channel (YouTube Data API)"})
        if not res.country:
            res.warnings.append("The creator has not set a country on the channel")

        res.long_videos, res.long_feed_saturated = self._playlist("UULF" + cid[2:], "long")
        res.shorts, res.shorts_feed_saturated = self._playlist("UUSH" + cid[2:], "short")
        res.long_feed_ok = res.shorts_feed_ok = True
        ids = [v.video_id for v in res.long_videos]
        for i in range(0, len(ids), 50):
            det = self._call("videos", part="contentDetails,statistics", id=",".join(ids[i : i + 50]))
            by_id = {d["id"]: d for d in det.get("items", [])}
            for v in res.long_videos:
                d = by_id.get(v.video_id)
                if d:
                    v.duration_seconds = iso_duration_to_seconds(d["contentDetails"].get("duration", ""))
                    if d.get("statistics", {}).get("viewCount"):
                        v.views = int(d["statistics"]["viewCount"])
        res.long_videos.sort(key=lambda v: v.published_at or "", reverse=True)
        res.shorts.sort(key=lambda v: v.published_at or "", reverse=True)
        return res

    def search_channels(self, query: str, max_results: int = 15) -> list[dict]:
        data = self._call(
            "search", part="snippet", type="channel", q=query, maxResults=max_results, regionCode="US"
        )
        return [
            {
                "channel_id": it["snippet"]["channelId"],
                "title": it["snippet"].get("title"),
                "description": it["snippet"].get("description"),
                "url": f"https://www.youtube.com/channel/{it['snippet']['channelId']}",
            }
            for it in data.get("items", [])
        ]
