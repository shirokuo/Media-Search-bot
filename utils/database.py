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
        q = query.strip()
        # Escape user input to prevent regex issues
        escaped = re.escape(q)
        regex = re.compile(f".*{escaped}.*", flags=re.IGNORECASE)
        if USE_CAPTION_FILTER:
            filter_doc = {'$or': [{'file_name': regex}, {'caption': regex}]}
        else:
            filter_doc = {'file_name': regex}

    if file_type:
        filter_doc['file_type'] = file_type

    # Projection to only needed fields (faster)
    projection = {"file_name": 1, "file_size": 1, "file_type": 1, "caption": 1, "_id": 1}

    cursor = database[COLLECTION_NAME].find(filter_doc, projection)

    # Sort by natural order (recent first)
    cursor = cursor.sort('$natural', -1).skip(offset).limit(max_results)

    # Convert cursor to list (motor)
    files = await cursor.to_list(length=max_results)

    # Normalize returned documents to have expected attributes (compatibility)
    normalized = []
    for d in files:
        # If stored with _id as file_id then adjust
        file_doc = {
            "file_id": str(d.get("_id")) if "_id" in d else d.get("file_id"),
            "file_name": d.get("file_name"),
            "file_size": d.get("file_size"),
            "file_type": d.get("file_type"),
            "caption": d.get("caption"),
        }
        normalized.append(file_doc)

    # Determine next_offset: if returned less than requested max -> no more results
    if len(normalized) < max_results:
        next_offset = ''
    else:
        next_offset = offset + max_results

    return normalized, next_offset
