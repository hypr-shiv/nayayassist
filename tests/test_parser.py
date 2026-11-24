"""
Unit tests for document parser and upload signature validation (Phase 1 & 7).
Verifies T-101 (magic signature verification) and coordinate normalization.
"""

import io
import pytest
from app import parser


def test_verify_file_signature_pdf():
    # Valid PDF magic bytes
    stream = io.BytesIO(b"%PDF-1.7\n%some binary data")
    mime = parser.verify_file_signature(stream, "sample.pdf")
    assert mime == "application/pdf"
    assert stream.tell() == 0  # Rewound


def test_verify_file_signature_png():
    # Valid PNG magic bytes
    stream = io.BytesIO(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    mime = parser.verify_file_signature(stream, "sample.png")
    assert mime == "image/png"


def test_verify_file_signature_jpeg():
    # Valid JPEG magic bytes
    stream = io.BytesIO(b"\xFF\xD8\xFF\xE0\x00\x10JFIF")
    mime = parser.verify_file_signature(stream, "sample.jpg")
    assert mime == "image/jpeg"


def test_verify_file_signature_mismatch():
    # PDF magic bytes with .png extension
    stream = io.BytesIO(b"%PDF-1.7\nfake png")
    with pytest.raises(ValueError, match="magic bytes indicate PDF"):
        parser.verify_file_signature(stream, "fake.png")


def test_verify_file_signature_invalid():
    # Plain text or corrupted bytes
    stream = io.BytesIO(b"Hello world, I am just text")
    with pytest.raises(ValueError, match="Unsupported file format"):
        parser.verify_file_signature(stream, "test.pdf")


def test_norm_coordinate():
    # Coordinate normalization to [0.0, 1.0]
    assert parser._norm(0, 100) == 0.0
    assert parser._norm(50, 100) == 0.5
    assert parser._norm(100, 100) == 1.0
    assert parser._norm(-10, 100) == 0.0
    assert parser._norm(150, 100) == 1.0
    assert parser._norm(33.333333, 100) == 0.33333
