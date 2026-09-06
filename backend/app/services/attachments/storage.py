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


# Section 53: never trust the stored mime_type enough to render it inline without
# thought — it's the *uploader's* browser-supplied Content-Type at upload time, not
# something we've verified. Serving an uploaded text/html (or image/svg+xml, which
# can embed <script>) file back with its own claimed content-type and no
# Content-Disposition would let a stored HTML/SVG payload execute at this app's own
# origin when opened — a stored-XSS vector, not merely a display quirk.
_INLINE_SAFE_MIME_PREFIXES = ("image/", "audio/", "video/")
_INLINE_SAFE_MIME_EXACT = {"application/pdf", "text/plain"}
_INLINE_UNSAFE_MIME_EXACT = {"image/svg+xml"}  # can embed <script> despite the image/ prefix


def _sanitize_header_value(value: str) -> str:
    """Strips characters that could enable HTTP header injection or break the
    Content-Disposition quoting — filenames here originate from the uploader's
    browser and are otherwise untrusted."""
    return "".join(c for c in value if c not in ('"', "\r", "\n", "\x00")) or "attachment"


def safe_download_headers(mime_type: str | None, filename: str) -> tuple[str, dict[str, str]]:
    """Returns (media_type, extra_headers) safe to serve back to a browser."""
    mime = mime_type or "application/octet-stream"
    inline_safe = (
        mime.startswith(_INLINE_SAFE_MIME_PREFIXES) or mime in _INLINE_SAFE_MIME_EXACT
    ) and mime not in _INLINE_UNSAFE_MIME_EXACT

    disposition = "inline" if inline_safe else "attachment"
    safe_filename = _sanitize_header_value(filename)
    return mime, {
        "Content-Disposition": f'{disposition}; filename="{safe_filename}"',
        "X-Content-Type-Options": "nosniff",
    }
