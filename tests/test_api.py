"""
API and integration tests for FastAPI routing (Phase 5 & 7).
Verifies INV-07, T-101 (magic signature & upload rejection).
Synchronous execution for pure determinism and zero threading latency.
"""

import io
import pytest
from fastapi import HTTPException, UploadFile
from app.main import health_check, search_statute_index, upload_and_analyze_document
from app import config


def test_health_check_endpoint():
    # INV-07: Disclaimer present
    data = health_check()
    assert data["status"] == "healthy"
    assert data["disclaimer"] == config.DISCLAIMER


def test_statute_search_api():
    results = search_statute_index("cheque bounce")
    assert len(results) > 0
    assert any(r["id"] == "NIA-138" for r in results)


def test_magic_signature_rejects_spoofed_pdf():
    # T-101 / TD-01: Reject file with PDF extension but non-PDF bytes
    fake_pdf = io.BytesIO(b"This is just plain text, not a real PDF file!")
    upload_file = UploadFile(filename="spoofed.pdf", file=fake_pdf)
    with pytest.raises(HTTPException) as exc_info:
        upload_and_analyze_document(upload_file)
    assert exc_info.value.status_code == 415
    assert "Unsupported or mismatched media type" in exc_info.value.detail
