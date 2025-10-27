import re
import logging

from pymongo.errors import DuplicateKeyError
from umongo import Instance, Document, fields
from motor.motor_asyncio import AsyncIOMotorClient
from marshmallow.exceptions import ValidationError

from info import DATABASE_URI, DATABASE_NAME, COLLECTION_NAME, USE_CAPTION_FILTER
from .helpers import unpack_new_file_id

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

client = AsyncIOMotorClient(DATABASE_URI)
database = client[DATABASE_NAME]
instance = Instance.from_db(database)


@instance.register
class Media(Document):
    file_id = fields.StrField(attribute='_id')
    file_ref = fields.StrField(allow_none=True)
    file_name = fields.StrField(required=True)
    file_size = fields.IntField(required=True)
    file_type = fields.StrField(allow_none=True)
    mime_type = fields.StrField(allow_none=True)
    caption = fields.StrField(allow_none=True)

    class Meta:
        indexes = ('$file_name', )
        collection_name = COLLECTION_NAME


async def save_file(media):
    """Save file in database"""

    file_id, file_ref = unpack_new_file_id(media.file_id)

    try:
        file = Media(
            file_id=file_id,
            file_ref=file_ref,
            file_name=media.file_name,
            file_size=media.file_size,
            file_type=media.file_type,
            mime_type=media.mime_type,
            caption=media.caption.html if media.caption else None,
        )
    except ValidationError:
        logger.exception('Error occurred while saving file in database')
    else:
        try:
            await file.commit()
        except DuplicateKeyError:
            logger.warning(media.file_name + " is already saved in database")
        else:
            logger.info(media.file_name + " is saved in database")


async def get_search_results(query, file_type=None, max_results=10, offset=0, recent=False):
    """
    Tiered search to avoid collection-wide scanning that causes timeouts:
    1) If recent=True or empty query -> return recent files.
    2) Try $text search (fast if text index exists).
    3) Try $regex with small max_time_ms.
    4) Fallback: scan recent N docs and filter in Python (safe).
    Returns (normalized_results_list, next_offset)
    """
    # Normalization
    q = (query or "").strip()
    projection = {
        "file_name": 1,
        "file_size": 1,
        "file_type": 1,
        "caption": 1,
        "_id": 1,
    }

    # Helper to normalize docs to expected format
    def _normalize(docs):
        normalized = []
        for d in docs:
            file_doc = {
                "file_id": str(d.get("_id")) if "_id" in d else d.get("file_id"),
                "file_name": d.get("file_name"),
                "file_size": d.get("file_size"),
                "file_type": d.get("file_type"),
                "caption": d.get("caption"),
            }
            normalized.append(file_doc)
        return normalized

    # 1) recent / empty
    if recent or not q:
        cursor = database[COLLECTION_NAME].find({}, projection).sort("$natural", -1).skip(offset).limit(max_results)
        docs = await cursor.to_list(length=max_results)
        normalized = _normalize(docs)
        next_offset = "" if len(normalized) < max_results else offset + max_results
        return normalized, next_offset

    # 2) Try text search first (if text index exists) - fastest when available
    try:
        text_filter = {"$text": {"$search": q}}
        # include score to sort
        cursor = database[COLLECTION_NAME].find(text_filter, {**projection, "score": {"$meta": "textScore"}})
        cursor = cursor.sort([("score", {"$meta": "textScore"})]).skip(offset).limit(max_results)
        docs = await cursor.to_list(length=max_results)
        if docs:
            normalized = _normalize(docs)
            next_offset = "" if len(normalized) < max_results else offset + max_results
            logger.debug(f"Search: text-search success for '{q}', returned {len(normalized)}")
            return normalized, next_offset
    except Exception as e:
        # Likely no text index or other error — ignore and continue to regex
        logger.debug(f"Text search not available/failed for '{q}': {e}")

    # 3) Try $regex with limited max_time_ms to avoid long blocking
    try:
        # simple substring regex (case-insensitive)
        # NOTE: do NOT escape here completely to allow searching for dots etc.
        # but sanitize by trimming whitespace
        regex = q
        if USE_CAPTION_FILTER:
            filter_doc = {
                "$or": [
                    {"file_name": {"$regex": regex, "$options": "i"}},
                    {"caption": {"$regex": regex, "$options": "i"}},
                ]
            }
        else:
            filter_doc = {"file_name": {"$regex": regex, "$options": "i"}}

        cursor = (
            database[COLLECTION_NAME]
            .find(filter_doc, projection)
            .sort("$natural", -1)
            .skip(offset)
            .limit(max_results)
            .max_time_ms(1500)  # cepat: batasi 1.5 detik
        )
        docs = await cursor.to_list(length=max_results)
        if docs:
            normalized = _normalize(docs)
            next_offset = "" if len(normalized) < max_results else offset + max_results
            logger.debug(f"Search: regex success for '{q}', returned {len(normalized)}")
            return normalized, next_offset
    except Exception as e:
        logger.warning(f"Regex search slow/failed for '{q}': {e}")

    # 4) Fallback: scan recent N docs and filter in Python
    # If query is short (<=4) we limit to fewer docs; if longer allow more
    recent_limit = 1000 if len(q) >= 5 else 600
    try:
        # Get recent docs only (avoid scanning whole collection)
        recent_docs = await database[COLLECTION_NAME].find({}, projection).sort("$natural", -1).to_list(length=recent_limit)
    except Exception as e:
        logger.error(f"Fallback recent scan failed: {e}")
        recent_docs = []

    q_lower = q.lower()
    matched = []
    for d in recent_docs:
        fname = (d.get("file_name") or "").lower()
        caption = (d.get("caption") or "").lower()
        if q_lower in fname or (USE_CAPTION_FILTER and q_lower in caption):
            matched.append(d)
            if len(matched) >= (offset + max_results):
                break

    # apply offset and slice
    sliced = matched[offset: offset + max_results]
    normalized = _normalize(sliced)
    next_offset = "" if len(sliced) < max_results else offset + max_results
    logger.debug(f"Search: fallback matched {len(normalized)} for '{q}' from recent {len(recent_docs)} docs")

    return normalized, next_offset
