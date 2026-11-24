"""Research providers.

Every provider returns a ChannelResearch. Only data read from YouTube goes into it.
Fields that could not be read stay None, and the reason is added to `warnings`.

* public_feed: the default. Needs no API key. Reads YouTube's public RSS feeds and channel pages.
* youtube_api: the official YouTube Data API v3. It turns on only when the
  YOUTUBE_API_KEY environment variable is set. See youtube_api.py.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field


@dataclass
class Video:
    video_id: str
    title: str
    url: str
    kind: str  # "long" | "short"
    published_at: str | None  # ISO-8601
    views: int | None = None
    duration_seconds: int | None = None


@dataclass
class ChannelResearch:
    provider: str
    channel_id: str | None = None
    handle: str | None = None
    title: str | None = None
    description: str | None = None
    subscriber_count: int | None = None
    subscriber_text: str | None = None
    country: str | None = None
    long_videos: list[Video] = field(default_factory=list)
    shorts: list[Video] = field(default_factory=list)
    # True when a feed returned its maximum number of items, so older uploads may be missing.
    long_feed_saturated: bool = False
    shorts_feed_saturated: bool = False
    long_feed_ok: bool = False
    shorts_feed_ok: bool = False
    evidence: list[dict] = field(default_factory=list)  # {"url", "label"}
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class ProviderError(RuntimeError):
    pass


def youtube_api_enabled() -> bool:
    return bool(os.environ.get("YOUTUBE_API_KEY"))


def get_provider():
    if youtube_api_enabled() and os.environ.get("AQ_PROVIDER", "").lower() != "public_feed":
        from .youtube_api import YouTubeApiProvider

        return YouTubeApiProvider(os.environ["YOUTUBE_API_KEY"])
    from .public_feed import PublicFeedProvider

    return PublicFeedProvider()
