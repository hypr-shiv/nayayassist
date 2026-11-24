"""
Migrates flat file JSON analysis records from data/results/ into the relational database.
Used for zero-downtime pilot transition from MVP file store to PostgreSQL/SQLite.
"""

import sys
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from datetime import datetime, timezone
from app import config
from app.db.session import SessionLocal, init_db
from app.db.models import (
    DocumentModel, DocumentPageModel, DocumentBlockModel,
    DocumentDateModel, LegalClaimModel, CitationModel, AnalysisRunModel
)


def migrate_flat_records():
    init_db()
    results_dir = config.RESULTS_DIR
    if not results_dir.exists():
        print(f"Results directory {results_dir} does not exist.")
        return 0

    db = SessionLocal()
    migrated_count = 0
    try:
        for json_path in results_dir.glob("*.json"):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                doc_id = data.get("doc_id", json_path.stem)
                existing = db.query(DocumentModel).filter(DocumentModel.id == doc_id).first()
                if existing:
                    continue  # already migrated
                
                doc_model = DocumentModel(
                    id=doc_id,
                    filename=data.get("filename", f"{doc_id}.pdf"),
                    stored_path=data.get("stored_path", ""),
                    file_hash=data.get("file_hash", ""),
                    mime_type="application/pdf" if data.get("filename", "").endswith(".pdf") else "image/png",
                    file_size_bytes=data.get("file_size_bytes", 0),
                    doc_type=data.get("doc_type", "unknown"),
                    type_confidence=data.get("type_confidence", 0.0),
                    status="analyzed",
                    created_at=datetime.now(timezone.utc)
                )
                db.add(doc_model)

                # Migrate pages & blocks
                for page_dict in data.get("pages", []):
                    page_num = page_dict.get("page", 1)
                    page_model = DocumentPageModel(
                        document_id=doc_id,
                        page_number=page_num,
                        width=page_dict.get("width", 595.0),
                        height=page_dict.get("height", 842.0)
                    )
                    db.add(page_model)

                    for block_dict in page_dict.get("blocks", []):
                        bbox = block_dict.get("bbox", [0.0, 0.0, 1.0, 1.0])
                        block_model = DocumentBlockModel(
                            document_id=doc_id,
                            block_uid=block_dict.get("id", f"p{page_num}_b0"),
                            page_number=page_num,
                            bbox_x0=bbox[0],
                            bbox_y0=bbox[1],
                            bbox_x1=bbox[2],
                            bbox_y1=bbox[3],
                            text=block_dict.get("text", ""),
                            source=block_dict.get("source", "text_layer"),
                            ocr_conf=block_dict.get("ocr_conf", 100.0)
                        )
                        db.add(block_model)

                # Migrate dates
                for date_dict in data.get("dates", []):
                    date_model = DocumentDateModel(
                        document_id=doc_id,
                        raw_text=date_dict.get("raw", ""),
                        date_iso=date_dict.get("date_iso"),
                        role=date_dict.get("role", "mentioned"),
                        is_computed=date_dict.get("is_computed", False),
                        computation_basis=date_dict.get("computation_basis")
                    )
                    db.add(date_model)

                # Migrate claims and citations
                for claim_dict in data.get("claims", []):
                    claim_model = LegalClaimModel(
                        document_id=doc_id,
                        claim_text=claim_dict.get("claim", ""),
                        category=claim_dict.get("category", "general"),
                        confidence=claim_dict.get("confidence", "yellow"),
                        reasons=claim_dict.get("reasons", [])
                    )
                    db.add(claim_model)
                    db.flush()  # get claim_model.id

                    for ref in claim_dict.get("document_refs", []):
                        cit = CitationModel(
                            claim_id=claim_model.id,
                            block_id=ref,
                            citation_type="document"
                        )
                        db.add(cit)

                    for sref in claim_dict.get("statute_refs", []):
                        cit = CitationModel(
                            claim_id=claim_model.id,
                            statute_id=sref,
                            citation_type="statute"
                        )
                        db.add(cit)

                # Migrate audit runs if present
                for run_dict in data.get("analysis_runs", []):
                    run_model = AnalysisRunModel(
                        document_id=doc_id,
                        execution_mode=run_dict.get("execution_mode", "rule"),
                        llm_provider=run_dict.get("llm_provider", "none"),
                        model_identifier=run_dict.get("model_identifier", "rule"),
                        prompt_version=run_dict.get("prompt_version", "1.0.0"),
                        schema_version=run_dict.get("schema_version", "1.0.0"),
                        duration_ms=run_dict.get("duration_ms", 0)
                    )
                    db.add(run_model)

                migrated_count += 1
            except Exception as e:
                print(f"Failed to migrate {json_path}: {e}")

        db.commit()
        print(f"Migrated {migrated_count} records into the relational database.")
    finally:
        db.close()
    return migrated_count


if __name__ == "__main__":
    migrate_flat_records()
