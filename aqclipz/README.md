# AQ Clipz Lead Engine (Phase 1)

Finds and qualifies YouTube creators who could become AQ Clipz clients. It never contacts anyone.

## Run it

```bash
cd aqclipz
python3 run.py                  # http://127.0.0.1:8000
python3 -m unittest discover -s tests
```

Needs Python 3.10 or later and nothing else: no pip installs, no API keys, no paid services.

| Env var | Default | Purpose |
|---|---|---|
| `PORT` / `HOST` | `8000` / `127.0.0.1` | Where the server listens |
| `AQ_DB_PATH` | `aqclipz/data/aqclipz.db` | SQLite file |
| `AQ_ACCESS_TOKEN` | unset | If set, every `/api` call needs the `X-Access-Token` header or an `aq_token` cookie. Set it before hosting the app anywhere public. |
| `YOUTUBE_API_KEY` | unset | Turns on the YouTube Data API provider. Off by default. |

## Architecture

```
static/            index.html, app.css, app.js   (dashboard: vanilla JS, no build step)
leadengine/
  server.py        JSON API and static files     (http.server from the standard library)
  db.py            SQLite: leads, evidence, videos, search_history
  scoring.py       deterministic 100-point qualification
  research.py      provider output -> metrics -> lead record
  exporting.py     CSV export
  urls.py          YouTube URL parsing and duplicate keys
  providers/
    public_feed.py default, no key: public channel page and RSS feeds
    youtube_api.py optional, off unless YOUTUBE_API_KEY is set
run.py             entry point and snapshot commands
```

### How research works without an API key
For each channel, `public_feed` reads three public sources:

1. **Channel page** (`/@handle/videos`): channel ID, name, rounded subscriber count, and video lengths.
2. **Long-form feed** (`feeds/videos.xml?playlist_id=UULF…`): the latest 15 long-form uploads, with exact dates and views.
3. **Shorts feed** (`feeds/videos.xml?playlist_id=UUSH…`): the latest 15 Shorts.

The provider computes uploads per month over the last 90 days, days since the last upload, the longest gap between uploads, and average length. It picks the most-viewed recent long-form video as the best sample. Every source URL is saved as evidence.

**What it cannot get without the API:** country, niche, format, offers, contact details and sponsors. You research these by hand on the lead page.

Search without an API key logs your query and opens YouTube's channel search, so you can copy channel links back in. With `YOUTUBE_API_KEY` set, search returns channels in the app.

## Data integrity rules (enforced in code)
- **Nothing is invented.** A field with no data stays empty and scores 0 for its part. The explanation lists every missing item.
- **Verified vs estimate.** Each scoring field records where its value came from:
  - Values read from YouTube are marked **verified**, and their source URL is saved as evidence.
  - A value you type is an **estimate** until you switch it to Verified.
- **Pending verification.** A lead leaves Pending only when all six critical fields are verified: subscribers, long-form rate, Shorts rate, format, niche and country. Until then its score is shown as *provisional*.
- **Duplicates** are caught by channel ID, @handle (any casing) or URL, including video links that resolve to a channel already saved. Leads with the same name are flagged too.
- **CSV export** guards against formula injection and includes an "Estimated fields" column.

## Scoring (max 100)
| Criterion | Max | Rule summary |
|---|---|---|
| Long-form consistency | 20 | ≥8/mo 20 · ≥4 17 · ≥2 13 · ≥1 8 · >0 4. Capped at 6 if the last upload was over 45 days ago. -3 if any gap is longer than 30 days. |
| Short-form gap | 25 | 0 Shorts/mo 25 · <2 22 · <4 18 · <8 13 · <16 8 · <30 4 · else 1. Scores 0 if there are no recent long-form uploads. |
| Clipping potential | 20 | Format (interview 8 … other 1), length (≥45 min 6, ≥20 5, ≥10 3), quotable moments +3, guests +3 |
| Business potential | 15 | Sells an offer +6, subscriber band 0–5 (sweet spot 50K–500K), sponsors +4 |
| Contact accessibility | 10 | Business email 5, website 3, booking link 2 |
| Niche fit | 10 | Core niche 6 or adjacent niche 3, plus US-based 4 |

Tiers: 85–100 Excellent · 70–84 Qualified · 55–69 Possible · under 55 Reject.
Lists: **Qualified** = verified and scoring 55 or more · **Pending** = not fully verified · **Rejected** = verified and under 55, or rejected by hand.

## Keeping data in Claude Code Cloud
Cloud containers are temporary, and the SQLite file is gitignored. To keep your leads between sessions, export a snapshot and commit it to this private repo:

```bash
python3 run.py snapshot-export data/snapshot.json   # then commit it
python3 run.py snapshot-import data/snapshot.json   # in a new session, on an empty DB
```

For day-to-day use from a phone, the app needs a host with a persistent disk. Deployment is not done yet and needs your approval.
