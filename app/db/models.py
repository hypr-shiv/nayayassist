"""
SQLAlchemy 2.0 ORM models for NyayaAssist.
Direct mapping of relational schemas defined in DATABASE.md.
Strictly ensures INV-10 (no document text in audit tables).
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Text, DateTime, ForeignKey, JSON
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class UserConsentModel(Base):
    __tablename__ = "user_consents"

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id = Column(String(36), index=True, nullable=True)
    consent_version = Column(String(16), nullable=False, default="1.0.0")
    ip_hash = Column(String(64), nullable=True)
    accepted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DocumentModel(Base):
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    owner_id = Column(String(36), index=True, nullable=True)
    filename = Column(String(255), nullable=False)
    stored_path = Column(String(512), nullable=False)
    file_hash = Column(String(64), nullable=False)
    mime_type = Column(String(64), nullable=False)
    file_size_bytes = Column(Integer, nullable=False)
    doc_type = Column(String(64), nullable=False)
    type_confidence = Column(Float, nullable=False)
    status = Column(String(32), default="analyzed")
    retain_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    pages = relationship("DocumentPageModel", back_populates="document", cascade="all, delete-orphan")
    blocks = relationship("DocumentBlockModel", back_populates="document", cascade="all, delete-orphan")
    dates = relationship("DocumentDateModel", back_populates="document", cascade="all, delete-orphan")
    claims = relationship("LegalClaimModel", back_populates="document", cascade="all, delete-orphan")
    runs = relationship("AnalysisRunModel", back_populates="document", cascade="all, delete-orphan")


class DocumentPageModel(Base):
    __tablename__ = "document_pages"

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    page_number = Column(Integer, nullable=False)
    width = Column(Float, nullable=False)
    height = Column(Float, nullable=False)

    document = relationship("DocumentModel", back_populates="pages")


class DocumentBlockModel(Base):
    __tablename__ = "document_blocks"

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    block_uid = Column(String(32), nullable=False)  # e.g. p1_b0
    page_number = Column(Integer, nullable=False)
    bbox_x0 = Column(Float, nullable=False)
    bbox_y0 = Column(Float, nullable=False)
    bbox_x1 = Column(Float, nullable=False)
    bbox_y1 = Column(Float, nullable=False)
    text = Column(Text, nullable=False)
    source = Column(String(32), nullable=False)  # text_layer or ocr
    ocr_conf = Column(Float, nullable=True)

    document = relationship("DocumentModel", back_populates="blocks")


class DocumentDateModel(Base):
    __tablename__ = "document_dates"

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    raw_text = Column(String(128), nullable=False)
    date_iso = Column(String(32), nullable=True)
    role = Column(String(32), nullable=False)
    is_computed = Column(Boolean, default=False)
    computation_basis = Column(Text, nullable=True)
    anchor_event = Column(String(64), nullable=True)

    document = relationship("DocumentModel", back_populates="dates")


class LegalClaimModel(Base):
    __tablename__ = "legal_claims"

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    claim_text = Column(Text, nullable=False)
    category = Column(String(64), nullable=False)
    confidence = Column(String(16), nullable=False)  # green, yellow, red
    reasons = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    document = relationship("DocumentModel", back_populates="claims")
    citations = relationship("CitationModel", back_populates="claim", cascade="all, delete-orphan")


class CitationModel(Base):
    __tablename__ = "citations"

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    claim_id = Column(String(36), ForeignKey("legal_claims.id", ondelete="CASCADE"), nullable=False)
    block_id = Column(String(36), nullable=True)
    statute_id = Column(String(64), ForeignKey("statute_corpus.id"), nullable=True)
    citation_type = Column(String(32), nullable=False)  # document or statute
    relevance_score = Column(Float, default=1.0)

    claim = relationship("LegalClaimModel", back_populates="citations")


class StatuteCorpusModel(Base):
    __tablename__ = "statute_corpus"

    id = Column(String(64), primary_key=True)  # e.g. NIA-138
    act = Column(String(255), nullable=False)
    section = Column(String(64), nullable=False)
    title = Column(String(255), nullable=False)
    text = Column(Text, nullable=False)
    source_url = Column(String(512), nullable=False)
    retrieved_on = Column(String(32), nullable=False)
    verified = Column(Boolean, default=False)
    verified_by = Column(String(64), nullable=True)
    verified_on = Column(String(32), nullable=True)


class AnalysisRunModel(Base):
    __tablename__ = "analysis_runs"

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    execution_mode = Column(String(32), nullable=False)  # llm or rule
    llm_provider = Column(String(32), nullable=False)
    model_identifier = Column(String(64), nullable=False)
    prompt_version = Column(String(16), nullable=False)
    schema_version = Column(String(16), nullable=False)
    duration_ms = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    document = relationship("DocumentModel", back_populates="runs")
