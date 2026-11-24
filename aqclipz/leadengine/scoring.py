"""Deterministic lead qualification.

Rules:
* Each criterion has a fixed maximum (total 100).
* A missing field scores 0 for its part and is listed as missing. Nothing is assumed.
* A value marked "estimate" counts toward the score, but the lead stays in Pending
  verification until every critical field is verified.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_POINTS = {
    "longform_consistency": 20,
    "shortform_gap": 25,
    "clipping_potential": 20,
    "business_potential": 15,
    "contact_accessibility": 10,
    "niche_fit": 10,
}

LABELS = {
    "longform_consistency": "Long-form consistency",
    "shortform_gap": "Short-form content gap",
    "clipping_potential": "Clipping potential",
    "business_potential": "Business potential",
    "contact_accessibility": "Contact accessibility",
    "niche_fit": "Niche fit",
}

# Fields that must be verified before a lead leaves Pending verification.
CRITICAL_FIELDS = [
    "subscriber_count",
    "longform_per_month",
    "shorts_per_month",
    "content_format",
    "niche",
    "country",
]

CONTENT_FORMATS = {
    "podcast_interview": ("Podcast / interview (guests)", 8),
    "solo_podcast": ("Solo podcast / commentary", 7),
    "talking_head": ("Talking-head education", 6),
    "presentation": ("Webinar / presentation / workshop", 5),
    "vlog": ("Vlog / lifestyle", 3),
    "other": ("Other", 1),
}

TARGET_NICHES = {
    "podcast": "Podcast",
    "entrepreneurship": "Entrepreneurship",
    "business_education": "Business education",
    "coaching": "Coaching",
    "consulting": "Consulting",
    "interviews": "Interview-based",
    "personal_brand": "Personal brand",
}
ADJACENT_NICHES = {
    "marketing": "Marketing",
    "sales": "Sales",
    "finance": "Finance / investing",
    "real_estate": "Real estate",
    "leadership": "Leadership",
}
OTHER_NICHES = {
    "health_wellness": "Health & wellness",
    "tech": "Tech",
    "other": "Other",
}
ALL_NICHES = {**TARGET_NICHES, **ADJACENT_NICHES, **OTHER_NICHES}


def tier_for(score: int) -> str:
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Qualified"
    if score >= 55:
        return "Possible"
    return "Reject"


@dataclass
class Part:
    key: str
    points: int = 0
    reasons: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def max(self) -> int:
        return MAX_POINTS[self.key]

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": LABELS[self.key],
            "points": self.points,
            "max": self.max,
            "reasons": self.reasons,
            "missing": self.missing,
        }


def _known(v) -> bool:
    return v is not None and v != ""


def _tag(lead: dict, fieldname: str) -> str:
    prov = (lead.get("provenance") or {}).get(fieldname)
    return " (estimate)" if prov == "estimate" else ""


def _fmt_rate(r: float) -> str:
    return f"{r:.1f}".rstrip("0").rstrip(".")


def score_longform(lead: dict) -> Part:
    p = Part("longform_consistency")
    rate = lead.get("longform_per_month")
    if not _known(rate):
        p.missing.append("Long-form uploads per month")
        return p
    rate = float(rate)
    if rate >= 8:
        pts = 20
    elif rate >= 4:
        pts = 17
    elif rate >= 2:
        pts = 13
    elif rate >= 1:
        pts = 8
    elif rate > 0:
        pts = 4
    else:
        pts = 0
    p.reasons.append(f"{_fmt_rate(rate)} long-form uploads/month{_tag(lead, 'longform_per_month')}")
    days = lead.get("days_since_last_longform")
    if _known(days):
        days = int(days)
        if days > 45 and pts > 6:
            pts = 6
            p.reasons.append(f"Capped: last long-form upload was {days} days ago")
        else:
            p.reasons.append(f"Last long-form upload {days} days ago")
    gap = lead.get("max_longform_gap_days")
    if _known(gap) and int(gap) > 30 and pts > 0:
        pts = max(0, pts - 3)
        p.reasons.append(f"-3: irregular schedule (longest gap {int(gap)} days)")
    p.points = pts
    return p


def score_shortform_gap(lead: dict) -> Part:
    p = Part("shortform_gap")
    shorts = lead.get("shorts_per_month")
    longf = lead.get("longform_per_month")
    if not _known(shorts):
        p.missing.append("Shorts per month")
    if not _known(longf):
        p.missing.append("Long-form uploads per month")
    if p.missing:
        return p
    shorts, longf = float(shorts), float(longf)
    if longf <= 0:
        p.reasons.append("No recent long-form uploads, so there is no source material to clip")
        return p
    if shorts == 0:
        pts = 25
    elif shorts < 2:
        pts = 22
    elif shorts < 4:
        pts = 18
    elif shorts < 8:
        pts = 13
    elif shorts < 16:
        pts = 8
    elif shorts < 30:
        pts = 4
    else:
        pts = 1
    p.reasons.append(
        f"{_fmt_rate(shorts)} Shorts/month{_tag(lead, 'shorts_per_month')} vs "
        f"{_fmt_rate(longf)} long-form/month"
    )
    p.points = pts
    return p


def score_clipping(lead: dict) -> Part:
    p = Part("clipping_potential")
    fmt = lead.get("content_format")
    if fmt in CONTENT_FORMATS:
        label, pts = CONTENT_FORMATS[fmt]
        p.points += pts
        p.reasons.append(f"Format: {label}{_tag(lead, 'content_format')} (+{pts})")
    else:
        p.missing.append("Content format")
    mins = lead.get("avg_longform_minutes")
    if _known(mins):
        mins = float(mins)
        pts = 6 if mins >= 45 else 5 if mins >= 20 else 3 if mins >= 10 else 1
        p.points += pts
        p.reasons.append(f"Average long-form length {mins:.0f} min{_tag(lead, 'avg_longform_minutes')} (+{pts})")
    else:
        p.missing.append("Average video length")
    if lead.get("quotable_moments") == 1:
        p.points += 3
        p.reasons.append("Quotable / hook-worthy moments observed (+3)")
    elif lead.get("quotable_moments") is None:
        p.missing.append("Quotable moments check")
    if lead.get("guest_variety") == 1:
        p.points += 3
        p.reasons.append("Multiple speakers or rotating guests (+3)")
    elif lead.get("guest_variety") is None:
        p.missing.append("Guest / speaker check")
    p.points = min(p.points, p.max)
    return p


def score_business(lead: dict) -> Part:
    p = Part("business_potential")
    if lead.get("has_offer") == 1:
        p.points += 6
        p.reasons.append("Sells an offer (course, coaching, service, product) (+6)")
    elif lead.get("has_offer") is None:
        p.missing.append("Offer / product check")
    subs = lead.get("subscriber_count")
    if _known(subs):
        subs = int(subs)
        if subs < 1_000:
            pts = 0
        elif subs < 5_000:
            pts = 1
        elif subs < 10_000:
            pts = 2
        elif subs < 50_000:
            pts = 4
        elif subs < 500_000:
            pts = 5
        elif subs < 2_000_000:
            pts = 3
        else:
            pts = 1
        p.points += pts
        p.reasons.append(f"{subs:,} subscribers{_tag(lead, 'subscriber_count')} (+{pts})")
    else:
        p.missing.append("Subscriber count")
    if lead.get("has_sponsors") == 1:
        p.points += 4
        p.reasons.append("Sponsors or monetisation visible (+4)")
    elif lead.get("has_sponsors") is None:
        p.missing.append("Sponsorship check")
    p.points = min(p.points, p.max)
    return p


def score_contact(lead: dict) -> Part:
    p = Part("contact_accessibility")
    for fieldname, pts, label in (
        ("has_business_email", 5, "Public business email"),
        ("has_website", 3, "Website with contact info"),
        ("has_booking_link", 2, "Booking / inquiry link"),
    ):
        v = lead.get(fieldname)
        if v == 1:
            p.points += pts
            p.reasons.append(f"{label} (+{pts})")
        elif v is None:
            p.missing.append(label)
    return p


def score_niche(lead: dict) -> Part:
    p = Part("niche_fit")
    niche = lead.get("niche")
    if niche in TARGET_NICHES:
        p.points += 6
        p.reasons.append(f"Core niche: {TARGET_NICHES[niche]}{_tag(lead, 'niche')} (+6)")
    elif niche in ADJACENT_NICHES:
        p.points += 3
        p.reasons.append(f"Adjacent niche: {ADJACENT_NICHES[niche]}{_tag(lead, 'niche')} (+3)")
    elif niche in OTHER_NICHES:
        p.reasons.append(f"Outside target niches: {OTHER_NICHES[niche]}")
    else:
        p.missing.append("Niche")
    country = (lead.get("country") or "").strip().upper()
    if country in ("US", "USA", "UNITED STATES"):
        p.points += 4
        p.reasons.append(f"US-based{_tag(lead, 'country')} (+4)")
    elif country:
        p.reasons.append(f"Not US-based ({lead.get('country')})")
    else:
        p.missing.append("Country")
    return p


def verification_status(lead: dict) -> str:
    prov = lead.get("provenance") or {}
    verified = [f for f in CRITICAL_FIELDS if _known(lead.get(f)) and prov.get(f) == "verified"]
    if len(verified) == len(CRITICAL_FIELDS):
        return "verified"
    if verified:
        return "partial"
    return "unverified"


def qualify(lead: dict) -> dict:
    parts = [
        score_longform(lead),
        score_shortform_gap(lead),
        score_clipping(lead),
        score_business(lead),
        score_contact(lead),
        score_niche(lead),
    ]
    total = sum(p.points for p in parts)
    tier = tier_for(total)
    vstatus = verification_status(lead)
    if lead.get("manual_reject"):
        stage = "rejected"
    elif vstatus != "verified":
        stage = "pending"
    elif total >= 55:
        stage = "qualified"
    else:
        stage = "rejected"

    missing = [m for p in parts for m in p.missing]
    prov = lead.get("provenance") or {}
    estimates = [f for f, v in prov.items() if v == "estimate" and _known(lead.get(f))]
    lines = [f"{total}/100 ({tier})."]
    for p in parts:
        detail = "; ".join(p.reasons) if p.reasons else "no data"
        lines.append(f"{LABELS[p.key]} {p.points}/{p.max}: {detail}.")
    if missing:
        lines.append("Missing (scored 0): " + ", ".join(dict.fromkeys(missing)) + ".")
    if estimates:
        lines.append("Based partly on estimates: " + ", ".join(estimates) + ".")
    if vstatus != "verified":
        unverified = [f for f in CRITICAL_FIELDS if prov.get(f) != "verified" or not _known(lead.get(f))]
        lines.append("Needs verification: " + ", ".join(unverified) + ".")
    return {
        "score": total,
        "tier": tier,
        "stage": stage,
        "verification_status": vstatus,
        "breakdown": [p.as_dict() for p in parts],
        "explanation": " ".join(lines),
    }
