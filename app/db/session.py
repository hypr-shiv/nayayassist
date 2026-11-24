"""
Database engine and session management for NyayaAssist Pilot Phase.
Supports PostgreSQL (with pgvector) in pilot environments, and SQLite for zero-daemon local testing.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app import config

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{config.DATA_DIR / 'nyaya.db'}")

# SQLite specific connect args for thread safety
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=False
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    """Dependency that yields an active database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Creates all tables in the connected database."""
    from app.db.models import Base
    Base.metadata.create_all(bind=engine)
