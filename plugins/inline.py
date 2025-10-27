##plugins/inline.py

import logging
import asyncio
from urllib.parse import quote

from pyrogram import Client, emoji, filters
from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultCachedDocument

from utils.database import get_search_results  # pastikan import langsung dari utils.database
from info import CACHE_TIME, SHARE_BUTTON_TEXT, AUTH_USERS, AUTH_CHANNEL

logger = logging.getLogger(__name__)
cache_time = 0 if AUTH_USERS or AUTH_CHANNEL else CACHE_TIME


@Client.on_inline_query(filters.user(AUTH_USERS) if AUTH_USERS else None)
async def answer(bot, query):
    try:
        text = (query.query or "").strip()
        offset = int(query.offset or 0)
        is_empty = text == ""

        try:
            files, next_offset = await asyncio.wait_for(
                get_search_results(text, max_results=10, offset=offset, recent=is_empty),
                timeout=4.5
            )
        except asyncio.TimeoutError:
            logger.warning(f"Search timeout for query: {text}")
            await query.answer(
                results=[
                    InlineQueryResultArticle(
                        title="⚠️ Pencarian terlalu lama",
                        input_message_content=InputTextMessageContent(
                            "⏳ Pencarian melebihi batas waktu. Coba gunakan kata kunci yang lebih spesifik."
                        ),
                        description="Coba perpendek atau perjelas kata kunci kamu.",
                    )
                ],
                cache_time=0,
                switch_pm_text="⚠️ Timeout",
                switch_pm_parameter="timeout"
            )
            return

        results = []
        if files:
            for f in files:
                file_id = f.get("file_id")
                file_name = f.get("file_name") or "Tanpa Nama"
                file_size = f.get("file_size") or 0
                file_type = f.get("file_type") or "Unknown"
                caption = f.get("caption") or ""

                results.append(
                    InlineQueryResultCachedDocument(
                        title=file_name,
                        document_file_id=file_id,
                        caption=caption,
                        description=f"📦 {file_type.upper()} | 💾 {round(file_size/1024/1024, 2)} MB",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [InlineKeyboardButton(SHARE_BUTTON_TEXT, switch_inline_query=file_name)]
                            ]
                        )
                    )
                )

        # Jika tidak ada hasil sama sekali, tampilkan not found message langsung di inline result
        if not results:
            results.append(
                InlineQueryResultArticle(
                    title=f"{emoji.CROSS_MARK} Tidak ditemukan hasil",
                    input_message_content=InputTextMessageContent(
                        f"❌ Tidak ditemukan file yang cocok untuk: **{text or 'Kueri kosong'}**"
                    ),
                    description="Coba kata kunci lain atau periksa ejaan file.",
                )
            )

        await query.answer(
            results=results,
            cache_time=cache_time,
            is_personal=True,
            next_offset=str(next_offset) if next_offset else "",
        )

    except Exception as e:
        logger.exception(f"❌ Inline query error: {e}")
        try:
            await query.answer(
                results=[
                    InlineQueryResultArticle(
                        title="❌ Terjadi kesalahan",
                        input_message_content=InputTextMessageContent("⚠️ Kesalahan internal, coba lagi nanti."),
                    )
                ],
                cache_time=0,
                is_personal=True,
            )
        except Exception:
            pass


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
    """Get size in readable format"""

    units = ["Bytes", "KB", "MB", "GB", "TB", "PB", "EB"]
    size = float(size)
    i = 0
    while size >= 1024.0 and i < len(units):
        i += 1
        size /= 1024.0
    return "%.2f %s" % (size, units[i])


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
