"""Turn provider output into lead metrics and save it."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote_plus

from .db import DuplicateLead, Store
from .providers import ChannelResearch, ProviderError, get_provider
from .urls import ParsedYouTubeUrl, UrlError, canonical_channel_url, parse_youtube_url

WINDOW_DAYS = 90
DAYS_PER_MONTH = 30.44


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def upload_rate(dates: list[datetime], saturated: bool, now: datetime) -> float:
    """Uploads per month over the last 90 days.

    A saturated feed holds only the most recent N items. If all of them fall inside the
    window, the rate is measured over the span they cover, which is a lower bound."""
    in_window = [d for d in dates if (now - d).days <= WINDOW_DAYS]
    if saturated and dates and len(in_window) == len(dates):
        span = max((now - min(dates)).days, 1)
        return round(len(dates) / (span / DAYS_PER_MONTH), 1)
    return round(len(in_window) / (WINDOW_DAYS / DAYS_PER_MONTH), 1)


def derive_metrics(res: ChannelResearch, now: datetime | None = None) -> dict:
    """Compute scoring inputs from fetched data. A metric whose source failed stays None."""
    now = now or datetime.now(timezone.utc)
    m: dict = {}
    long_dates = sorted((d for d in (_parse_dt(v.published_at) for v in res.long_videos) if d), reverse=True)
    short_dates = sorted((d for d in (_parse_dt(v.published_at) for v in res.shorts) if d), reverse=True)

    if res.long_feed_ok:
        m["longform_per_month"] = upload_rate(long_dates, res.long_feed_saturated, now)
        if long_dates:
            m["days_since_last_longform"] = (now - long_dates[0]).days
            recent = [d for d in long_dates if (now - d).days <= WINDOW_DAYS]
            if len(recent) >= 2:
                gaps = [(a - b).days for a, b in zip(recent, recent[1:])]
                gaps.append((now - recent[0]).days)
                m["max_longform_gap_days"] = max(gaps)
    if res.shorts_feed_ok:
        m["shorts_per_month"] = upload_rate(short_dates, res.shorts_feed_saturated, now)
        if short_dates:
            m["days_since_last_short"] = (now - short_dates[0]).days

    durs = [v.duration_seconds for v in res.long_videos if v.duration_seconds]
    if len(durs) >= 3:
        m["avg_longform_minutes"] = round(sum(durs) / len(durs) / 60, 1)

    # Best sample: the most-viewed long-form video from the last 90 days, or else the latest one.
    recent_long = [v for v in res.long_videos
                   if (d := _parse_dt(v.published_at)) and (now - d).days <= WINDOW_DAYS]
    pool = recent_long or res.long_videos[:1]
    if pool:
        best = max(pool, key=lambda v: v.views or 0)
        m["_best_sample"] = best.url
    return m


class ResearchError(Exception):
    pass


def research_url(store: Store, raw_url: str, *, refresh_lead_id: int | None = None,
                 fetch: bool = True, provider=None) -> dict:
    """Research one channel. Returns {lead_id, status, warnings}. Raises DuplicateLead."""
    try:
        parsed: ParsedYouTubeUrl = parse_youtube_url(raw_url)
    except UrlError as e:
        store.log("research", raw_url, "error", str(e))
        raise ResearchError(str(e)) from e

    if refresh_lead_id is None:
        dup = store.find_duplicate(channel_key=parsed.key,
                                   channel_id=parsed.value if parsed.kind == "channel_id" else None,
                                   handle=parsed.value if parsed.kind == "handle" else None)
        if dup:
            store.log("research", raw_url, "duplicate", dup[1], dup[0])
            raise DuplicateLead(*dup)

    if not fetch:
        if parsed.kind == "video":
            raise ResearchError("A video URL needs a fetch to find its channel. Paste the channel URL instead.")
        lead_id = store.create_lead(parsed.key, parsed.fetch_url,
                                    handle=parsed.value if parsed.kind == "handle" else None,
                                    channel_id=parsed.value if parsed.kind == "channel_id" else None)
        store.log("manual", raw_url, "ok", "Added without fetching", lead_id)
        return {"lead_id": lead_id, "status": "created", "warnings": []}

    provider = provider or get_provider()
    try:
        res = provider.research(parsed)
    except ProviderError as e:
        store.log("refresh" if refresh_lead_id else "research", raw_url, "error", str(e), refresh_lead_id)
        raise ResearchError(str(e)) from e

    if refresh_lead_id is None:
        dup = store.find_duplicate(channel_id=res.channel_id, handle=res.handle)
        if dup:
            store.log("research", raw_url, "duplicate", dup[1], dup[0])
            raise DuplicateLead(*dup)
        key = f"id:{res.channel_id}"
        url = canonical_channel_url(res.channel_id, res.handle, parsed.fetch_url)
        lead_id = store.create_lead(key, url, channel_id=res.channel_id, handle=res.handle,
                                    creator_name=res.title)
        status = "created"
    else:
        dup = store.find_duplicate(channel_id=res.channel_id, exclude_id=refresh_lead_id)
        if dup:
            raise DuplicateLead(*dup)
        lead_id, status = refresh_lead_id, "refreshed"

    store.apply_research(lead_id, res.to_dict(), derive_metrics(res))
    lead = store.get_lead(lead_id, with_children=False)
    summary = (f"{res.title or 'channel'}: {len(res.long_videos)} long-form, {len(res.shorts)} Shorts in feeds; "
               f"score {lead['score']} ({lead['tier']}, provisional until verified)")
    store.log("refresh" if refresh_lead_id else "research", raw_url, "ok", summary, lead_id)
    return {"lead_id": lead_id, "status": status, "warnings": res.warnings}


def youtube_search_url(query: str) -> str:
    # sp=EgIQAg%3D%3D filters YouTube search results to channels.
    return f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp=EgIQAg%3D%3D"
