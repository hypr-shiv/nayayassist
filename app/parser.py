"""
Spatial document parser and layout-aware OCR engine.
Pure I/O module (Architectural Rule 4.1). Must NOT do classification or legal analysis.
Implements Task T-101 (magic signature verification and streamed size checks).
"""

import io
import uuid
from pathlib import Path
from typing import List, Tuple, BinaryIO

import pymupdf as fitz
from PIL import Image

from app import config
from app.models import Page, Block

# Minimum selectable characters on a page to avoid OCR fallback
MIN_TEXT_CHARS = 40

# Magic Signatures (T-101 / FR-01)
MAGIC_PDF = b"%PDF"
MAGIC_PNG = b"\x89PNG\r\n\x1a\n"
MAGIC_JPEG = b"\xFF\xD8\xFF"


def verify_file_signature(stream: BinaryIO, filename: str) -> str:
    """
    Verifies that the file stream begins with valid magic bytes matching its extension.
    Returns the detected mime type or raises ValueError (T-101).
    """
    header = stream.read(8)
    stream.seek(0)
    
    if not header:
        raise ValueError(f"File '{filename}' is empty (0 bytes).")
    
    ext = Path(filename).suffix.lower()
    
    if header.startswith(MAGIC_PDF):
        if ext != ".pdf":
            raise ValueError(f"File magic bytes indicate PDF, but file has extension '{ext}'")
        return "application/pdf"
    
    if header.startswith(b"\x89PNG"):
        if ext != ".png":
            raise ValueError(f"File magic bytes indicate PNG, but file has extension '{ext}'")
        return "image/png"
        
    if header.startswith(b"\xFF\xD8\xFF"):
        if ext not in [".jpg", ".jpeg"]:
            raise ValueError(f"File magic bytes indicate JPEG, but file has extension '{ext}'")
        return "image/jpeg"
        
    raise ValueError(f"Unsupported file format or invalid magic bytes for '{filename}'")


def _norm(val: float, total: float) -> float:
    """Normalizes coordinate to [0.0, 1.0] closed range, rounded to 5 decimal places."""
    if total <= 0:
        return 0.0
    return round(max(0.0, min(1.0, val / total)), 5)


