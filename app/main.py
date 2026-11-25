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

from fastapi import FastAPI, UploadFile, File, HTTPException, status, Query, Response, Request, Depends
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app import config, parser, analyzer, retrieval, nlp, auth
from app.models import (
    AnalysisResult, Answer, QuestionRequest, DocTypeUpdateRequest,
    FirebasePublicConfig, FirebaseSessionRequest, AuthStatusResponse, AuthUser
)
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


def require_authenticated_user(request: Request) -> AuthUser:
    """Requires a valid HMAC-signed session issued from verified Firebase Auth."""
    cookie_val = request.cookies.get(config.SESSION_COOKIE_NAME)
    user = auth.verify_session_cookie(cookie_val)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign in to use NyayaAssist."
        )
    return user


def _load_authorized_record(doc_id: str, current_user: AuthUser):
    """Validates doc_id format, existence, and strict owner isolation (TD-14)."""
    clean_id = validate_doc_id(doc_id)
    record_path = config.RECORDS_DIR / f"{clean_id}.json"
    if not record_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document record not found.")

    with open(record_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("owner_uid_hash") != current_user.uid_hash:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: document does not belong to your authenticated session."
        )
    return clean_id, record_path, data

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


@app.get("/login")
def serve_login():
    """Serves the Google Firebase authentication login page."""
    login_file = config.BASE_DIR / "static" / "login.html"
    if login_file.exists():
        return FileResponse(login_file)
    raise HTTPException(status_code=404, detail="Login page not found.")


@app.get("/logout")
def serve_logout():
    """Serves the Firebase sign-out and session revocation confirmation page."""
    logout_file = config.BASE_DIR / "static" / "logout.html"
    if logout_file.exists():
        return FileResponse(logout_file)
    raise HTTPException(status_code=404, detail="Logout page not found.")


@app.get("/api/health")
def health_check():
    """Liveness check and active provider reporting."""
    return {
        "status": "healthy",
        "llm_provider": config.LLM_PROVIDER,
        "data_mode": config.DATA_MODE,
        "max_upload_mb": config.MAX_UPLOAD_MB,
        "firebase_configured": auth.is_firebase_configured(),
        "disclaimer": config.DISCLAIMER
    }


@app.get("/api/auth/config", response_model=FirebasePublicConfig)
def get_auth_config():
    """Returns public Firebase Web SDK configuration for client initialization."""
    return auth.get_public_firebase_config()


@app.get("/api/auth/me", response_model=AuthStatusResponse)
def get_current_user(request: Request):
    """Checks the signed session cookie and returns active user status."""
    cookie_val = request.cookies.get(config.SESSION_COOKIE_NAME)
    user = auth.verify_session_cookie(cookie_val)
    return AuthStatusResponse(
        authenticated=user is not None,
        firebase_configured=auth.is_firebase_configured(),
        user=user,
        disclaimer=config.DISCLAIMER
    )


@app.post("/api/auth/session", response_model=AuthStatusResponse)
def create_firebase_session(req: FirebaseSessionRequest, request: Request, response: Response):
    """
    Exchanges a verified Google Firebase ID token for an HttpOnly HMAC-signed
    NyayaAssist session cookie after confirming DPDPA §6 consent.
    """
    if not req.consent_accepted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Explicit DPDPA privacy & legal-information-only consent must be accepted before sign-in."
        )

    try:
        verified_user = auth.verify_firebase_id_token(req.id_token)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    cookie_value, auth_user = auth.create_session_token(
        verified_user=verified_user,
        consent_version=req.consent_version
    )

    is_https = request.url.scheme == "https"
    response.set_cookie(
        key=config.SESSION_COOKIE_NAME,
        value=cookie_value,
        max_age=config.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=is_https,
        path="/"
    )

    return AuthStatusResponse(
        authenticated=True,
        firebase_configured=True,
        user=auth_user,
        disclaimer=config.DISCLAIMER
    )


@app.post("/api/auth/logout")
def logout_session(request: Request, response: Response):
    """Revokes server-side session and clears the nyaya_session cookie."""
    cookie_val = request.cookies.get(config.SESSION_COOKIE_NAME)
    revoked = auth.revoke_session_cookie(cookie_val)
    response.delete_cookie(key=config.SESSION_COOKIE_NAME, path="/")
    return {
        "status": "logged_out",
        "session_revoked": revoked,
        "disclaimer": config.DISCLAIMER
    }


