"""
Typed data contracts and schemas (Pydantic v2).
Must NOT import any module from app/ except config.py (Architectural Rule 4.1).
"""

from typing import List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator
from app import config


class Page(BaseModel):
    page_no: int
    width: float
    height: float


class Block(BaseModel):
    id: str
    page: int
    bbox: List[float] = Field(..., description="[x0, y0, x1, y1] normalized to [0.0, 1.0]")
    text: str
    source: Literal["text_layer", "ocr"]
    ocr_conf: Optional[float] = Field(None, ge=0.0, le=100.0)

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, v: List[float]) -> List[float]:
        if len(v) != 4:
            raise ValueError("Bounding box must contain exactly 4 coordinates [x0, y0, x1, y1]")
        for coord in v:
            if not (0.0 <= coord <= 1.0):
                raise ValueError(f"Coordinate {coord} outside normalized range [0.0, 1.0]")
        return [round(c, 5) for c in v]


class DateItem(BaseModel):
    raw: str
    date_iso: Optional[str] = Field(None, description="ISO format YYYY-MM-DD, or None if working days")
    role: Literal["issue", "received", "deadline", "hearing", "mentioned"]
    is_computed: bool = False
    computation_basis: Optional[str] = None
    block_id: Optional[str] = None
    sentence: Optional[str] = None


class Citation(BaseModel):
    kind: Literal["document", "statute"]
    relation: Literal["quoted", "supports", "possibly_related"]
    ref_id: str
    label: str
    quote: Optional[str] = None
    page: Optional[int] = None
    bbox: Optional[List[float]] = None
    verified: Optional[bool] = None
    source_url: Optional[str] = None


class Claim(BaseModel):
    id: str
    text: str
    confidence: Literal["green", "yellow", "red"]
    reasons: List[str] = Field(default_factory=list)
    citations: List[Citation] = Field(default_factory=list)


class GlossaryItem(BaseModel):
    term: str
    meaning: str
    reviewed_by: Optional[str] = None
    reviewed_on: Optional[str] = None


class StatuteSection(BaseModel):
    id: str
    jurisdiction: str = "IN"
    act: str
    section: str
    title: str
    keywords: List[str] = Field(default_factory=list)
    text: str
    source_url: str
    retrieved_on: str
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    version: Optional[str] = None
    verified: bool = False
    verified_by: Optional[str] = None
    verified_on: Optional[str] = None


class AnalysisRun(BaseModel):
    id: str
    document_id: str
    mode: str
    llm_provider: Optional[str] = None
    model: Optional[str] = None
    prompt_version: str = config.PROMPT_VERSION
    schema_version: str = config.SCHEMA_VERSION
    duration_ms: int
    started_at: str


class AnalysisResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    doc_id: str
    filename: str
    doc_type: str
    type_confidence: float = Field(ge=0.0, le=1.0)
    pages: List[Page] = Field(default_factory=list)
    blocks: List[Block] = Field(default_factory=list)
    dates: List[DateItem] = Field(default_factory=list)
    claims: List[Claim] = Field(default_factory=list)
    glossary: List[GlossaryItem] = Field(default_factory=list)
    procedural_steps: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    unclear: List[str] = Field(default_factory=list)
    disclaimer: str = Field(default=config.DISCLAIMER)  # INV-07


class Answer(BaseModel):
    answer: str
    citations: List[Citation] = Field(default_factory=list)
    confidence: Literal["green", "yellow", "red"] = "yellow"
    disclaimer: str = Field(default=config.DISCLAIMER)  # INV-07


class QuestionRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)


class DocTypeUpdateRequest(BaseModel):
    doc_type: str = Field(..., description="User-corrected document type (FR-11)")


class LLMClaimOutput(BaseModel):
    plain_text: str
    document_refs: List[str] = Field(default_factory=list)
    statute_refs: List[str] = Field(default_factory=list)


class LLMExplainOutput(BaseModel):
    claims: List[LLMClaimOutput] = Field(default_factory=list)
    unclear: List[str] = Field(default_factory=list)


class LLMQAOutput(BaseModel):
    answer: str
    document_refs: List[str] = Field(default_factory=list)
    law_refs: List[str] = Field(default_factory=list)
