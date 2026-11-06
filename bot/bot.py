"""Telegram AI chat bot powered by Claude.

Long-polling (no public HTTPS / webhook needed). One conversation per Telegram chat.
Config comes from environment variables -- see .env.example.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict

import anthropic
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
for _name in ("httpx", "httpx2"):
    logging.getLogger(_name).setLevel(logging.WARNING)
log = logging.getLogger("bot")

# ---------------------------------------------------------------- config ----
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
MODEL = os.environ.get("BOT_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("BOT_EFFORT", "low")  # chat works well at low effort
MAX_TOKENS = int(os.environ.get("BOT_MAX_TOKENS", "16000"))
ALLOWED_USER_IDS = {
    int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x
}
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "You are a helpful, friendly assistant chatting with a user on Telegram. "
    "Reply in plain text: no Markdown tables or headings. Keep answers short "
    "unless the user asks for detail. If you don't know something, say so.",
)

TELEGRAM_LIMIT = 4096
BETAS = [
    "server-side-fallback-2026-07-01",  # fallbacks="default": retry declined requests on a fallback model
    "compact-2026-01-12",  # server-side compaction keeps very long chats inside the context window
]

# Reads ANTHROPIC_API_KEY from the environment. The SDK retries 429/5xx/network errors.
claude = anthropic.AsyncAnthropic(max_retries=3, timeout=120.0)

# chat_id -> message history. Append-only: earlier turns are never edited, which keeps
# prompt caching and thinking blocks valid. Compaction handles growth server-side.
histories: dict[int, list[dict]] = defaultdict(list)
locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


# --------------------------------------------------------------- helpers ----
def is_allowed(update: Update) -> bool:
    user = update.effective_user
    return not ALLOWED_USER_IDS or (user is not None and user.id in ALLOWED_USER_IDS)


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split on paragraph/line/space boundaries so each chunk fits Telegram's limit."""
    chunks: list[str] = []
    while len(text) > limit:
        cut = max(text.rfind(sep, 0, limit) for sep in ("\n\n", "\n", " "))
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


async def keep_typing(chat, stop: asyncio.Event) -> None:
    """Telegram's typing indicator lasts ~5s; refresh it until the reply is ready."""
    while not stop.is_set():
        try:
            await chat.send_action(ChatAction.TYPING)
        except Exception:  # typing is cosmetic; never fail the reply over it
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=4.5)
        except asyncio.TimeoutError:
            pass


async def ask_claude(chat_id: int, user_text: str) -> str:
    history = histories[chat_id]
    history.append({"role": "user", "content": user_text})
    try:
        response = await claude.beta.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=history,
            output_config={"effort": EFFORT},
            cache_control={"type": "ephemeral"},  # auto-cache the growing conversation prefix
            context_management={"edits": [{"type": "compact_20260112"}]},
            fallbacks="default",
            betas=BETAS,
        )
    except Exception:
        # The request failed, so no model turn references this message: safe to drop it
        # and let the user simply resend.
        history.pop()
        raise

    if response.stop_reason == "refusal":
        # Whole chain (including fallback) declined. Drop the turn so the chat can continue.
        history.pop()
        log.info("chat %s refusal: %s", chat_id, response.stop_details)
        return "Sorry, I can't help with that one. Feel free to ask something else."

    # Append the full content (thinking, compaction and fallback blocks included), not just text.
    history.append(
        {"role": "assistant", "content": [b.to_dict() for b in response.content]}
    )

    u = response.usage
    log.info(
        "chat %s req=%s in=%s cache_read=%s cache_write=%s out=%s stop=%s",
        chat_id, response._request_id, u.input_tokens, u.cache_read_input_tokens,
        u.cache_creation_input_tokens, u.output_tokens, response.stop_reason,
    )

    text = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if response.stop_reason == "max_tokens":
        text += "\n\n[Reply cut off - ask me to continue.]"
    return text or "Sorry, I came up empty. Could you rephrase?"


# -------------------------------------------------------------- handlers ----
async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    name = update.effective_user.first_name if update.effective_user else "there"
    await update.message.reply_text(
        f"Hi {name}! I'm an AI assistant. Ask me anything.\n"
        "I remember our conversation. Send /reset to start over."
    )


async def reset(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    async with locks[chat_id]:
        histories.pop(chat_id, None)
    await update.message.reply_text("Memory cleared. Fresh start!")


async def on_text(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        log.info("ignored user %s", update.effective_user and update.effective_user.id)
        return
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    if not text:
        return

    stop = asyncio.Event()
    typing = asyncio.create_task(keep_typing(update.effective_chat, stop))
    try:
        async with locks[chat_id]:  # one request at a time per chat keeps history ordered
            reply = await ask_claude(chat_id, text)
    except anthropic.RateLimitError:
        reply = "I'm getting too many requests right now. Please try again in a minute."
    except anthropic.APIConnectionError:
        reply = "I couldn't reach my AI service. Please try again shortly."
    except anthropic.APIStatusError as e:
        log.error("chat %s API error %s: %s", chat_id, e.status_code, e.message)
        reply = "Something went wrong on my side. Please try again."
    except Exception:
        log.exception("chat %s unexpected error", chat_id)
        reply = "Something went wrong on my side. Please try again."
    finally:
        stop.set()
        await typing

    for i, chunk in enumerate(split_message(reply)):
        if i == 0:
            await update.message.reply_text(chunk)
        else:
            await update.effective_chat.send_message(chunk)


async def on_unsupported(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if is_allowed(update) and update.message:
        await update.message.reply_text("I can only read text messages for now.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("telegram error", exc_info=context.error)


def main() -> None:
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)  # different chats are handled in parallel
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, on_unsupported))
    app.add_error_handler(on_error)
    log.info("bot starting (model=%s, effort=%s)", MODEL, EFFORT)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
