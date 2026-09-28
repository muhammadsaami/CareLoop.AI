"""
CareLoop AI — DischargeDocument Routes (Phase 2)

Endpoints:
  POST /api/v1/discharge-documents                        upload + process
  GET  /api/v1/discharge-documents/{document_id}          fetch one
  GET  /api/v1/patients/{patient_id}/discharge-documents  list for patient
  POST /api/v1/discharge-documents/{document_id}/reprocess re-run extraction

Uploads use multipart/form-data.  Only PDF, PNG, JPG, and JPEG are accepted.
"""
from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, File, Form, UploadFile, status

from app.api.auth_deps import (
    require_discharge_upload_patient_access,
    require_patient_access,
)
from app.api.deps import DischargeDocumentServiceDep
from app.core.logging import get_logger
from app.schemas.discharge_document import (
    DischargeDocumentDetailResponse,
    DischargeDocumentResponse,
)
from app.schemas.extraction import ExtractionResultResponse

logger = get_logger(__name__)

router = APIRouter(tags=["Discharge Documents"])


# ── Upload ──────────────────────────────────────────────────────────────────

@router.post(
    "/discharge-documents",
    response_model=ExtractionResultResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a discharge document and extract structured data",
    responses={
        400: {"description": "Unsupported file type or page limit exceeded"},
        404: {"description": "Patient not found"},
        409: {"description": "Duplicate document for this patient"},
        413: {"description": "File exceeds the maximum upload size"},
        422: {"description": "Document is corrupted or unreadable"},
        503: {"description": "OCR or extraction provider not configured"},
    },
    dependencies=[Depends(require_discharge_upload_patient_access)],
)
def upload_discharge_document(
    service: DischargeDocumentServiceDep,
    patient_id: uuid.UUID = Form(..., description="Existing patient UUID"),
    file: UploadFile = File(..., description="PDF, PNG, JPG, or JPEG"),
) -> ExtractionResultResponse:
    """
    Upload a discharge summary and run the full ingestion pipeline.

    The file is validated, stored under a UUID-based filename, and then
    processed.  Patients are never created automatically: `patient_id`
    must reference an existing patient.
    """
    data = file.file.read()
    return service.upload_and_process(
        patient_id=patient_id,
        filename=file.filename,
        content_type=file.content_type,
        data=data,
    )


# ── Query ───────────────────────────────────────────────────────────────────

@router.get(
    "/discharge-documents/{document_id}",
    response_model=DischargeDocumentDetailResponse,
    summary="Get a discharge document and its extraction audit trail",
    responses={404: {"description": "Document not found"}},
    dependencies=[Depends(require_patient_access)],
)
def get_discharge_document(
    document_id: uuid.UUID, service: DischargeDocumentServiceDep
) -> DischargeDocumentDetailResponse:
    """Return one discharge document plus its extraction run history."""
    document = service.get_document(document_id)
    return DischargeDocumentDetailResponse.model_validate(document)


@router.get(
    "/patients/{patient_id}/discharge-documents",
    response_model=List[DischargeDocumentResponse],
    summary="List discharge documents for a patient",
    responses={404: {"description": "Patient not found"}},
    dependencies=[Depends(require_patient_access)],
)
def list_patient_discharge_documents(
    patient_id: uuid.UUID, service: DischargeDocumentServiceDep
) -> List[DischargeDocumentResponse]:
    """Return all discharge documents uploaded for the given patient."""
    documents = service.list_for_patient(patient_id)
    return [DischargeDocumentResponse.model_validate(doc) for doc in documents]


# ── Reprocess ───────────────────────────────────────────────────────────────

@router.post(
    "/discharge-documents/{document_id}/reprocess",
    response_model=ExtractionResultResponse,
    summary="Re-run processing and extraction for an existing document",
    responses={
        404: {"description": "Document not found"},
        422: {"description": "Document is corrupted or unreadable"},
        503: {"description": "OCR or extraction provider not configured"},
    },
    dependencies=[Depends(require_patient_access)],
)
def reprocess_discharge_document(
    document_id: uuid.UUID, service: DischargeDocumentServiceDep
) -> ExtractionResultResponse:
    """
    Re-read a stored document and re-run the pipeline.

    Useful after Tesseract has been installed or a provider API key added.
    """
    return service.reprocess(document_id)
