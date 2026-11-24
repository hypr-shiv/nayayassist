"""
BM25 legal corpus retrieval engine.
Pure domain module (Architectural Rule 4.1). Must NOT make LLM calls or mutate corpus files.
Implements ranking over India Code statutes (FR-40, FR-41, T-104).
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

from app import config
from app.models import StatuteSection

# Stop words to remove from BM25 queries
STOP_WORDS = {
    "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "of", "with",
    "by", "is", "are", "was", "were", "be", "been", "this", "that", "it", "as",
    "from", "into", "if", "not", "shall", "may", "which", "such", "any", "no"
}


def tokenize(text: str) -> List[str]:
    """Tokenizes string into lowercase alphanumeric words, filtering stop words."""
    words = re.findall(r"\b[a-zA-Z0-9_\-]+\b", text.lower())
    return [w for w in words if w not in STOP_WORDS and len(w) > 1]


class BM25Index:
    """Lightweight in-memory BM25 ranker for legal text."""
    def __init__(self, corpus: List[StatuteSection]):
        self.sections = corpus
        self.doc_tokens = []
        for s in corpus:
            combined = f"{s.act} {s.section} {s.title} {' '.join(s.keywords)} {s.text}"
            self.doc_tokens.append(tokenize(combined))
            
        try:
            from rank_bm25 import BM25Okapi
            self.bm25 = BM25Okapi(self.doc_tokens)
            self._has_bm25 = True
        except ImportError:
            self._has_bm25 = False

    def search(self, query: str, limit: int = 3, min_terms: int = 1) -> List[StatuteSection]:
        """Ranks statutes based on query tokens."""
        q_tokens = tokenize(query)
        if not q_tokens or len(q_tokens) < min_terms:
            return []

        if self._has_bm25:
            scores = self.bm25.get_scores(q_tokens)
            ranked_pairs = sorted(zip(self.sections, scores), key=lambda x: x[1], reverse=True)
            return [s for s, score in ranked_pairs if score > 0.0][:limit]
        else:
            # Fallback naive term-overlap scoring
            scored = []
            q_set = set(q_tokens)
            for s, tokens in zip(self.sections, self.doc_tokens):
                overlap = len(q_set.intersection(set(tokens)))
                if overlap >= min_terms:
                    scored.append((s, overlap))
            scored.sort(key=lambda x: x[1], reverse=True)
            return [s for s, _ in scored][:limit]


class StatuteStore:
    """Manages verified India Code statute repository."""
    def __init__(self, json_path: Path):
        self.json_path = json_path
        self.sections: List[StatuteSection] = []
        self._load()
        self.index = BM25Index(self.sections)

    def _load(self):
        if not self.json_path.exists():
            return
        with open(self.json_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            self.sections = [StatuteSection(**item) for item in raw_data]

    def get_by_id(self, statute_id: str) -> Optional[StatuteSection]:
        for s in self.sections:
            if s.id == statute_id:
                return s
        return None

    def search(self, query: str, limit: int = 3) -> List[StatuteSection]:
        return self.index.search(query, limit=limit)


# Singleton instance
_STATUTE_STORE: Optional[StatuteStore] = None

def get_statute_store() -> StatuteStore:
    global _STATUTE_STORE
    if _STATUTE_STORE is None:
        path = config.BASE_DIR / "data" / "statutes.json"
        _STATUTE_STORE = StatuteStore(path)
    return _STATUTE_STORE
