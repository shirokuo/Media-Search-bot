##plugins/inline.py

import logging
import asyncio
import uuid
from urllib.parse import quote

from pyrogram import Client, emoji, filters
from pyrogram.errors import UserNotParticipant, BadRequest
from pyrogram.types import (
    InlineQueryResultCachedDocument,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from utils.database import get_search_results  # pastikan import langsung dari utils.database
from info import CACHE_TIME, SHARE_BUTTON_TEXT, AUTH_USERS, AUTH_CHANNEL

logger = logging.getLogger(__name__)
cache_time = 0 if AUTH_USERS or AUTH_CHANNEL else CACHE_TIME


def _make_id(prefix: str = "r"):
    """Generate short unique id for inline results"""
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _make_reply_markup(file_name: str):
    # tombol share yang sederhana; gunakan switch_inline_query_current_chat jika mau cari dari nama file
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔎 Cari lagi", switch_inline_query_current_chat=file_name)],
            [InlineKeyboardButton("📤 Bagikan bot", url=f"t.me/share/url?url={SHARE_BUTTON_TEXT}")],
        ]
    )


def size_formatter(size):
    if not size:
        return "N/A"
    try:
        size = int(size)
    except Exception:
        return "N/A"
    if size < 1024:
        return f"{size} B"
    elif size < 1024 ** 2:
        return f"{size / 1024:.2f} KB"
    elif size < 1024 ** 3:
        return f"{size / 1024 ** 2:.2f} MB"
    else:
        return f"{size / 1024 ** 3:.2f} GB"


@Client.on_inline_query(filters.user(AUTH_USERS) if AUTH_USERS else None)
async def answer(bot, query):
    """
    Inline query handler with robust error handling:
    - catches MESSAGE_NOT_MODIFIED and other BadRequest errors from Telegram
    - provides unique ids for inline results
    - returns a 'not found' article when no results
    """
    try:
        text = (query.query or "").strip()
        offset = int(query.offset or 0)
        is_empty = text == ""

        # call get_search_results with sensible timeout
        try:
            files, next_offset = await asyncio.wait_for(
                get_search_results(text, max_results=10, offset=offset, recent=is_empty),
                timeout=4.5
            )
        except asyncio.TimeoutError:
            logger.warning(f"Search timeout for query: {text}")
            # Kirim hasil timeout sebagai inline article (unik id)
            timeout_result = InlineQueryResultArticle(
                id=_make_id("timeout"),
                title="⚠️ Pencarian terlalu lama",
                input_message_content=InputTextMessageContent(
                    "⏳ Pencarian melebihi batas waktu. Coba gunakan kata kunci yang lebih spesifik."
                ),
                description="Coba perpendek atau perjelas kata kunci kamu.",
            )
            try:
                await query.answer(results=[timeout_result], cache_time=0, is_personal=True)
            except BadRequest as be:
                # Tangani MESSAGE_NOT_MODIFIED dan lainnya
                msg = str(be)
                if "MESSAGE_NOT_MODIFIED" in msg:
                    logger.debug("Ignored MESSAGE_NOT_MODIFIED while sending timeout_result")
                else:
                    logger.exception("BadRequest while answering timeout result: %s", be)
            except Exception:
                logger.exception("Unexpected error while answering timeout result")
            return

        results = []
        if files:
            for f in files:
                # file bisa berupa dict (dari normalize) — aman untuk akses dengan .get
                file_id = f.get("file_id")
                file_name = f.get("file_name") or "Tanpa Nama"
                file_size = f.get("file_size") or 0
                file_type = f.get("file_type") or "Unknown"
                caption = f.get("caption") or ""

                # setiap result beri id unik agar Telegram tidak mengira duplikat dan mencoba edit
                try:
                    results.append(
                        InlineQueryResultCachedDocument(
                            id=_make_id("doc"),
                            title=file_name,
                            document_file_id=file_id,
                            caption=caption,
                            description=f"📦 {file_type.upper()} | 💾 {size_formatter(file_size)}",
                            reply_markup=_make_reply_markup(file_name),
                        )
                    )
                except Exception:
                    # jika ada file_id yang invalid atau error pembuatan result, skip
                    logger.debug("Skipping invalid file for inline result: %s", file_name, exc_info=True)
                    continue

        # Tidak ada hasil: kembalikan satu artikel 'not found' (sebagai inline result)
        if not results:
            nf_title = f"{emoji.CROSS_MARK} Tidak ditemukan hasil"
            nf_text = f"❌ Tidak ditemukan file yang cocok untuk: **{text or 'Kueri kosong'}**\nCoba kata kunci lain."
            not_found = InlineQueryResultArticle(
                id=_make_id("nf"),
                title=nf_title,
                input_message_content=InputTextMessageContent(nf_text),
                description="Coba kata kunci lain atau periksa ejaan.",
            )
            results = [not_found]

        # Jawab ke Telegram — bungkus dengan try/except untuk tangani MESSAGE_NOT_MODIFIED
        try:
            await query.answer(
                results=results,
                cache_time=cache_time,
                is_personal=True,
                next_offset=str(next_offset) if next_offset else "",
            )
        except BadRequest as be:
            # Tangani kasus di mana Telegram menolak karena "message not modified"
            msg = str(be)
            if "MESSAGE_NOT_MODIFIED" in msg:
                # Terjadi saat bot mencoba mengirim/mengedit dengan konten yang sama — aman diabaikan
                logger.debug("Ignored MESSAGE_NOT_MODIFIED while answering inline query for '%s'", text)
            else:
                # Log exception lengkap untuk investigasi
                logger.exception("BadRequest when answering inline query: %s", be)
        except Exception as e:
            logger.exception("Unexpected error when answering inline query: %s", e)

    except Exception as e:
        logger.exception("❌ Inline query handler failed unexpectedly: %s", e)
        # fallback safe response
        try:
            fallback = InlineQueryResultArticle(
                id=_make_id("err"),
                title="❌ Terjadi kesalahan",
                input_message_content=InputTextMessageContent("⚠️ Kesalahan internal, coba lagi nanti."),
            )
            await query.answer(results=[fallback], cache_time=0, is_personal=True)
        except Exception:
            logger.exception("Also failed to send fallback inline message")


async def is_subscribed(bot, query):
    try:
        user = await bot.get_chat_member(AUTH_CHANNEL, query.from_user.id)
    except UserNotParticipant:
        pass
    except Exception as e:
        logger.exception(e)
    else:
        if not user.status == 'kicked':
            return True

    return False
