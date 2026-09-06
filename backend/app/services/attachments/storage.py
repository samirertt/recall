"""Filesystem storage for attachments (Section 16). The on-disk filename is always
server-generated — never derived from the user-supplied filename — so there is no
path-traversal surface even before the defense-in-depth check in `read_attachment_bytes`.
"""

import hashlib
import uuid
from pathlib import Path

from app.core.config import get_settings

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # generous for logs/screenshots/PDFs at personal scale


def _safe_extension(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if 0 < len(suffix) <= 10 and all(c.isalnum() or c == "." for c in suffix):
        return suffix
    return ""


def save_attachment_bytes(incident_id: int, filename: str, data: bytes) -> tuple[str, str]:
    """Writes bytes to disk and returns (relative_path, sha256)."""
    settings = get_settings()
    sha256 = hashlib.sha256(data).hexdigest()
    stored_name = f"{uuid.uuid4().hex}{_safe_extension(filename)}"
    incident_dir = settings.attachments_dir / str(incident_id)
    incident_dir.mkdir(parents=True, exist_ok=True)
    (incident_dir / stored_name).write_bytes(data)
    return f"{incident_id}/{stored_name}", sha256


def read_attachment_bytes(relative_path: str) -> bytes:
    settings = get_settings()
    base = settings.attachments_dir.resolve()
    path = (settings.attachments_dir / relative_path).resolve()
    if not str(path).startswith(str(base)):
        raise ValueError("Invalid attachment path")  # defense in depth
    return path.read_bytes()
