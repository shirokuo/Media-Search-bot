##plugins/inline.py

import asyncio
import logging
from urllib.parse import quote

from pyrogram import Client, emoji, filters
from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultCachedDocument
from utils import get_search_results
from utils.database import get_search_results  # pastikan import langsung dari utils.database
from info import CACHE_TIME, SHARE_BUTTON_TEXT, AUTH_USERS, AUTH_CHANNEL

logger = logging.getLogger(__name__)
cache_time = 0 if AUTH_USERS or AUTH_CHANNEL else CACHE_TIME


@Client.on_inline_query(filters.user(AUTH_USERS) if AUTH_USERS else None)
async def answer(bot, query):
    """Handle inline search query"""
    try:
        text = query.query.strip()
        if not text:
            await query.answer(
                results=[],
                cache_time=0,
                switch_pm_text="🔍 Ketik nama file untuk mencari",
                switch_pm_parameter="help"
            )
            return

        # Support "|filetype"
        if '|' in text:
            text, file_type = text.split('|', maxsplit=1)
            text, file_type = text.strip(), file_type.strip().lower()
        else:
            file_type = None

        # Fetch results (limit 10)
        offset = int(query.offset or 0)
        reply_markup = get_reply_markup(bot.username, query=text)

        # Timeout untuk mencegah QUERY_ID_INVALID
        try:
            files, next_offset = await asyncio.wait_for(
                get_search_results(text, file_type=file_type, max_results=10, offset=offset),
                timeout=4.5
            )
        except asyncio.TimeoutError:
            logger.warning(f"Search timeout for query: {text}")
            await query.answer(
                results=[],
                cache_time=0,
                switch_pm_text="⚠️ Pencarian terlalu lama, coba lagi",
                switch_pm_parameter="retry"
            )
            return

        results = []
        for file in files:
            # pastikan file punya file_id dan nama
            if not getattr(file, "file_id", None) or not getattr(file, "file_name", None):
                continue
            results.append(
                InlineQueryResultCachedDocument(
                    title=file.file_name,
                    document_file_id=file.file_id,
                    caption=file.caption or "",
                    description=f"Size: {size_formatter(file.file_size)} | Type: {file.file_type or 'N/A'}",
                    reply_markup=reply_markup
                )
            )

        if results:
            switch_pm_text = f"{emoji.FILE_FOLDER} Hasil untuk '{text}'"
            await query.answer(
                results=results,
                cache_time=cache_time,
                switch_pm_text=switch_pm_text,
                switch_pm_parameter="start",
                next_offset=str(next_offset)
            )
        else:
            await query.answer(
                results=[],
                cache_time=cache_time,
                switch_pm_text=f"{emoji.CROSS_MARK} Tidak ditemukan hasil untuk '{text}'",
                switch_pm_parameter="notfound"
            )
    except Exception as e:
        logger.exception(f"❌ Inline query error: {e}")
        try:
            await query.answer(
                results=[],
                cache_time=0,
                switch_pm_text="⚠️ Terjadi kesalahan",
                switch_pm_parameter="error"
            )
        except:
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
