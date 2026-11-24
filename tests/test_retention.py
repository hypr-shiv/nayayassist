"""
Tests for DPDPA Retention Reaper Daemon (FR-102 / T-111).
Verifies:
- Files past retention window are cryptographically erased and unlinked.
- Audit log is created with zero document text or PII (INV-10).
"""

import time
import json
from pathlib import Path
from app import config
from app.retention import reap_expired_records, secure_wipe_and_delete


def test_secure_wipe_and_delete(tmp_path):
    target = tmp_path / "sensitive_doc.pdf"
    content = b"Confidential legal notice text"
    target.write_bytes(content)
    assert target.exists()

    erased_bytes = secure_wipe_and_delete(target)
    assert erased_bytes == len(content)
    assert not target.exists()


def test_reaper_sweeps_expired_records(tmp_path, monkeypatch):
    test_uploads = tmp_path / "uploads"
    test_records = tmp_path / "records"
    test_uploads.mkdir(parents=True)
    test_records.mkdir(parents=True)

    monkeypatch.setattr(config, "UPLOADS_DIR", test_uploads)
    monkeypatch.setattr(config, "RECORDS_DIR", test_records)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    # Create dummy old file
    old_upload = test_uploads / "doc123.pdf"
    old_upload.write_bytes(b"%PDF-1.4 old legal file")
    old_result = test_records / "doc123.json"
    old_result.write_text(json.dumps({
        "doc_id": "doc123",
        "stored_path": str(old_upload)
    }), encoding="utf-8")

    # Set file modification times to 2 days ago
    past_mtime = time.time() - (2 * 86400)
    import os
    os.utime(old_upload, (past_mtime, past_mtime))
    os.utime(old_result, (past_mtime, past_mtime))

    # Run reaper with 1-day retention (86400s)
    events = reap_expired_records(retention_seconds=86400)

    assert len(events) >= 1
    assert not old_upload.exists()
    assert not old_result.exists()

    # Check audit log contains ZERO document text (INV-10)
    audit_file = tmp_path / "reaper_audit.jsonl"
    assert audit_file.exists()
    with open(audit_file, "r", encoding="utf-8") as f:
        log_lines = [json.loads(line) for line in f]
    
    assert len(log_lines) >= 1
    for log in log_lines:
        assert log["doc_id"] == "doc123"
        assert log["action"].startswith("cryptographic_erasure")
        # Ensure zero document text is in log
        assert "content" not in log
        assert "text" not in log
        assert "legal" not in json.dumps(log).lower()
