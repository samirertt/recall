"""Attachment orchestration (Phase 6): save -> extract -> record. Extraction failure
never fails the upload (Section 52) — the ExtractedText row simply records `failed`.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timing import log_duration
from app.models.attachment import Attachment, ExtractedText
from app.models.enums import ExtractionStatus
from app.services.attachments.extraction import ExtractionResult, extract_text_attachment
from app.services.attachments.storage import MAX_ATTACHMENT_BYTES, save_attachment_bytes


class AttachmentTooLarge(ValueError):
    pass


async def add_attachment(
    session: AsyncSession,
    incident_id: int,
    filename: str,
    data: bytes,
    mime_type: str | None,
) -> Attachment:
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentTooLarge(f"Attachment exceeds {MAX_ATTACHMENT_BYTES} byte limit")

    relative_path, sha256 = save_attachment_bytes(incident_id, filename, data)
    attachment = Attachment(
        incident_id=incident_id,
        filename=filename,
        relative_path=relative_path,
        mime_type=mime_type,
        size_bytes=len(data),
        sha256=sha256,
    )
    session.add(attachment)
    await session.flush()

    try:
        with log_duration("extract_attachment", attachment_id=attachment.id, size_bytes=len(data)):
            result = extract_text_attachment(filename, data)
    except Exception as exc:  # extractor contract says it won't raise — don't trust blindly
        result = ExtractionResult(
            status=ExtractionStatus.failed,
            text=None,
            confidence=None,
            engine=None,
            engine_version=None,
            error=str(exc),
        )

    session.add(
        ExtractedText(
            attachment_id=attachment.id,
            status=result.status,
            text=result.text,
            confidence=result.confidence,
            extractor_engine=result.engine,
            extractor_version=result.engine_version,
            source_attachment_hash=sha256,
            error=result.error,
            extracted_at=datetime.now(UTC),
        )
    )
    await session.commit()
    await session.refresh(attachment)
    return attachment


async def list_attachments(session: AsyncSession, incident_id: int) -> list[Attachment]:
    result = await session.execute(
        select(Attachment).where(Attachment.incident_id == incident_id)
    )
    return list(result.scalars().all())


async def get_attachment(session: AsyncSession, attachment_id: int) -> Attachment | None:
    result = await session.execute(select(Attachment).where(Attachment.id == attachment_id))
    return result.scalar_one_or_none()


async def get_extracted_text(session: AsyncSession, attachment_id: int) -> ExtractedText | None:
    result = await session.execute(
        select(ExtractedText)
        .where(ExtractedText.attachment_id == attachment_id)
        .order_by(ExtractedText.id.desc())
    )
    return result.scalars().first()
