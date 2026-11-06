# Project HexQ

## `bot/`: Telegram AI Chat Bot (Python, recommended)
A standalone Telegram bot powered by Google Gemini (gemini-3.5-flash). It needs no n8n and no public webhook. See [`bot/README.md`](bot/README.md).

---

## n8n version

## `workflows/telegram-ai-chatbot.json` — Telegram AI Chat Bot

```
Telegram Trigger ─► Route Message ─┬─ /start ─────► Send Welcome
                                   ├─ text ──► Show Typing ─► AI Agent ─┬─ ok ──► Send Reply
                                   │            (Claude + memory/chat)  └─ error ► Send Error Fallback
                                   └─ other (photo, sticker…) ─► Send Unsupported
```

### Setup
1. **Create the bot**: message `@BotFather` → `/newbot` → copy the token.
2. **n8n credentials**:
   - *Telegram API* → paste the bot token. Name it `Telegram Bot API`.
   - *Anthropic API* → your Anthropic key. Name it `Anthropic API`.
3. **Import**: Workflows → Import from File → `workflows/telegram-ai-chatbot.json`. Re-select the credentials on every Telegram node and the Anthropic model node.
4. **Activate** the workflow. The Telegram Trigger registers its webhook automatically.

### Requirements
- n8n must be reachable over **public HTTPS**, because Telegram only delivers to an HTTPS webhook. On a self-hosted instance, set `WEBHOOK_URL=https://your-domain/`.
- Only one webhook can exist per bot token. Telegram allows one webhook URL per bot, so if you test with "Listen for test event" you must re-activate the workflow afterwards.

### Behaviour and limits
| Concern | Handling |
|---|---|
| Conversation memory | Window Buffer Memory, keyed by `chat.id`, holding the last 10 turns. It lives in memory, so it is lost when n8n restarts. For persistence, swap it for Postgres or Redis Chat Memory with the same session key. |
| Non-text messages | Answered with a fixed "text only" message. |
| LLM failure or rate limit | The Agent node's error output sends a fallback message instead of failing silently. |
| Telegram 4096-character limit | The reply is truncated at 4000 characters. |
| Markdown parse errors | Replies are sent as plain text, with no `parse_mode`. |

### Optional hardening
- **Restrict to your users**: in the Telegram Trigger, set *Additional Fields → Restrict to Chat IDs / User IDs*.
- **Add tools**: attach tool sub-nodes (HTTP Request, Google Sheets, Calculator…) to the AI Agent's *Tool* input.
