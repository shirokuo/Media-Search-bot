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
        q = (query.query or "").strip()
        offset = int(query.offset or 0)
        is_empty = not q

        # Cari hasil
        try:
            files, next_offset = await asyncio.wait_for(
                get_search_results(q, offset=offset, recent=is_empty),
                timeout=4.8
            )
        except asyncio.TimeoutError:
            logger.warning(f"Search timeout for query: {q}")
            await query.answer(
                results=[
                    InlineQueryResultArticle(
                        title="⚠️ Waktu pencarian habis",
                        description="Coba gunakan kata kunci yang lebih spesifik",
                        input_message_content=InputTextMessageContent(
                            f"⚠️ Pencarian untuk '{q}' terlalu lama. Coba ulangi dengan kata kunci lebih spesifik."
                        ),
                    )
                ],
                cache_time=0
            )
            return

        results = []

        # Tampilkan hasil file jika ditemukan
        if files:
            for f in files:
                results.append(
                    InlineQueryResultCachedDocument(
                        title=f.get("file_name") or "Tanpa nama",
                        document_file_id=f.get("file_id"),
                        caption=f.get("caption") or "",
                        description=f"Size: {f.get('file_size', 'N/A')} | Type: {f.get('file_type', 'N/A')}",
                    )
                )
        else:
            # Tidak ada hasil → kirim satu hasil inline artikel
            results.append(
                InlineQueryResultArticle(
                    title=f"❌ Tidak ditemukan hasil untuk '{q or 'kosong'}'",
                    description="Pastikan ejaan benar atau coba kata lain.",
                    input_message_content=InputTextMessageContent(
                        f"❌ Tidak ada file ditemukan untuk pencarian: `{q}`"
                    ),
                )
            )

        await query.answer(
            results=results,
            cache_time=cache_time,
            next_offset=str(next_offset) if next_offset else "",
            is_personal=True
        )

    except Exception as e:
        logger.exception(f"❌ Inline query error: {e}")
        await query.answer(
            results=[
                InlineQueryResultArticle(
                    title="⚠️ Terjadi kesalahan",
                    description=str(e),
                    input_message_content=InputTextMessageContent("⚠️ Terjadi kesalahan saat mencari file."),
                )
            ],
            cache_time=0,
            is_personal=True
        )
        
