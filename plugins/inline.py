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
    """Handle inline search query"""
    try:
        raw = query.query or ""  # jangan None
        text = raw.strip()

        # Jika bot dipanggil tanpa kata kunci (user mengetik hanya @bot), tampilkan recent
        is_empty_query = (text == "")

        # support "text | filetype"
        file_type = None
        if not is_empty_query and '|' in text:
            text, file_type = text.split('|', maxsplit=1)
            text = text.strip()
            file_type = file_type.strip().lower()

        offset = int(query.offset or 0)
        reply_markup = get_reply_markup(bot.username, query=text)

        # ketika kosong, request recent (fast) - berikan lebih banyak hasil agar user bisa scroll
        max_results = 10
        if is_empty_query:
            # recent results: offset digunakan sama
            try:
                files, next_offset = await asyncio.wait_for(
                    get_search_results("", file_type=file_type, max_results=max_results, offset=offset, recent=True),
                    timeout=4.8
                )
            except asyncio.TimeoutError:
                logger.warning("Search timeout for recent results")
                await query.answer(results=[], cache_time=0,
                                   switch_pm_text="⚠️ Pencarian terlalu lama, coba lagi",
                                   switch_pm_parameter="retry")
                return
        else:
            # normal search dengan timeout; jika timeout -> beri pesan friendly
            try:
                files, next_offset = await asyncio.wait_for(
                    get_search_results(text, file_type=file_type, max_results=max_results, offset=offset, recent=False),
                    timeout=4.8
                )
            except asyncio.TimeoutError:
                logger.warning(f"Search timeout for query: {text}")
                await query.answer(
                    results=[],
                    cache_time=0,
                    switch_pm_text="⚠️ Pencarian terlalu lama, coba singkat kata kunci atau coba lagi",
                    switch_pm_parameter="retry"
                )
                return

        results = []
        for file in files:
            # validasi minimal
            file_id = getattr(file, "file_id", None) or getattr(file, "file_id", None) or file.get("file_id") if isinstance(file, dict) else None
            file_name = getattr(file, "file_name", None) or file.get("file_name") if isinstance(file, dict) else None
            if not file_id or not file_name:
                continue

            # make sure file_size/file_type/caption retrieval robust
            file_size = getattr(file, "file_size", None) or file.get("file_size") if isinstance(file, dict) else None
            file_type_val = getattr(file, "file_type", None) or file.get("file_type") if isinstance(file, dict) else None
            caption = getattr(file, "caption", None) or file.get("caption") if isinstance(file, dict) else None

            results.append(
                InlineQueryResultCachedDocument(
                    title=file_name,
                    document_file_id=file_id,
                    caption=caption or "",
                    description=f"Size: {size_formatter(file_size)} | Type: {file_type_val or 'N/A'}",
                    reply_markup=reply_markup
                )
            )

        if results:
            switch_pm_text = f"{emoji.FILE_FOLDER} Hasil"
            if text:
                switch_pm_text += f" untuk '{text}'"
            await query.answer(
                results=results,
                cache_time=cache_time,
                switch_pm_text=switch_pm_text,
                switch_pm_parameter="start",
                next_offset=str(next_offset) if next_offset != '' else ''
            )
        else:
            # ketika kosong dan ini query kosong sebelumnya, tunjukkan pesan default
            if is_empty_query:
                switch_pm_text = f"{emoji.CROSS_MARK} Tidak ada file terbaru"
            else:
                switch_pm_text = f"{emoji.CROSS_MARK} Tidak ditemukan hasil untuk '{text}'"

            await query.answer(
                results=[],
                cache_time=cache_time,
                switch_pm_text=switch_pm_text,
                switch_pm_parameter="notfound"
            )
    except Exception as e:
        logger.exception(f"❌ Inline query error: {e}")
        # jangan crash; usahakan jawab ke Telegram
        try:
            await query.answer(results=[], cache_time=0,
                               switch_pm_text="⚠️ Terjadi kesalahan internal",
                               switch_pm_parameter="error")
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
