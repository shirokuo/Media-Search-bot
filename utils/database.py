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


async def get_search_results(query: str, file_type=None, max_results=10, offset=0, recent=False):
    """
    Advanced search with:
    - Smart regex that tolerates partial match, punctuation, and spacing
    - Optional caption search
    - Fallback fuzzy search in Python
    - Returns (results, next_offset)
    """

    q = (query or "").strip()
    projection = {
        "file_name": 1,
        "file_size": 1,
        "file_type": 1,
        "caption": 1,
        "file_id": 1,
    }

    def _normalize(docs):
        normalized = []
        for d in docs:
            normalized.append({
                "file_id": d.get("file_id") or str(d.get("_id")),
                "file_name": d.get("file_name"),
                "file_size": d.get("file_size"),
                "file_type": d.get("file_type"),
                "caption": d.get("caption"),
            })
        return normalized

    # Jika query kosong atau recent=True, ambil file terbaru
    if recent or not q:
        cursor = database[COLLECTION_NAME].find({}, projection).sort("$natural", -1).skip(offset).limit(max_results)
        docs = await cursor.to_list(length=max_results)
        normalized = _normalize(docs)
        next_offset = "" if len(normalized) < max_results else offset + max_results
        return normalized, next_offset

    # 🔍 Regex super fleksibel
    # Contoh input: "how bot" → hasilkan pola "how.*bot"
    safe_query = re.sub(r"[^0-9a-zA-Z\u00C0-\u024F]+", " ", q).strip()
    regex_pattern = ".*".join(map(re.escape, safe_query.split()))
    smart_regex = re.compile(regex_pattern, re.IGNORECASE)

    # 🔹 Tahap 1: Regex search di Mongo (dengan batas waktu aman)
    try:
        mongo_filter = {
            "$or": [{"file_name": {"$regex": smart_regex}}]
        }
        if USE_CAPTION_FILTER:
            mongo_filter["$or"].append({"caption": {"$regex": smart_regex}})

        cursor = (
            database[COLLECTION_NAME]
            .find(mongo_filter, projection)
            .sort("$natural", -1)
            .skip(offset)
            .limit(max_results)
            .max_time_ms(2500)
        )
        docs = await cursor.to_list(length=max_results)
        if docs:
            normalized = _normalize(docs)
            next_offset = "" if len(normalized) < max_results else offset + max_results
            logger.info(f"Super regex matched {len(normalized)} result(s) for '{q}'")
            return normalized, next_offset
    except Exception as e:
        logger.warning(f"Regex search error for '{q}': {e}")

    # 🔹 Tahap 2: fallback fuzzy search (scan sebagian data dan cocokan manual)
    try:
        recent_docs = await database[COLLECTION_NAME].find({}, projection).sort("$natural", -1).to_list(length=1000)
        q_lower = q.lower()
        matched = []
        for d in recent_docs:
            name = (d.get("file_name") or "").lower()
            caption = (d.get("caption") or "").lower()
            if q_lower in name or (USE_CAPTION_FILTER and q_lower in caption):
                matched.append(d)
                if len(matched) >= (offset + max_results):
                    break
        sliced = matched[offset: offset + max_results]
        if sliced:
            normalized = _normalize(sliced)
            next_offset = "" if len(sliced) < max_results else offset + max_results
            logger.info(f"Fallback fuzzy matched {len(normalized)} for '{q}'")
            return normalized, next_offset
    except Exception as e:
        logger.error(f"Fuzzy fallback failed for '{q}': {e}")

    # Jika tidak ada hasil sama sekali
    return [], ""
