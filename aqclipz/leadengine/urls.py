"""Parse and canonicalise YouTube URLs. Used for research input and duplicate detection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

CHANNEL_ID_RE = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
HANDLE_RE = re.compile(r"^@[0-9A-Za-z._-]{2,100}$")
VIDEO_ID_RE = re.compile(r"^[0-9A-Za-z_-]{11}$")

YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}


@dataclass
class ParsedYouTubeUrl:
    kind: str  # "channel_id" | "handle" | "custom" | "user" | "video"
    value: str
    original: str

    @property
    def key(self) -> str:
        """Canonical key used for duplicate detection before the channel ID is known."""
        if self.kind == "channel_id":
            return f"id:{self.value}"
        if self.kind == "handle":
            return f"handle:{self.value.lower()}"
        return f"{self.kind}:{self.value.lower()}"

    @property
    def fetch_url(self) -> str:
        if self.kind == "channel_id":
            return f"https://www.youtube.com/channel/{self.value}"
        if self.kind == "handle":
            return f"https://www.youtube.com/{self.value}"
        if self.kind == "custom":
            return f"https://www.youtube.com/c/{self.value}"
        if self.kind == "user":
            return f"https://www.youtube.com/user/{self.value}"
        return f"https://www.youtube.com/watch?v={self.value}"


class UrlError(ValueError):
    pass


def parse_youtube_url(raw: str) -> ParsedYouTubeUrl:
    text = (raw or "").strip()
    if not text:
        raise UrlError("Empty URL")
    # Bare handle or channel id
    if HANDLE_RE.match(text):
        return ParsedYouTubeUrl("handle", text, raw)
    if CHANNEL_ID_RE.match(text):
        return ParsedYouTubeUrl("channel_id", text, raw)
    if not re.match(r"^https?://", text, re.I):
        text = "https://" + text
    u = urlparse(text)
    host = (u.hostname or "").lower()
    if host not in YT_HOSTS:
        raise UrlError("Not a YouTube URL")
    parts = [p for p in u.path.split("/") if p]
    if host == "youtu.be":
        if parts and VIDEO_ID_RE.match(parts[0]):
            return ParsedYouTubeUrl("video", parts[0], raw)
        raise UrlError("Unrecognised youtu.be link")
    if not parts:
        raise UrlError("URL does not point to a channel or video")
    head = parts[0]
    if head.startswith("@") and HANDLE_RE.match(head):
        return ParsedYouTubeUrl("handle", head, raw)
    if head == "channel" and len(parts) > 1 and CHANNEL_ID_RE.match(parts[1]):
        return ParsedYouTubeUrl("channel_id", parts[1], raw)
    if head == "c" and len(parts) > 1:
        return ParsedYouTubeUrl("custom", parts[1], raw)
    if head == "user" and len(parts) > 1:
        return ParsedYouTubeUrl("user", parts[1], raw)
    if head == "watch":
        v = parse_qs(u.query).get("v", [""])[0]
        if VIDEO_ID_RE.match(v):
            return ParsedYouTubeUrl("video", v, raw)
    if head in ("shorts", "live", "embed") and len(parts) > 1 and VIDEO_ID_RE.match(parts[1]):
        return ParsedYouTubeUrl("video", parts[1], raw)
    raise UrlError("URL does not point to a channel or video")


def canonical_channel_url(channel_id: str | None, handle: str | None, fallback: str) -> str:
    if handle:
        return f"https://www.youtube.com/{handle}"
    if channel_id:
        return f"https://www.youtube.com/channel/{channel_id}"
    return fallback


def normalise_name(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())
