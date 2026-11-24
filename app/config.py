"""
Application configuration and environment variables.
Must NOT import any other module from app/ (Architectural Rule 4.1).
"""

import os
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data_store"))
UPLOADS_DIR = DATA_DIR / "uploads"
RECORDS_DIR = DATA_DIR / "records"
RESULTS_DIR = RECORDS_DIR

# Ensure storage directories exist
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
RECORDS_DIR.mkdir(parents=True, exist_ok=True)

# Application Versions
PROMPT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"

# LLM Configuration
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "none").lower()
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

# Operational Limits
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "15"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
OCR_LANG = os.environ.get("OCR_LANG", "eng")
TESSERACT_PATH = os.environ.get("TESSERACT_PATH", "")
RETRIEVAL_MIN_TERMS = int(os.environ.get("RETRIEVAL_MIN_TERMS", "2"))
DATA_MODE = os.environ.get("DATA_MODE", "demo").lower()
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "1"))

# Mandatory Statutory Disclaimer (INV-07)
# Must not be changed without legal counsel review
DISCLAIMER = (
    "Legal information only, not legal advice. No advocate-client relationship is created. "
    "Verify all statements and response deadlines against the original document. "
    "Consult a licensed advocate registered with your State Bar Council for case-specific legal strategy."
)
