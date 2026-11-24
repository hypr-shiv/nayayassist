"""
FastAPI application routing and HTTP interface for NyayaAssist.
Contains routing, request validation, and HTTP serialization ONLY (Architectural Rule 4.1).
ZERO business logic.
"""

import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Optional, Literal

from fastapi import FastAPI, UploadFile, File, HTTPException, status, Query, Response
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app import config, parser, analyzer, retrieval, nlp
from app.models import AnalysisResult, Answer, QuestionRequest, DocTypeUpdateRequest
from app.calendar_export import generate_ics_calendar

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nyayaassist.api")

DOC_ID_REGEX = re.compile(r"^[0-9a-f]{32}$")


def validate_doc_id(doc_id: str) -> str:
    """Enforces 32-character hexadecimal doc_id to eliminate path traversal and wildcard exploits."""
    if not doc_id or not DOC_ID_REGEX.match(doc_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid doc_id format: must be a 32-character hexadecimal string."
        )
    return doc_id

app = FastAPI(
    title="NyayaAssist API",
    description="Local-First Legal Document Intelligence Platform for Indian Citizens",
    version=config.SCHEMA_VERSION
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
static_dir = config.BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def serve_index():
    index_file = config.BASE_DIR / "static" / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "NyayaAssist API is running. Mount static/index.html to view web interface."}


@app.get("/api/health")
def health_check():
    """Liveness check and active provider reporting."""
    return {
        "status": "healthy",
        "llm_provider": config.LLM_PROVIDER,
        "data_mode": config.DATA_MODE,
        "max_upload_mb": config.MAX_UPLOAD_MB,
        "disclaimer": config.DISCLAIMER
    }


@app.post("/api/documents", response_model=AnalysisResult)
def upload_and_analyze_document(file: UploadFile = File(...)):
    """
    Accepts PDF, PNG, or JPEG file upload, verifies magic signature,
    enforces streamed size limits, parses layout, and executes analysis pipeline.
    """
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No filename provided.")

    # 1. Verify File Magic Bytes & Signature (T-101 / FR-01)
    try:
        parser.verify_file_signature(file.file, file.filename)
    except ValueError as e:
        status_code = status.HTTP_400_BAD_REQUEST if "empty" in str(e).lower() else status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        raise HTTPException(
            status_code=status_code,
            detail=f"Unsupported or mismatched media type: {str(e)}"
        )

    # 2. Streamed Upload Size Enforcement (T-101 / TD-02 / FR-02)
    doc_id = uuid.uuid4().hex
    ext = Path(file.filename).suffix.lower()
    stored_path = config.UPLOADS_DIR / f"{doc_id}{ext}"
    
    total_bytes = 0
    chunk_size = 64 * 1024  # 64 KB increments
    
    with open(stored_path, "wb") as buffer:
        while True:
            chunk = file.file.read(chunk_size)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > config.MAX_UPLOAD_BYTES:
                # Remove partial file and reject
                buffer.close()
                if stored_path.exists():
                    stored_path.unlink()
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File exceeds maximum allowed upload size of {config.MAX_UPLOAD_MB} MB."
                )
            buffer.write(chunk)

    if total_bytes == 0:
        if stored_path.exists():
            stored_path.unlink()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty (0 bytes)."
        )

    # 3. Parse Document (Pages + Blocks with Normalised BBoxes)
    try:
        pages, blocks, warnings = parser.parse_document(stored_path, doc_id)
    except Exception as e:
        if stored_path.exists():
            stored_path.unlink()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to extract document contents: {str(e)}"
        )

    # 4. Analyze Document (Pipeline Orchestration)
    result, run_record = analyzer.analyze_document_pipeline(
        file_path=stored_path,
        doc_id=doc_id,
        filename=file.filename,
        pages=pages,
        blocks=blocks,
        warnings=warnings
    )

    # 5. Persist Analysis Record (JSON)
    record_path = config.RECORDS_DIR / f"{doc_id}.json"
    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(result.model_dump(), f, indent=2)

    return result


