##plugins/inline.py

import logging
import asyncio
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


def get_reply_markup(username, query):
    url = 't.me/share/url?url=' + quote(SHARE_BUTTON_TEXT.format(username=username))
    buttons = [
        [
            InlineKeyboardButton("🔎 Cari lagi", switch_inline_query_current_chat=query),
            InlineKeyboardButton("📤 Bagikan bot", url=url),
        ]
    ]
    return InlineKeyboardMarkup(buttons)


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

@Client.on_inline_query(filters.user(AUTH_USERS) if AUTH_USERS else None)
async def answer(bot, query):
    try:
        raw = query.query or ""
        text = raw.strip()
        is_empty_query = (text == "")

        file_type = None
        if not is_empty_query and '|' in text:
            text, file_type = text.split('|', maxsplit=1)
            text = text.strip()
            file_type = file_type.strip().lower()

        offset = int(query.offset or 0)
        reply_markup = get_reply_markup(bot.username, query=text)
        max_results = 10

        # tunggu hasil tapi tetap di bawah 5s (safety)
        try:
            files, next_offset = await asyncio.wait_for(
                get_search_results(text, file_type=file_type, max_results=max_results, offset=offset, recent=is_empty_query),
                timeout=4.9
            )
        except asyncio.TimeoutError:
            logger.warning(f"Search timeout for query: {text}")
            await query.answer(
                results=[
                    InlineQueryResultArticle(
                        title="⚠️ Waktu pencarian habis",
                        description="Coba gunakan kata kunci lebih spesifik atau tambahkan lebih dari 2 karakter.",
                        input_message_content=InputTextMessageContent(
                            f"⚠️ Pencarian untuk '{text}' terlalu lama. Coba ulangi dengan kata kunci yang lebih spesifik."
                        ),
                    )
                ],
                cache_time=0,
                is_personal=True
            )
            return

        results = []
        if files:
            for f in files:
                results.append(
                    InlineQueryResultCachedDocument(
                        title=f.get("file_name") or "Tanpa nama",
                        document_file_id=f.get("file_id"),
                        caption=f.get("caption") or "",
                        description=f"Size: {f.get('file_size', 'N/A')} | Type: {f.get('file_type', 'N/A')}",
                        reply_markup=reply_markup
                    )
                )
        else:
            # tidak ada hasil
            results.append(
                InlineQueryResultArticle(
                    title=f"❌ Tidak ditemukan hasil untuk '{text or 'kosong'}'",
                    description="Periksa ejaan atau coba kata kunci lain (lebih spesifik).",
                    input_message_content=InputTextMessageContent(f"❌ Tidak ada file ditemukan untuk pencarian: `{text}`"),
                )
            )

        await query.answer(results=results, cache_time=cache_time, next_offset=str(next_offset) if next_offset else "", is_personal=True)

    except Exception as e:
        logger.exception(f"❌ Inline query error: {e}")
        try:
            await query.answer(
                results=[
                    InlineQueryResultArticle(
                        title="⚠️ Terjadi kesalahan",
                        description="Terjadi kesalahan internal saat mencari file",
                        input_message_content=InputTextMessageContent("⚠️ Terjadi kesalahan internal saat mencari file."),
                    )
                ],
                cache_time=0,
                is_personal=True
            )
        except Exception:
            pass
