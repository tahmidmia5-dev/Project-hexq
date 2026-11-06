# Telegram AI Chat Bot (Python + Gemini)

A standalone bot with no n8n and no webhook. It uses long polling, so it runs anywhere: your laptop, a VPS, or a container.

```
Telegram ──poll──► bot.py ──► Gemini API (gemini-3.5-flash)
                     │  per-chat history (last 30 exchanges)
                     └──► reply (split into ≤4096-char chunks)
```

## Run locally
```bash
cd bot
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in both keys
set -a && . ./.env && set +a
python bot.py
```
Get the Telegram token from `@BotFather` with `/newbot`. Get the Gemini key from aistudio.google.com: **Get API key** → **Create API key**.

## Run with Docker
```bash
cd bot
docker build -t tg-gemini-bot .
docker run -d --restart unless-stopped --env-file .env --name tg-gemini-bot tg-gemini-bot
```

## Commands
| Command | Effect |
|---|---|
| `/start` | Shows the welcome message |
| `/reset` | Clears this chat's memory |

## Configuration (env vars)
| Var | Default | Notes |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Required |
| `GEMINI_API_KEY` | — | Required (`GOOGLE_API_KEY` also works) |
| `BOT_MODEL` | `gemini-3.5-flash` | Google lists 3.5 Flash as deprecated. Its replacement is `gemini-3.6-flash` |
| `BOT_THINKING_LEVEL` | `low` | `minimal` \| `low` \| `medium` \| `high`. Higher is slower and costs more |
| `BOT_MAX_TOKENS` | `8192` | Per-reply output cap |
| `BOT_HISTORY_TURNS` | `30` | How many user+bot exchanges each chat remembers |
| `ALLOWED_USER_IDS` | *(empty = public)* | Comma-separated Telegram user IDs |
| `SYSTEM_PROMPT` | friendly assistant | The bot's personality and rules |

## How it behaves
- **Memory**: each chat keeps its own history, capped at the last `BOT_HISTORY_TURNS` exchanges. The model's full replies are stored, including the thought signatures Gemini 3 needs back.
- **Memory lives in RAM**: a restart wipes it.
- **Blocked or empty answers**: if a safety block or empty response stops an answer, the user gets a polite message and the turn is dropped.
- **Errors**: 429 and 5xx errors are retried 4 times with backoff. If a request still fails, the user gets a friendly message and the failed turn is rolled back, so they can just resend.
- **Concurrency**: different chats are handled in parallel. Messages within one chat are handled in order.
- **Non-text messages** get a "text only" reply. The typing indicator stays on while Gemini is thinking.
