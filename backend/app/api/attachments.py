from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.attachment import AttachmentRead, ExtractedTextRead
from app.services.attachments import service as attachments_service
from app.services.attachments.service import AttachmentTooLarge
from app.services.attachments.storage import read_attachment_bytes
from app.services.incidents import service as incidents_service

router = APIRouter(tags=["attachments"])


@router.post(
    "/incidents/{incident_id}/attachments", response_model=AttachmentRead, status_code=201
)
async def upload_attachment(
    incident_id: int, file: UploadFile, session: AsyncSession = Depends(get_db)
) -> AttachmentRead:
    incident = await incidents_service.get_incident(session, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    data = await file.read()
    try:
        attachment = await attachments_service.add_attachment(
            session, incident_id, file.filename or "upload.bin", data, file.content_type
        )
    except AttachmentTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return attachment


@router.get("/incidents/{incident_id}/attachments", response_model=list[AttachmentRead])
async def list_attachments(
    incident_id: int, session: AsyncSession = Depends(get_db)
) -> list[AttachmentRead]:
    return await attachments_service.list_attachments(session, incident_id)


@router.get("/attachments/{attachment_id}/content")
async def download_attachment(attachment_id: int, session: AsyncSession = Depends(get_db)):
    attachment = await attachments_service.get_attachment(session, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    data = read_attachment_bytes(attachment.relative_path)
    media_type = attachment.mime_type or "application/octet-stream"
    return Response(content=data, media_type=media_type)


@router.get("/attachments/{attachment_id}/extracted-text", response_model=ExtractedTextRead)
async def get_extracted_text(
    attachment_id: int, session: AsyncSession = Depends(get_db)
) -> ExtractedTextRead:
    extracted = await attachments_service.get_extracted_text(session, attachment_id)
    if extracted is None:
        raise HTTPException(status_code=404, detail="No extraction record for this attachment")
    return extracted
