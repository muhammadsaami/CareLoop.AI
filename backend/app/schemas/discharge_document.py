"""
CareLoop AI — DischargeDocument Pydantic Schemas (Phase 2)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.discharge_document import (
    DocumentType,
    ExtractionStatus,
    OcrStatus,
    ProcessingStatus,
)
from app.models.extraction_run import ExtractionRunStatus


class ExtractionRunResponse(BaseModel):
    """Audit record for one extraction attempt.

    By construction this cannot leak credentials: the model has no field
    for an API key, authorization header, prompt, or document text.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    provider: str
    model: str
    started_at: datetime
    completed_at: Optional[datetime]
    status: ExtractionRunStatus
    raw_ocr_character_count: Optional[int]
    extraction_error: Optional[str]
    validation_error: Optional[str]


class DischargeDocumentResponse(BaseModel):
    """Full discharge document record.

    `extracted_text` is intentionally NOT exposed here.  It contains raw
    protected health information and is only needed server-side for
    reprocessing.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    original_filename: str
    content_type: str
    file_size: int
    sha256_hash: str
    document_type: DocumentType
    processing_status: ProcessingStatus
    ocr_status: OcrStatus
    extraction_status: ExtractionStatus
    page_count: Optional[int]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime


class DischargeDocumentDetailResponse(DischargeDocumentResponse):
    """Document record plus its extraction audit trail."""

    extraction_runs: List[ExtractionRunResponse] = Field(default_factory=list)
