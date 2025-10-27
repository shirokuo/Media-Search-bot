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
    Adaptive, batch-based search:
    - If recent or empty -> return recent files fast.
    - If query length >= 3 -> try server-side regex but iterate cursor in batches and stop early.
    - If server-side fails or query short -> fallback to client-side batch scan (small batches) until enough results.
    Returns (normalized_list, next_offset)
    """
    q = (query or "").strip()
    projection = {"file_name": 1, "file_size": 1, "file_type": 1, "caption": 1, "_id": 1}
    col = database[COLLECTION_NAME]

    def _normalize(docs):
        out = []
        for d in docs:
            out.append({
                "file_id": str(d.get("_id")),
                "file_name": d.get("file_name"),
                "file_size": d.get("file_size"),
                "file_type": d.get("file_type"),
                "caption": d.get("caption"),
            })
        return out

    # 1) recent / empty query -> recent results fast
    if recent or not q:
        cursor = col.find({}, projection).sort("$natural", -1).skip(offset).limit(max_results)
        docs = await cursor.to_list(length=max_results)
        normalized = _normalize(docs)
        next_offset = "" if len(normalized) < max_results else offset + max_results
        return normalized, next_offset

    # prepare smart regex pattern (super flexible)
    safe_query = re.sub(r"[^0-9a-zA-Z\u00C0-\u024F]+", " ", q).strip()
    pattern_parts = [re.escape(p) for p in safe_query.split() if p]
    if not pattern_parts:
        # fallback to substring
        pattern = re.escape(q)
    else:
        pattern = ".*".join(pattern_parts)
    smart_regex = re.compile(pattern, re.IGNORECASE)

    # if file_type filter provided, include it
    file_type_filter = {"file_type": file_type} if file_type else {}

    # 2) Try server-side regex but iterate in batches and stop early
    try:
        # If query is short (len < 3) server might scan too much — we will still try but with cautious batch size
        batch_size = 50 if len(q) >= 3 else 30

        mongo_filter = {"file_name": {"$regex": smart_regex}}
        if USE_CAPTION_FILTER:
            mongo_filter = {"$or": [{"file_name": {"$regex": smart_regex}}, {"caption": {"$regex": smart_regex}}]}

        if file_type:
            # add file_type constraint
            if "$or" in mongo_filter:
                for clause in mongo_filter["$or"]:
                    clause.update({"file_type": file_type})
            else:
                mongo_filter["file_type"] = file_type

        cursor = col.find(mongo_filter, projection).sort("$natural", -1).batch_size(batch_size)
        collected = []
        collected_needed = offset + max_results

        # iterate cursor and stop when enough matches collected
        while await cursor.fetch_next:
            doc = cursor.next_object()
            collected.append(doc)
            if len(collected) >= collected_needed:
                break

        # If collected less than needed, we still return what we have
        sliced = collected[offset: offset + max_results]
        if sliced:
            normalized = _normalize(sliced)
            next_offset = "" if len(sliced) < max_results else offset + max_results
            logger.info(f"Server-regex matched {len(normalized)} for '{q}' (iterative)")
            return normalized, next_offset
        # else fallthrough to client-side fallback
    except Exception as e:
        logger.warning(f"Server-side regex failed/slow for '{q}': {e}")

    # 3) Client-side fallback scan in small batches (safe)
    try:
        # For short queries, scan fewer docs per batch to keep fast; longer queries can scan more
        batch_doc_limit = 200 if len(q) >= 5 else 120
        scanned = 0
        matched = []
        # We'll fetch in pages of page_size (to avoid loading huge lists)
        page_size = 200
        page = 0
        q_lower = q.lower()

        while len(matched) < (offset + max_results):
            docs = await col.find({}, projection).sort("$natural", -1).skip(page * page_size).limit(page_size).to_list(length=page_size)
            if not docs:
                break
            for d in docs:
                name = (d.get("file_name") or "").lower()
                caption = (d.get("caption") or "").lower()
                # match using smart_regex first (fast in Python) or substring
                if smart_regex.search(name) or (USE_CAPTION_FILTER and smart_regex.search(caption)) or (q_lower in name) or (USE_CAPTION_FILTER and q_lower in caption):
                    matched.append(d)
                    if len(matched) >= (offset + max_results):
                        break
            scanned += len(docs)
            page += 1
            # safety: don't scan indefinitely - cap scanned docs
            if scanned >= (batch_doc_limit * 5):  # hard cap ~ batch_doc_limit*5 docs
                break

        sliced = matched[offset: offset + max_results]
        if sliced:
            normalized = _normalize(sliced)
            next_offset = "" if len(sliced) < max_results else offset + max_results
            logger.info(f"Client-fallback matched {len(normalized)} for '{q}' after scanning {scanned} docs")
            return normalized, next_offset

    except Exception as e:
        logger.error(f"Client-side fallback error for '{q}': {e}")

    # 4) nothing found
    return [], ""
