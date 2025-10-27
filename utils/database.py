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
    For given query return (results, next_offset)
    recent=True -> return recent files (no regex)
    """
    # Build filter
    if recent or not query:
        filter_doc = {}
    else:
        # Gunakan pencarian substring case-insensitive, bukan regex berat
        q = query.strip()
        filter_doc = {"file_name": {"$regex": q, "$options": "i"}}

        if USE_CAPTION_FILTER:
            filter_doc = {
                "$or": [
                    {"file_name": {"$regex": q, "$options": "i"}},
                    {"caption": {"$regex": q, "$options": "i"}}
                ]
            }

    if file_type:
        filter_doc["file_type"] = file_type

    projection = {
        "file_name": 1,
        "file_size": 1,
        "file_type": 1,
        "caption": 1,
        "_id": 1,
    }

    try:
        cursor = (
            database[COLLECTION_NAME]
            .find(filter_doc, projection)
            .sort("$natural", -1)
            .skip(offset)
            .limit(max_results)
            .max_time_ms(4000)  # batasi 4 detik agar tidak timeout lama
        )

        docs = await cursor.to_list(length=max_results)
    except Exception as e:
        logger.warning(f"Fallback search triggered due to slow query: {e}")
        docs = []
        # Fallback pencarian manual ringan kalau query timeout
        all_docs = await database[COLLECTION_NAME].find({}, projection).sort("$natural", -1).to_list(length=200)
        query_lower = query.lower()
        for d in all_docs:
            if query_lower in d.get("file_name", "").lower():
                docs.append(d)
                if len(docs) >= max_results:
                    break

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

    # Tentukan next_offset
    next_offset = "" if len(normalized) < max_results else offset + max_results

    return normalized, next_offset