def _configure_tesseract() -> None:
    """Configures pytesseract command path from config or standard installation locations."""
    try:
        import pytesseract
        import shutil
        import os
        
        if config.TESSERACT_PATH and Path(config.TESSERACT_PATH).is_file():
            pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_PATH
            return

        if shutil.which("tesseract"):
            return

        candidates = [
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                pytesseract.pytesseract.tesseract_cmd = str(candidate)
                break
    except Exception:
        pass


def _ocr_page(page: fitz.Page, page_no: int, doc_id: str) -> Tuple[List[Block], List[str]]:
    """Runs Tesseract OCR on a rendered page image at 2x resolution."""
    warnings = []
    blocks = []
    
    try:
        import pytesseract
        import pandas as pd
        _configure_tesseract()
    except ImportError:
        warnings.append(f"Page {page_no}: OCR engine is not installed. Text extraction skipped.")
        return blocks, warnings

    try:
        # Render page at 2x zoom for crisp character recognition
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        w_img, h_img = img.size
        
        # Extract word-level data
        data = pytesseract.image_to_data(
            img, 
            lang=config.OCR_LANG, 
            output_type=pytesseract.Output.DATAFRAME
        )
        
        # Clean dataframe
        data = data[data.text.notnull()]
        data['text'] = data['text'].astype(str).str.strip()
        data = data[data['text'] != '']
        
        if data.empty:
            warnings.append(f"Page {page_no}: Scanned page contained no readable text under OCR.")
            return blocks, warnings

        # Group words by block and paragraph
        grouped = data.groupby(['block_num', 'par_num'])
        for (b_num, p_num), group in grouped:
            block_text = " ".join(group['text'].tolist())
            if not block_text.strip():
                continue
                
            x_min = group['left'].min()
            y_min = group['top'].min()
            x_max = (group['left'] + group['width']).max()
            y_max = (group['top'] + group['height']).max()
            
            # Mean confidence of words
            confs = pd.to_numeric(group['conf'], errors='coerce').dropna()
            mean_conf = float(confs.mean()) if not confs.empty else 0.0
            mean_conf = max(0.0, min(100.0, mean_conf))
            
            bbox = [
                _norm(x_min, w_img),
                _norm(y_min, h_img),
                _norm(x_max, w_img),
                _norm(y_max, h_img)
            ]
            
            block_id = f"{doc_id}-p{page_no}-b{b_num}-{p_num}"
            blocks.append(Block(
                id=block_id,
                page=page_no,
                bbox=bbox,
                text=block_text,
                source="ocr",
                ocr_conf=round(mean_conf, 2)
            ))
            
        warnings.append(f"Page {page_no} had fewer than {MIN_TEXT_CHARS} selectable characters; Tesseract OCR was used.")
        
    except Exception as e:
        warnings.append(f"Page {page_no}: OCR extraction encountered an error: {str(e)}")
        
    return blocks, warnings


def _ocr_image_file(file_path: Path, doc_id: str) -> Tuple[List[Page], List[Block], List[str]]:
    """Runs OCR directly on a standalone image file (PNG/JPEG)."""
    warnings = []
    blocks = []
    
    img = Image.open(file_path)
    w_img, h_img = img.size
    pages = [Page(page_no=1, width=float(w_img), height=float(h_img))]
    
    try:
        import pytesseract
        import pandas as pd
        _configure_tesseract()
    except ImportError:
        warnings.append("OCR engine is not installed. Text extraction skipped.")
        return pages, blocks, warnings

    try:
        data = pytesseract.image_to_data(
            img, 
            lang=config.OCR_LANG, 
            output_type=pytesseract.Output.DATAFRAME
        )
        data = data[data.text.notnull()]
        data['text'] = data['text'].astype(str).str.strip()
        data = data[data['text'] != '']
        
        if data.empty:
            warnings.append("Uploaded image contained no readable text under OCR.")
            return pages, blocks, warnings

        grouped = data.groupby(['block_num', 'par_num'])
        for (b_num, p_num), group in grouped:
            block_text = " ".join(group['text'].tolist())
            if not block_text.strip():
                continue
                
            x_min = group['left'].min()
            y_min = group['top'].min()
            x_max = (group['left'] + group['width']).max()
            y_max = (group['top'] + group['height']).max()
            
            confs = pd.to_numeric(group['conf'], errors='coerce').dropna()
            mean_conf = float(confs.mean()) if not confs.empty else 0.0
            
            bbox = [
                _norm(x_min, w_img),
                _norm(y_min, h_img),
                _norm(x_max, w_img),
                _norm(y_max, h_img)
            ]
            
            block_id = f"{doc_id}-p1-b{b_num}-{p_num}"
            blocks.append(Block(
                id=block_id,
                page=1,
                bbox=bbox,
                text=block_text,
                source="ocr",
                ocr_conf=round(max(0.0, min(100.0, mean_conf)), 2)
            ))
            
    except Exception as e:
        warnings.append(f"Image OCR extraction error: {str(e)}")
        
    return pages, blocks, warnings


def parse_document(file_path: Path, doc_id: str) -> Tuple[List[Page], List[Block], List[str]]:
    """
    Main entry point for parsing document text and geometry.
    Returns (pages, blocks, warnings).
    """
    ext = file_path.suffix.lower()
    if ext in [".png", ".jpg", ".jpeg"]:
        return _ocr_image_file(file_path, doc_id)

    doc = fitz.open(file_path)
    pages: List[Page] = []
    blocks: List[Block] = []
    warnings: List[str] = []

    for page_idx in range(len(doc)):
        page_no = page_idx + 1
        page = doc[page_idx]
        rect = page.rect
        pages.append(Page(page_no=page_no, width=round(rect.width, 2), height=round(rect.height, 2)))

        # 1. Native text-layer extraction
        page_text = page.get_text("text").strip()
        
        # If page has sufficient native text, extract text blocks
        if len(page_text) >= MIN_TEXT_CHARS:
            raw_blocks = page.get_text("blocks")
            for b_idx, b in enumerate(raw_blocks):
                x0, y0, x1, y1, text, block_no, block_type = b[:7]
                # Filter out image blocks (type 1) or empty text
                if block_type != 0 or not text.strip():
                    continue
                
                bbox = [
                    _norm(x0, rect.width),
                    _norm(y0, rect.height),
                    _norm(x1, rect.width),
                    _norm(y1, rect.height)
                ]
                block_id = f"{doc_id}-p{page_no}-b{b_idx}"
                blocks.append(Block(
                    id=block_id,
                    page=page_no,
                    bbox=bbox,
                    text=text.strip(),
                    source="text_layer",
                    ocr_conf=None
                ))
        else:
            # 2. OCR Fallback for scanned/low-char pages
            ocr_b, ocr_w = _ocr_page(page, page_no, doc_id)
            blocks.extend(ocr_b)
            warnings.extend(ocr_w)

    doc.close()
    return pages, blocks, warnings
