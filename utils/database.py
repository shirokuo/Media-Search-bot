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
    Akurat, cepat, dan aman dari timeout:
    1. Jika query kosong -> tampilkan file terbaru.
    2. Coba cari dengan regex case-insensitive di file_name dan caption.
    3. Jika hasil kosong, kembalikan tanda 'not_found'.
    """
    q = (query or "").strip()
    projection = {"file_name": 1, "file_size": 1, "file_type": 1, "caption": 1, "_id": 1}

    def _normalize(docs):
        normalized = []
        for d in docs:
            normalized.append({
                "file_id": str(d.get("_id")),
                "file_name": d.get("file_name"),
                "file_size": d.get("file_size"),
                "file_type": d.get("file_type"),
                "caption": d.get("caption"),
            })
        return normalized

    # Jika tidak ada query => tampilkan file terbaru
    if recent or not q:
        cursor = (
            database[COLLECTION_NAME]
            .find({}, projection)
            .sort("$natural", -1)
            .skip(offset)
            .limit(max_results)
        )
        docs = await cursor.to_list(length=max_results)
        normalized = _normalize(docs)
        next_offset = "" if len(normalized) < max_results else offset + max_results
        return normalized, next_offset

    # Buat regex yang toleran (ignore case dan spasi, simbol)
    # Regex memecah kata dan mencari kecocokan sebagian
    safe_q = re.escape(q)
    pattern = "|".join(
        re.escape(word.strip()) for word in q.split() if word.strip()
    )  # contoh: "how bot" => "how|bot"
    regex = re.compile(pattern, re.IGNORECASE)

    if USE_CAPTION_FILTER:
        filter_doc = {
            "$or": [
                {"file_name": {"$regex": regex}},
                {"caption": {"$regex": regex}},
            ]
        }
    else:
        filter_doc = {"file_name": {"$regex": regex}}

    try:
        cursor = (
            database[COLLECTION_NAME]
            .find(filter_doc, projection)
            .sort("$natural", -1)
            .skip(offset)
            .limit(max_results)
            .max_time_ms(2000)  # hindari timeout
        )
        docs = await cursor.to_list(length=max_results)
        if docs:
            normalized = _normalize(docs)
            next_offset = "" if len(normalized) < max_results else offset + max_results
            logger.info(f"✅ Search success for query '{q}', found {len(normalized)} results")
            return normalized, next_offset
        else:
            logger.info(f"⚠️ No match found for query '{q}'")
            return [], ""
    except Exception as e:
        logger.error(f"❌ Error during search '{q}': {e}")
        return [], ""