@app.get("/api/documents/{doc_id}", response_model=AnalysisResult)
def get_document_analysis(doc_id: str):
    """Retrieves full analysis record by doc_id."""
    clean_id = validate_doc_id(doc_id)
    record_path = config.RECORDS_DIR / f"{clean_id}.json"
    
    if not record_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document record not found.")

    with open(record_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return AnalysisResult(**data)


@app.get("/api/documents/{doc_id}/file")
def get_original_file(doc_id: str):
    """Serves the original uploaded file for the document canvas viewer."""
    clean_id = validate_doc_id(doc_id)
    
    # Locate file in uploads directory
    matched_files = list(config.UPLOADS_DIR.glob(f"{clean_id}.*"))
    if not matched_files or not matched_files[0].exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Original document file not found.")

    file_path = matched_files[0]
    media_type = "application/pdf" if file_path.suffix.lower() == ".pdf" else f"image/{file_path.suffix.lstrip('.')}"
    return FileResponse(file_path, media_type=media_type)


@app.post("/api/documents/{doc_id}/ask", response_model=Answer)
def ask_document_question(doc_id: str, req: QuestionRequest):
    """In-document Q&A with extractive fallback (FR-60..64)."""
    clean_id = validate_doc_id(doc_id)
    record_path = config.RECORDS_DIR / f"{clean_id}.json"
    
    if not record_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document record not found.")

    with open(record_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    analysis = AnalysisResult(**data)
    answer = analyzer.answer_question(analysis.blocks, req.question)
    return answer


@app.get("/api/documents/{doc_id}/calendar.ics")
def export_calendar(
    doc_id: str,
    kind: Literal["deadline", "all"] = Query("deadline", description="Whether to include deadlines only or all dates")
):
    """Exports response deadlines as an RFC 5545 .ics calendar file (FR-90, FR-91)."""
    clean_id = validate_doc_id(doc_id)
    record_path = config.RECORDS_DIR / f"{clean_id}.json"
    
    if not record_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document record not found.")

    with open(record_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    analysis = AnalysisResult(**data)
    
    try:
        ics_text = generate_ics_calendar(
            doc_id=analysis.doc_id,
            filename=analysis.filename,
            dates=analysis.dates,
            kind=kind
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    return Response(
        content=ics_text,
        media_type="text/calendar",
        headers={"Content-Disposition": f"attachment; filename=\"{analysis.doc_id}-deadlines.ics\""}
    )


@app.get("/api/statutes/search")
def search_statute_index(q: str = Query(..., min_length=2)):
    """Searches the verified Indian statutory index (FR-42)."""
    store = retrieval.get_statute_store()
    results = store.search(q, limit=10)
    return [
        {
            "id": s.id,
            "act": s.act,
            "section": s.section,
            "title": s.title,
            "verified": s.verified,
            "source_url": s.source_url,
            "text": s.text[:200] + "..."
        }
        for s in results
    ]


@app.patch("/api/documents/{doc_id}/type", response_model=AnalysisResult)
def update_document_type(doc_id: str, req: DocTypeUpdateRequest):
    """Allows user to correct the detected document type and refreshes procedural steps (FR-11)."""
    clean_id = validate_doc_id(doc_id)
    record_path = config.RECORDS_DIR / f"{clean_id}.json"
    
    if not record_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document record not found.")

    valid_types = [
        "cheque_dishonour_notice", "court_summons", "eviction_notice",
        "consumer_complaint_summons", "legal_notice", "tax_notice", "agreement", "unknown"
    ]
    if req.doc_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid doc_type '{req.doc_type}'. Must be one of {valid_types}."
        )

    with open(record_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    data["doc_type"] = req.doc_type
    data["type_confidence"] = 1.0  # User manually confirmed
    data["procedural_steps"] = nlp.get_procedural_steps(req.doc_type)

    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return AnalysisResult(**data)


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str):
    """Permanently deletes original upload and analysis records (FR-100)."""
    clean_id = validate_doc_id(doc_id)
    record_path = config.RECORDS_DIR / f"{clean_id}.json"
    matched_files = list(config.UPLOADS_DIR.glob(f"{clean_id}.*"))
    
    deleted = False
    if record_path.exists():
        record_path.unlink()
        deleted = True
        
    for f in matched_files:
        if f.exists():
            f.unlink()
            deleted = True
            
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found or already deleted.")
        
    return {"status": "success", "deleted": True, "doc_id": clean_id}
