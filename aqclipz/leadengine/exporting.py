"""CSV export."""

from __future__ import annotations

import csv
import io

COLUMNS = [
    ("creator_name", "Creator name"),
    ("channel_url", "YouTube channel URL"),
    ("subscriber_count", "Subscriber count"),
    ("niche", "Niche"),
    ("country", "Country"),
    ("longform_per_month", "Long-form uploads / month"),
    ("shorts_per_month", "Shorts / month"),
    ("score", "Qualification score"),
    ("tier", "Tier"),
    ("stage", "List"),
    ("qualification_explanation", "Qualification explanation"),
    ("best_sample_video_url", "Best sample video URL"),
    ("research_date", "Research date"),
    ("verification_status", "Verification status"),
    ("estimated_fields", "Estimated fields"),
    ("evidence_urls", "Evidence URLs"),
    ("website_url", "Website"),
    ("notes", "Notes"),
]


def _safe(v) -> str:
    """Stop spreadsheet formula injection from text fetched from YouTube."""
    s = "" if v is None else str(v)
    if s[:1] in ("=", "+", "-", "@", "\t", "\r"):
        s = "'" + s
    return s


def leads_to_csv(leads: list[dict], evidence_by_lead: dict[int, list[str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([label for _, label in COLUMNS])
    for lead in leads:
        prov = lead.get("provenance") or {}
        row = dict(lead)
        row["estimated_fields"] = "; ".join(sorted(k for k, v in prov.items() if v == "estimate"))
        row["evidence_urls"] = " | ".join(evidence_by_lead.get(lead["id"], []))
        w.writerow([_safe(row.get(k)) for k, _ in COLUMNS])
    return buf.getvalue()
