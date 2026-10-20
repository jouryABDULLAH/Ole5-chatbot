"""Customer attachments. The Ticket API has no upload call, so the chat service keeps the files under <ATTACHMENTS_DIR>/<session id>/ and the ticket's internal note gets a link to each. A link is long and random but needs no sign-in: anyone who has it can open the file."""

import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from ole5_chat.config import get_settings

ALLOWED = {".png", ".jpg", ".jpeg", ".pdf", ".docx", ".xlsx", ".txt", ".log"}
ID = re.compile(r"^[A-Za-z0-9_-]{32}$")


class AttachmentRefused(Exception):
    """The file cannot be attached. status is the HTTP status to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _clean(name: str) -> str:
    name = Path(name.replace("\\", "/")).name.strip()
    name = re.sub(r"[^\w.\- ()]", "_", name)[-120:]
    return name or "file"


def save(session_id: str, file_name: str, data: bytes, already: int) -> dict:
    s = get_settings()
    name = _clean(file_name)
    if Path(name).suffix.lower() not in ALLOWED:
        raise AttachmentRefused(415, "Allowed file types: " + ", ".join(sorted(ALLOWED)))
    if len(data) > s.attachment_max_mb * 1024 * 1024:
        raise AttachmentRefused(413, f"A file can be at most {s.attachment_max_mb:g} MB")
    if already >= s.attachment_max_files:
        raise AttachmentRefused(409, f"At most {s.attachment_max_files} files per ticket")
    att_id = secrets.token_urlsafe(24)
    folder = s.attachments_dir / session_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{att_id}-{name}").write_bytes(data)
    return {"id": att_id, "file_name": name, "size": len(data),
            "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def find(att_id: str) -> Path | None:
    if not ID.match(att_id):
        return None
    return next(get_settings().attachments_dir.glob(f"*/{att_id}-*"), None)


def delete(att_id: str) -> None:
    path = find(att_id)
    if path:
        path.unlink(missing_ok=True)


def link(att_id: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/chat/attachments/{att_id}"
