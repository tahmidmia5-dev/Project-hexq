# Telegram AI Chat Bot (Python + Claude)

A standalone bot with no n8n and no webhook. It uses long polling, so it runs anywhere: your laptop, a VPS, or a container.

```
Telegram ──poll──► bot.py ──► Claude API (claude-opus-5-5)
                     │  per-chat history (append-only, cached, auto-compacted)
                     └──► reply (split into ≤4096-char chunks)
```

## Run locally
```bash
cd bot
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in both tokens
set -a && . ./.env && set +a
python bot.py
```
Get the Telegram token from `@BotFather` with `/newbot`. Get the Anthropic key from console.anthropic.com.

## Run with Docker
```bash
cd bot
docker build -t tg-claude-bot .
docker run -d --restart unless-stopped --env-file .env --name tg-claude-bot tg-claude-bot
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
| `ANTHROPIC_API_KEY` | — | Required |
| `BOT_MODEL` | `claude-opus-5-5` | Use `claude-sonnet-5-5` or `claude-haiku-5-5` for lower cost |
| `BOT_EFFORT` | `low` | Thinking depth: `low` … `max`. Higher values are slower and cost more |
| `BOT_MAX_TOKENS` | `16000` | Per-reply output cap |
| `ALLOWED_USER_IDS` | *(empty = public)* | Comma-separated Telegram user IDs |
| `SYSTEM_PROMPT` | friendly assistant | The bot's personality and rules |

## How it behaves
- **Memory**: each chat keeps its own history. History is never edited, only appended to, so prompt caching stays valid and repeat turns are billed mostly at cache rates. Very long chats are summarised server-side by compaction, so they never overflow the context window.
- **Memory lives in RAM**: a restart wipes it. If that matters, persist `histories` to Redis or SQLite.
- **Refusals**: `fallbacks="default"` retries declined requests on a fallback model. If that model also declines, the user gets a polite message.
- **Errors**: the SDK retries rate limits, 5xx errors and network errors 3 times. If a request still fails, the user gets a friendly message and the failed turn is rolled back, so they can just resend.
- **Concurrency**: different chats are handled in parallel. Messages within one chat are handled in order.
- **Non-text messages** get a "text only" reply. The typing indicator stays on while Claude is thinking.
