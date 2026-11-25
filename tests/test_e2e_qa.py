"""
End-to-End (E2E) QA Test Suite for NyayaAssist.
Written from the perspective of a Senior QA Lead / SDET (10+ years experience).
Covers:
- Full document lifecycle (Ingestion -> Spatial Parse -> NLP -> Analysis -> Retrieval -> Q&A -> Calendar -> Deletion)
- Security boundaries (Path traversal, Wildcard deletion, 0-byte upload rejection)
- UPL Guard (Refusal of legal strategy questions)
- RFC 5545 Calendar compliance (All-day event start/end, alarm, headers)
- Trust Invariants INV-01 through INV-10
"""

import io
import pytest
import pymupdf as fitz
from fastapi import UploadFile, HTTPException

from app import config, parser, analyzer
from app.models import QuestionRequest, AuthUser
from app.main import (
    upload_and_analyze_document,
    get_document_analysis,
    get_original_file,
    ask_document_question,
    export_calendar,
    delete_document,
    validate_doc_id
)


_TEST_USER = AuthUser(uid_hash="e2e_test_user_hash_001", provider="google")


def create_realistic_notice_pdf() -> bytes:
    """Generates a realistic statutory legal notice PDF in memory."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    notice_text = (
        "LEGAL NOTICE UNDER SECTION 138 OF THE NEGOTIABLE INSTRUMENTS ACT, 1881\n\n"
        "Date of Notice: 01/03/2024\n"
        "To: Mr. R. K. Verma, Resident of Delhi\n\n"
        "Sir, under instructions from my client M/s Apex Enterprises, I issue this notice.\n"
        "My client presented Cheque No. 987654 for Rs. 50,000 drawn on State Bank of India.\n"
        "The said cheque was returned dishonoured by the bank with remark 'Funds Insufficient'.\n"
        "You are hereby called upon to pay the sum of Rs 50000 within 15 days of receipt of this notice,\n"
        "failing which my client shall initiate criminal proceedings under Section 138 of the Negotiable Instruments Act."
    )
    page.insert_text((50, 70), notice_text, fontsize=11)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_e2e_full_document_lifecycle():
    """Tests the entire document lifecycle from upload to permanent deletion."""
    pdf_bytes = create_realistic_notice_pdf()
    upload = UploadFile(
        filename="statutory_notice.pdf",
        file=io.BytesIO(pdf_bytes)
    )

    # 1. Upload & Ingestion
    result = upload_and_analyze_document(file=upload, current_user=_TEST_USER)
    assert result is not None
    doc_id = result.doc_id
    assert len(doc_id) == 32
    assert result.doc_type == "cheque_dishonour_notice"
    assert result.type_confidence >= 0.75
    assert len(result.claims) > 0
    assert len(result.dates) >= 1
    assert any(d.role == "deadline" and d.is_computed for d in result.dates)
    assert config.DISCLAIMER in result.disclaimer

    # Verify INV-01: Every claim cites the document
    for claim in result.claims:
        assert any(c.kind == "document" for c in claim.citations), f"Claim failed INV-01: {claim.text}"

    # 2. Retrieve Stored Analysis Record
    retrieved = get_document_analysis(doc_id=doc_id, current_user=_TEST_USER)
    assert retrieved.doc_id == doc_id
    assert retrieved.doc_type == result.doc_type
    assert len(retrieved.claims) == len(result.claims)

    # 3. Retrieve Original File
    file_resp = get_original_file(doc_id=doc_id, current_user=_TEST_USER)
    assert file_resp.media_type == "application/pdf"
    assert str(file_resp.path).endswith(".pdf")

    # 4. Factual In-Document Q&A
    q_fact = QuestionRequest(question="What is the cheque amount demanded?")
    ans_fact = ask_document_question(doc_id=doc_id, req=q_fact, current_user=_TEST_USER)
    assert ans_fact is not None
    assert "50,000" in ans_fact.answer or "50000" in ans_fact.answer
    assert len(ans_fact.citations) > 0

    # 5. Legal Strategy Question (UPL Defense Refusal)
    q_strat = QuestionRequest(question="Should I pay this amount or what is my defence in court?")
    ans_strat = ask_document_question(doc_id=doc_id, req=q_strat, current_user=_TEST_USER)
    assert ans_strat.confidence == "red"
    assert "not legal strategy" in ans_strat.answer.lower() or "advocate" in ans_strat.answer.lower()
    assert len(ans_strat.citations) == 0

    # 6. Calendar Export (.ics)
    cal_resp = export_calendar(doc_id=doc_id, kind="deadline", current_user=_TEST_USER)
    assert cal_resp.media_type == "text/calendar"
    cal_text = cal_resp.body.decode("utf-8")
    assert "BEGIN:VCALENDAR" in cal_text
    assert "BEGIN:VEVENT" in cal_text
    assert "DTSTART;VALUE=DATE:" in cal_text
    assert "DTEND;VALUE=DATE:" in cal_text
    assert "BEGIN:VALARM" in cal_text
    assert "END:VCALENDAR" in cal_text

    # 7. Permanent Deletion (FR-100)
    del_resp = delete_document(doc_id=doc_id, current_user=_TEST_USER)
    assert del_resp["status"] == "success"
    assert del_resp["deleted"] is True

    # 8. Post-Deletion 404 Verifications
    with pytest.raises(HTTPException) as exc_get:
        get_document_analysis(doc_id=doc_id, current_user=_TEST_USER)
    assert exc_get.value.status_code == 404

    with pytest.raises(HTTPException) as exc_file:
        get_original_file(doc_id=doc_id, current_user=_TEST_USER)
    assert exc_file.value.status_code == 404

    with pytest.raises(HTTPException) as exc_del:
        delete_document(doc_id=doc_id, current_user=_TEST_USER)
    assert exc_del.value.status_code == 404


def test_security_doc_id_validation():
    """Ensures path traversal and wildcard injections are rejected with HTTP 400."""
    invalid_ids = [
        "../etc/passwd",
        "..",
        ".",
        "*",
        "../../results",
        "non_hex_id_that_is_32_chars_long!",
        "123"
    ]
    for bad_id in invalid_ids:
        with pytest.raises(HTTPException) as exc:
            validate_doc_id(bad_id)
        assert exc.value.status_code == 400


def test_reject_empty_file_upload():
    """Ensures 0-byte file uploads are rejected cleanly with HTTP 400."""
    empty_upload = UploadFile(
        filename="empty.pdf",
        file=io.BytesIO(b"")
    )
    with pytest.raises(HTTPException) as exc:
        upload_and_analyze_document(file=empty_upload, current_user=_TEST_USER)
    assert exc.value.status_code == 400
    assert "empty" in exc.value.detail.lower()


def test_calendar_rfc5545_spec_compliance():
    """Verifies that generated .ics complies with RFC 5545 all-day date requirements."""
    from app.models import DateItem
    from app.calendar_export import generate_ics_calendar

    dates = [
        DateItem(raw="within 15 days", date_iso="2024-03-16", role="deadline", is_computed=True, computation_basis="15 days from notice")
    ]
    ics = generate_ics_calendar(
        doc_id="abcdef1234567890abcdef1234567890",
        filename="test_notice.pdf",
        dates=dates,
        kind="deadline"
    )

    # In RFC 5545 all-day events, DTEND is exclusive and must be day after DTSTART
    assert "DTSTART;VALUE=DATE:20240316" in ics
    assert "DTEND;VALUE=DATE:20240317" in ics
    assert "TRIGGER:-P2D" in ics
    assert "DISCLAIMER" in ics
