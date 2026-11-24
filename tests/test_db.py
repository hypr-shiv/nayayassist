"""
Tests for Pilot Database Layer (T-109 / TD-13).
Verifies:
- SQLAlchemy models instantiate and persist cleanly.
- Cascade deletion works on documents, blocks, and claims.
- Seed script successfully loads verified provisions.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DocumentModel, DocumentBlockModel, LegalClaimModel, CitationModel, StatuteCorpusModel
from scripts.seed_pilot_db import seed_statutes


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_document_and_cascade_deletion(test_db):
    doc = DocumentModel(
        id="test_doc_01",
        filename="notice.pdf",
        stored_path="data_store/uploads/test_doc_01.pdf",
        file_hash="abc123hash",
        mime_type="application/pdf",
        file_size_bytes=1024,
        doc_type="court_summons",
        type_confidence=0.95
    )
    test_db.add(doc)
    test_db.commit()

    block = DocumentBlockModel(
        document_id="test_doc_01",
        block_uid="p1_b0",
        page_number=1,
        bbox_x0=0.1,
        bbox_y0=0.1,
        bbox_x1=0.9,
        bbox_y1=0.2,
        text="Sample summons text block",
        source="text_layer",
        ocr_conf=100.0
    )
    test_db.add(block)

    claim = LegalClaimModel(
        document_id="test_doc_01",
        claim_text="Defendant must appear in court.",
        category="procedure",
        confidence="green"
    )
    test_db.add(claim)
    test_db.commit()

    # Query to assert persistence
    stored = test_db.query(DocumentModel).filter_by(id="test_doc_01").first()
    assert stored is not None
    assert len(stored.blocks) == 1
    assert len(stored.claims) == 1

    # Delete parent document and verify cascade
    test_db.delete(stored)
    test_db.commit()

    assert test_db.query(DocumentBlockModel).filter_by(document_id="test_doc_01").count() == 0
    assert test_db.query(LegalClaimModel).filter_by(document_id="test_doc_01").count() == 0


def test_seed_statutes(test_db):
    count = seed_statutes()
    assert count >= 4
