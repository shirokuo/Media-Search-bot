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


async def get_search_results(query, file_type=None, max_results=10, offset=0):
    """For given query return (results, next_offset)"""
    query = query.strip()
    if not query:
        regex = re.compile(".*", re.IGNORECASE)
    else:
        # Lebih fleksibel: cocokkan di mana pun dalam nama file
        safe_query = re.escape(query)
        regex = re.compile(f".*{safe_query}.*", re.IGNORECASE)

    if USE_CAPTION_FILTER:
        search_filter = {'$or': [{'file_name': regex}, {'caption': regex}]}
    else:
        search_filter = {'file_name': regex}

    if file_type:
        search_filter['file_type'] = file_type

    total = await Media.count_documents(search_filter)
    next_offset = offset + max_results
    if next_offset >= total:
        next_offset = ''

    cursor = Media.find(search_filter).sort('$natural', -1).skip(offset).limit(max_results)
    files = await cursor.to_list(length=max_results)

    return files, next_offset