@app.post("/api/documents", response_model=AnalysisResult)
def upload_and_analyze_document(
    file: UploadFile = File(...),
    current_user: AuthUser = Depends(require_authenticated_user)
):
    """
    Accepts PDF, PNG, or JPEG file upload, verifies magic signature,
    enforces streamed size limits, parses layout, and executes analysis pipeline.
    Requires authenticated session.
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
    result.owner_uid_hash = current_user.uid_hash

    # 5. Persist Analysis Record (JSON)
    record_path = config.RECORDS_DIR / f"{doc_id}.json"
    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(result.model_dump(), f, indent=2)

    return result


@app.get("/api/documents/{doc_id}", response_model=AnalysisResult)
def get_document_analysis(
    doc_id: str,
    current_user: AuthUser = Depends(require_authenticated_user)
):
    """Retrieves full analysis record by doc_id (authenticated owner only)."""
    _, _, data = _load_authorized_record(doc_id, current_user)
    return AnalysisResult(**data)


@app.get("/api/documents/{doc_id}/file")
def get_original_file(
    doc_id: str,
    current_user: AuthUser = Depends(require_authenticated_user)
):
    """Serves the original uploaded file for the document canvas viewer (authenticated owner only)."""
    clean_id, _, _ = _load_authorized_record(doc_id, current_user)

    # Locate file in uploads directory
    matched_files = list(config.UPLOADS_DIR.glob(f"{clean_id}.*"))
    if not matched_files or not matched_files[0].exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Original document file not found.")

    file_path = matched_files[0]
    media_type = "application/pdf" if file_path.suffix.lower() == ".pdf" else f"image/{file_path.suffix.lstrip('.')}"
    return FileResponse(file_path, media_type=media_type)


@app.post("/api/documents/{doc_id}/ask", response_model=Answer)
def ask_document_question(
    doc_id: str,
    req: QuestionRequest,
    current_user: AuthUser = Depends(require_authenticated_user)
):
    """In-document Q&A with extractive fallback (FR-60..64, authenticated owner only)."""
    _, _, data = _load_authorized_record(doc_id, current_user)
    analysis = AnalysisResult(**data)
    answer = analyzer.answer_question(analysis.blocks, req.question)
    return answer


@app.get("/api/documents/{doc_id}/calendar.ics")
def export_calendar(
    doc_id: str,
    kind: Literal["deadline", "all"] = Query("deadline", description="Whether to include deadlines only or all dates"),
    current_user: AuthUser = Depends(require_authenticated_user)
):
    """Exports response deadlines as an RFC 5545 .ics calendar file (FR-90, FR-91, authenticated owner only)."""
    _, _, data = _load_authorized_record(doc_id, current_user)
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
def update_document_type(
    doc_id: str,
    req: DocTypeUpdateRequest,
    current_user: AuthUser = Depends(require_authenticated_user)
):
    """Allows user to correct the detected document type and refreshes procedural steps (FR-11)."""
    _, record_path, data = _load_authorized_record(doc_id, current_user)

    valid_types = [
        "cheque_dishonour_notice", "court_summons", "eviction_notice",
        "consumer_complaint_summons", "legal_notice", "tax_notice", "agreement", "unknown"
    ]
    if req.doc_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid doc_type '{req.doc_type}'. Must be one of {valid_types}."
        )

    data["doc_type"] = req.doc_type
    data["type_confidence"] = 1.0  # User manually confirmed
    data["procedural_steps"] = nlp.get_procedural_steps(req.doc_type)

    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return AnalysisResult(**data)


@app.get("/api/samples")
def list_sample_documents():
    """Lists curated sample legal notices for live demonstration."""
    gold_path = config.BASE_DIR / "eval" / "gold_samples.json"
    if not gold_path.exists():
        return []
    with open(gold_path, "r", encoding="utf-8") as f:
        samples = json.load(f)
    return [
        {
            "id": s["id"],
            "title": s["title"],
            "doc_type": s["expected_doc_type"],
            "statute": s["expected_statute"],
            "has_relative_deadline": s.get("has_relative_deadline", False),
            "preview": s["raw_text"][:140] + "..."
        }
        for s in samples
    ]


@app.post("/api/samples/{sample_id}/load", response_model=AnalysisResult)
def load_sample_document(
    sample_id: str,
    current_user: AuthUser = Depends(require_authenticated_user)
):
    """
    Synthesizes a PDF from a curated sample legal notice and executes
    the complete deterministic analysis pipeline, returning the AnalysisResult.
    Requires authenticated session.
    """
    gold_path = config.BASE_DIR / "eval" / "gold_samples.json"
    if not gold_path.exists():
        raise HTTPException(status_code=404, detail="Sample database not found.")
    with open(gold_path, "r", encoding="utf-8") as f:
        samples = json.load(f)

    matched = next((s for s in samples if s["id"] == sample_id), None)
    if not matched:
        raise HTTPException(status_code=404, detail=f"Sample '{sample_id}' not found.")

    import pymupdf as fitz
    doc_id = uuid.uuid4().hex
    filename = f"{sample_id}.pdf"
    pdf_path = config.UPLOADS_DIR / f"{doc_id}.pdf"

    # Generate PDF with clean typography and layout
    doc = fitz.open()
    page = doc.new_page(width=595.28, height=841.89)  # standard A4
    rect = fitz.Rect(54, 72, 541, 769)
    page.insert_textbox(rect, matched["raw_text"], fontsize=11, fontname="helv", align=fitz.TEXT_ALIGN_LEFT)
    doc.save(pdf_path)
    doc.close()

    # Parse layout and analyze
    pages, blocks, warnings = parser.parse_document(pdf_path, doc_id)
    analysis_result, analysis_run = analyzer.analyze_document_pipeline(
        file_path=pdf_path,
        doc_id=doc_id,
        filename=filename,
        pages=pages,
        blocks=blocks,
        warnings=warnings
    )
    analysis_result.owner_uid_hash = current_user.uid_hash

    record_path = config.RECORDS_DIR / f"{doc_id}.json"
    with open(record_path, "w", encoding="utf-8") as f:
        f.write(analysis_result.model_dump_json(indent=2))

    return analysis_result


@app.delete("/api/documents/{doc_id}")
def delete_document(
    doc_id: str,
    current_user: AuthUser = Depends(require_authenticated_user)
):
    """Permanently deletes original upload and analysis records (FR-100, authenticated owner only)."""
    clean_id, record_path, _ = _load_authorized_record(doc_id, current_user)
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
