"""Telegram AI chat bot powered by Google Gemini.

Long-polling (no public HTTPS / webhook needed). One conversation per Telegram chat.
Config comes from environment variables -- see .env.example.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict

from google import genai
from google.genai import errors, types
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
for _name in ("httpx", "httpx2", "google_genai"):
    logging.getLogger(_name).setLevel(logging.WARNING)
log = logging.getLogger("bot")

# ---------------------------------------------------------------- config ----
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
MODEL = os.environ.get("BOT_MODEL", "gemini-3.5-flash")
THINKING_LEVEL = os.environ.get("BOT_THINKING_LEVEL", "low")  # minimal | low | medium | high
MAX_TOKENS = int(os.environ.get("BOT_MAX_TOKENS", "8192"))
HISTORY_TURNS = int(os.environ.get("BOT_HISTORY_TURNS", "30"))  # user+bot exchanges kept per chat
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

# Reads GEMINI_API_KEY (or GOOGLE_API_KEY) from the environment.
# Retries rate limits (429) and transient server errors with exponential backoff.
gemini = genai.Client(
    http_options=types.HttpOptions(
        timeout=120_000,  # milliseconds
        retry_options=types.HttpRetryOptions(
            attempts=4, http_status_codes=[408, 429, 500, 502, 503, 504]
        ),
    )
)

GEN_CONFIG = types.GenerateContentConfig(
    system_instruction=SYSTEM_PROMPT,
    max_output_tokens=MAX_TOKENS,
    thinking_config=types.ThinkingConfig(thinking_level=THINKING_LEVEL),
)

# chat_id -> conversation history (Gemini Content objects, oldest first).
histories: dict[int, list[types.Content]] = defaultdict(list)
locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

BLOCKED_REPLY = "Sorry, I can't help with that one. Feel free to ask something else."


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


def trim_history(history: list[types.Content]) -> None:
    """Keep the last HISTORY_TURNS exchanges; always start on a user turn."""
    max_items = HISTORY_TURNS * 2
    if len(history) > max_items:
        del history[: len(history) - max_items]
    while history and history[0].role != "user":
        history.pop(0)


async def ask_gemini(chat_id: int, user_text: str) -> str:
    history = histories[chat_id]
    history.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
    try:
        response = await gemini.aio.models.generate_content(
            model=MODEL, contents=history, config=GEN_CONFIG
        )
    except Exception:
        # The request failed: drop the unanswered message so the user can simply resend.
        history.pop()
        raise

    # Prompt blocked before any answer was generated.
    if response.prompt_feedback and response.prompt_feedback.block_reason:
        history.pop()
        log.info("chat %s prompt blocked: %s", chat_id, response.prompt_feedback.block_reason)
        return BLOCKED_REPLY

    candidate = response.candidates[0] if response.candidates else None
    finish = candidate.finish_reason if candidate else None
    text = (response.text or "").strip() if candidate else ""

    if not candidate or not candidate.content or (
        not text and finish != types.FinishReason.STOP
    ):
        history.pop()
        log.info("chat %s no answer (finish=%s)", chat_id, finish)
        return BLOCKED_REPLY

    # Append the model's full Content (keeps thought signatures Gemini 3 expects back).
    history.append(candidate.content)
    trim_history(history)

    u = response.usage_metadata
    log.info(
        "chat %s model=%s in=%s cached=%s thoughts=%s out=%s finish=%s",
        chat_id, response.model_version, u and u.prompt_token_count,
        u and u.cached_content_token_count, u and u.thoughts_token_count,
        u and u.candidates_token_count, finish,
    )

    if finish == types.FinishReason.MAX_TOKENS:
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
            reply = await ask_gemini(chat_id, text)
    except errors.APIError as e:
        log.error("chat %s Gemini API error %s: %s", chat_id, e.code, e.message)
        if e.code == 429:
            reply = "I'm getting too many requests right now. Please try again in a minute."
        else:
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
    log.info("bot starting (model=%s, thinking=%s)", MODEL, THINKING_LEVEL)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
