"""
NyayaAssist Pilot Database Package.
Implements SQLAlchemy 2.0 models and session management for pilot transition.
"""

from app.db.session import engine, SessionLocal, get_db, init_db
from app.db.models import (
    Base,
    DocumentModel,
    DocumentPageModel,
    DocumentBlockModel,
    DocumentDateModel,
    LegalClaimModel,
    CitationModel,
    StatuteCorpusModel,
    AnalysisRunModel,
    UserConsentModel
)

__all__ = [
    "engine",
    "SessionLocal",
    "get_db",
    "init_db",
    "Base",
    "DocumentModel",
    "DocumentPageModel",
    "DocumentBlockModel",
    "DocumentDateModel",
    "LegalClaimModel",
    "CitationModel",
    "StatuteCorpusModel",
    "AnalysisRunModel",
    "UserConsentModel",
]
