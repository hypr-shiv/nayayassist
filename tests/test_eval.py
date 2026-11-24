"""
Tests for Gold Set Evaluation Harness (T-116).
Validates that the curated gold set executes with:
- 100% Citation Traceability (INV-01)
- 100% Invariant Verification Rate (INV-01 through INV-10)
- >= 90% Document Classification Accuracy
"""

from pathlib import Path
from app import config
from eval.run_eval import evaluate_gold_set


def test_gold_set_evaluation():
    gold_path = config.BASE_DIR / "eval" / "gold_samples.json"
    assert gold_path.exists(), "Gold set sample file must exist"

    report = evaluate_gold_set(gold_path)
    assert report["total_samples"] >= 4
    assert report["classification_accuracy_pct"] >= 90.0
    assert report["citation_traceability_pct"] == 100.0
    assert report["invariant_violations"] == 0
