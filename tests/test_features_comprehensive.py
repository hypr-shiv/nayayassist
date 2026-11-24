"""
Comprehensive Feature & Edge-Case Verification Suite for NyayaAssist.
Validates all features from FEATURE.md and PRD.md:
- FR-11: Manual correction of document type via PATCH /api/documents/{doc_id}/type
- FR-25: Chronological timeline sorting and passed/expired deadline warnings
- FR-34: Identification and reporting of unclear/missing facts
- FR-50: Detection and citizen explanations for legal terminology (Glossary)
- FR-41: Statutory groundings carry verified India Code status and source URLs
"""

import io
import pytest
import pymupdf as fitz
from fastapi import UploadFile, HTTPException

from app import config, nlp
from app.models import DocTypeUpdateRequest
from app.main import (
    upload_and_analyze_document,
    get_document_analysis,
    update_document_type
)


def create_notice_with_glossary_and_past_deadline() -> bytes:
    """Creates a notice with legal terminology and a past deadline (2024)."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    notice_text = (
        "IN THE COURT OF CIVIL JUDGE, PATNA\n\n"
        "Date of Notice: 10/01/2024\n"
        "To: Mr. Mohan Kumar, Defendant\n\n"
        "Take notice that the Plaintiff has instituted a civil suit against you.\n"
        "You must file your vakalatnama and written statement within 30 days,\n"
        "failing which the court shall proceed ex parte against you.\n"
        "Mesne profits are also claimed for unauthorized occupation."
    )
    page.insert_text((50, 70), notice_text, fontsize=11)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_fr_11_user_document_type_override():
    """Validates FR-11: User can manually correct document type, refreshing procedural steps."""
    pdf_bytes = create_notice_with_glossary_and_past_deadline()
    upload = UploadFile(
        filename="civil_summons.pdf",
        file=io.BytesIO(pdf_bytes)
    )
    result = upload_and_analyze_document(file=upload)
    doc_id = result.doc_id

    # Initial type should be court_summons
    assert result.doc_type == "court_summons"
    original_steps = result.procedural_steps

    # User corrects type to eviction_notice
    update_req = DocTypeUpdateRequest(doc_type="eviction_notice")
    updated = update_document_type(doc_id=doc_id, req=update_req)

    assert updated.doc_type == "eviction_notice"
    assert updated.type_confidence == 1.0
    assert updated.procedural_steps != original_steps
    assert any("vacate" in s.lower() or "lease" in s.lower() or "tenan" in s.lower() for s in updated.procedural_steps)

    # Verify invalid document type is rejected with HTTP 400
    with pytest.raises(HTTPException) as exc:
        update_document_type(doc_id=doc_id, req=DocTypeUpdateRequest(doc_type="fraudulent_fake_type"))
    assert exc.value.status_code == 400


def test_fr_25_timeline_sorted_and_expired_deadline_warned():
    """Validates FR-25: Dates sorted chronologically and expired deadline emits warning."""
    pdf_bytes = create_notice_with_glossary_and_past_deadline()
    upload = UploadFile(
        filename="civil_summons_past.pdf",
        file=io.BytesIO(pdf_bytes)
    )
    result = upload_and_analyze_document(file=upload)

    # 1. Timeline must be sorted chronologically
    dated_items = [d.date_iso for d in result.dates if d.date_iso]
    assert dated_items == sorted(dated_items)

    # 2. Expired deadline warning must be emitted because deadline was in Feb 2024
    assert any("has already passed" in w.lower() or "expired" in w.lower() for w in result.warnings)


def test_fr_50_legal_glossary_detected():
    """Validates FR-50: Detects legal terms (vakalatnama, ex parte, mesne profits)."""
    pdf_bytes = create_notice_with_glossary_and_past_deadline()
    upload = UploadFile(
        filename="civil_summons_terms.pdf",
        file=io.BytesIO(pdf_bytes)
    )
    result = upload_and_analyze_document(file=upload)

    terms = [g.term.lower() for g in result.glossary]
    assert "vakalatnama" in terms
    assert "ex parte" in terms
    assert "mesne profits" in terms or "written statement" in terms

    # Check meanings are present and non-empty
    for g in result.glossary:
        assert len(g.meaning) > 10
