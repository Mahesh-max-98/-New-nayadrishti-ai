"""Persistent, private image conversation and version history.

Images are stored in PostgreSQL BYTEA for this first deployment stage. No temporary
Render disk paths or public object URLs are used. A future object-storage adapter can
replace the byte storage without changing the conversation API.
"""
import hashlib
import uuid

from database import connection

MAX_STORED_IMAGE_BYTES = 20 * 1024 * 1024


def init_image_history_db():
    with connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS image_conversations (
                conversation_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT 'New image conversation',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                deleted_at TIMESTAMPTZ
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS image_versions (
                image_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES image_conversations(conversation_id),
                parent_image_id TEXT,
                version_number INTEGER NOT NULL,
                original_prompt TEXT NOT NULL,
                enhanced_prompt TEXT,
                edit_instruction TEXT,
                model_name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                image_sha256 TEXT NOT NULL,
                image_data BYTEA NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                deleted_at TIMESTAMPTZ,
                UNIQUE(conversation_id, version_number)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS image_uploads (
                upload_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                image_data BYTEA NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                deleted_at TIMESTAMPTZ
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS image_uploads_owner_created
            ON image_uploads(user_id, created_at DESC)
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS image_messages (
                message_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES image_conversations(conversation_id),
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
                content TEXT NOT NULL,
                image_id TEXT REFERENCES image_versions(image_id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS image_conversations_owner_updated
            ON image_conversations(user_id, updated_at DESC)
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS image_versions_conversation_created
            ON image_versions(conversation_id, created_at DESC)
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS image_messages_conversation_created
            ON image_messages(conversation_id, created_at ASC)
        """)


def create_conversation(user_id, title="New image conversation"):
    conversation_id = uuid.uuid4().hex
    clean_title = str(title or "New image conversation").strip()[:180]
    with connection() as db:
        db.execute(
            """INSERT INTO image_conversations (conversation_id, user_id, title)
               VALUES (%s, %s, %s)""",
            (conversation_id, str(user_id), clean_title or "New image conversation"),
        )
    return conversation_id


def _owned_conversation(db, conversation_id, user_id):
    return db.execute(
        """SELECT conversation_id, title FROM image_conversations
           WHERE conversation_id=%s AND user_id=%s AND deleted_at IS NULL""",
        (str(conversation_id), str(user_id)),
    ).fetchone()


def add_message(conversation_id, user_id, role, content, image_id=None):
    if role not in ("user", "assistant", "system"):
        raise ValueError("Invalid message role.")
    clean_content = str(content or "").strip()[:12000]
    if not clean_content:
        return None
    message_id = uuid.uuid4().hex
    with connection() as db:
        if not _owned_conversation(db, conversation_id, user_id):
            raise LookupError("Conversation not found.")
        db.execute(
            """INSERT INTO image_messages
               (message_id, conversation_id, role, content, image_id)
               VALUES (%s, %s, %s, %s, %s)""",
            (message_id, conversation_id, role, clean_content, image_id),
        )
        db.execute(
            "UPDATE image_conversations SET updated_at=CURRENT_TIMESTAMP WHERE conversation_id=%s",
            (conversation_id,),
        )
    return message_id


def save_image_version(
    conversation_id, user_id, image_bytes, original_prompt, model_name,
    enhanced_prompt="", edit_instruction="", parent_image_id=None, mime_type="image/png"
):
    if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
        raise ValueError("Generated image data is empty.")
    if len(image_bytes) > MAX_STORED_IMAGE_BYTES:
        raise ValueError("Generated image exceeds the 20 MB storage limit.")

    from PIL import Image, UnidentifiedImageError
    import io
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.verify()
        with Image.open(io.BytesIO(image_bytes)) as image:
            width, height = image.size
            actual_format = (image.format or "").upper()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Generated image is not a valid image file.") from exc

    format_mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
    actual_mime = format_mime.get(actual_format)
    if not actual_mime:
        raise ValueError("Only PNG, JPEG and WebP generated images can be saved.")
    image_id = uuid.uuid4().hex
    digest = hashlib.sha256(image_bytes).hexdigest()

    with connection() as db:
        conversation = _owned_conversation(db, conversation_id, user_id)
        if not conversation:
            raise LookupError("Conversation not found.")
        if parent_image_id:
            parent = db.execute(
                """SELECT image_id FROM image_versions
                   WHERE image_id=%s AND conversation_id=%s AND deleted_at IS NULL""",
                (str(parent_image_id), conversation_id),
            ).fetchone()
            if not parent:
                raise LookupError("The parent image version was not found in this conversation.")
        version = db.execute(
            """SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version
               FROM image_versions WHERE conversation_id=%s""",
            (conversation_id,),
        ).fetchone()["next_version"]
        db.execute(
            """INSERT INTO image_versions
               (image_id, conversation_id, parent_image_id, version_number,
                original_prompt, enhanced_prompt, edit_instruction, model_name,
                mime_type, width, height, image_sha256, image_data)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                image_id, conversation_id, parent_image_id, version,
                str(original_prompt or "")[:8000], str(enhanced_prompt or "")[:8000],
                str(edit_instruction or "")[:8000], str(model_name or "unknown")[:160],
                actual_mime, width, height, digest, bytes(image_bytes),
            ),
        )
        db.execute(
            "UPDATE image_conversations SET updated_at=CURRENT_TIMESTAMP WHERE conversation_id=%s",
            (conversation_id,),
        )
    return {
        "image_id": image_id, "conversation_id": conversation_id,
        "version_number": version, "width": width, "height": height,
        "mime_type": actual_mime, "sha256": digest, "model": model_name,
    }


def get_image(image_id, user_id):
    with connection() as db:
        row = db.execute(
            """SELECT v.image_id, v.conversation_id, v.mime_type, v.width, v.height,
                      v.version_number, v.parent_image_id, v.original_prompt,
                      v.enhanced_prompt, v.edit_instruction, v.model_name, v.image_data
               FROM image_versions v
               JOIN image_conversations c USING (conversation_id)
               WHERE v.image_id=%s AND c.user_id=%s
                 AND v.deleted_at IS NULL AND c.deleted_at IS NULL""",
            (str(image_id), str(user_id)),
        ).fetchone()
        return dict(row) if row else None


def list_images(user_id, limit=60):
    with connection() as db:
        rows = db.execute(
            """SELECT v.image_id, v.conversation_id, v.version_number,
                      v.parent_image_id, v.original_prompt, v.edit_instruction,
                      v.model_name, v.mime_type, v.width, v.height, v.created_at
               FROM image_versions v
               JOIN image_conversations c USING (conversation_id)
               WHERE c.user_id=%s AND c.deleted_at IS NULL AND v.deleted_at IS NULL
               ORDER BY v.created_at DESC LIMIT %s""",
            (str(user_id), max(1, min(int(limit), 100))),
        ).fetchall()
        return [dict(row) for row in rows]


def list_conversations(user_id, limit=40):
    with connection() as db:
        rows = db.execute(
            """SELECT c.conversation_id, c.title, c.created_at, c.updated_at,
                      (SELECT COUNT(*) FROM image_versions v
                       WHERE v.conversation_id=c.conversation_id AND v.deleted_at IS NULL) AS image_count
               FROM image_conversations c
               WHERE c.user_id=%s AND c.deleted_at IS NULL
               ORDER BY c.updated_at DESC LIMIT %s""",
            (str(user_id), max(1, min(int(limit), 100))),
        ).fetchall()
        return [dict(row) for row in rows]


def get_conversation(conversation_id, user_id):
    with connection() as db:
        conversation = _owned_conversation(db, conversation_id, user_id)
        if not conversation:
            return None
        messages = db.execute(
            """SELECT m.message_id, m.role, m.content, m.image_id, m.created_at,
                      v.version_number, v.mime_type, v.width, v.height
               FROM image_messages m
               LEFT JOIN image_versions v ON v.image_id=m.image_id AND v.deleted_at IS NULL
               WHERE m.conversation_id=%s ORDER BY m.created_at ASC""",
            (conversation_id,),
        ).fetchall()
        return {"conversation": dict(conversation), "messages": [dict(row) for row in messages]}


def delete_image(image_id, user_id):
    """Permanently delete one image version owned by this account."""
    with connection() as db:
        row = db.execute(
            """DELETE FROM image_versions v
               USING image_conversations c
               WHERE v.conversation_id=c.conversation_id AND v.image_id=%s
                 AND c.user_id=%s AND c.deleted_at IS NULL
               RETURNING v.image_id""",
            (str(image_id), str(user_id)),
        ).fetchone()
        return bool(row)


def delete_conversation(conversation_id, user_id):
    """Permanently delete a conversation, its messages and all its image bytes."""
    with connection() as db:
        row = db.execute(
            """SELECT conversation_id FROM image_conversations
               WHERE conversation_id=%s AND user_id=%s AND deleted_at IS NULL""",
            (str(conversation_id), str(user_id)),
        ).fetchone()
        if not row:
            return False
        db.execute("DELETE FROM image_messages WHERE conversation_id=%s", (str(conversation_id),))
        db.execute("DELETE FROM image_versions WHERE conversation_id=%s", (str(conversation_id),))
        db.execute("DELETE FROM image_conversations WHERE conversation_id=%s AND user_id=%s",
                   (str(conversation_id), str(user_id)))
        return True


def save_upload(user_id, filename, mime_type, image_bytes):
    """Keep source uploads separate from generated image versions."""
    if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
        raise ValueError("Uploaded image is empty.")
    if len(image_bytes) > 10 * 1024 * 1024:
        raise ValueError("Uploaded image exceeds the 10 MB limit.")
    from PIL import Image, UnidentifiedImageError
    import io
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.verify()
        with Image.open(io.BytesIO(image_bytes)) as image:
            if image.format not in ("JPEG", "PNG", "WEBP"):
                raise ValueError("Only JPG, PNG and WebP uploads are supported.")
            width, height = image.size
            actual_format = (image.format or "").upper()
            if width * height > 40000000:
                raise ValueError("Uploaded image dimensions are too large (maximum 40 megapixels).")
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("The uploaded file is not a valid image.") from exc
    canonical_mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(actual_format)
    if not canonical_mime:
        raise ValueError("Only JPG, PNG and WebP uploads are supported.")
    upload_id = uuid.uuid4().hex
    with connection() as db:
        db.execute(
            """INSERT INTO image_uploads
               (upload_id, user_id, filename, mime_type, width, height, image_data)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (upload_id, str(user_id), str(filename or "reference-image")[:180],
             canonical_mime, width, height, bytes(image_bytes)),
        )
    return {"upload_id": upload_id, "filename": str(filename or "reference-image")[:180],
            "mime_type": canonical_mime, "width": width, "height": height}


def get_upload(upload_id, user_id):
    with connection() as db:
        row = db.execute(
            """SELECT upload_id, filename, mime_type, width, height, image_data, created_at
               FROM image_uploads WHERE upload_id=%s AND user_id=%s AND deleted_at IS NULL""",
            (str(upload_id), str(user_id)),
        ).fetchone()
        return dict(row) if row else None


def delete_upload(upload_id, user_id):
    with connection() as db:
        row = db.execute(
            """DELETE FROM image_uploads
               WHERE upload_id=%s AND user_id=%s
               RETURNING upload_id""",
            (str(upload_id), str(user_id)),
        ).fetchone()
        return bool(row)
