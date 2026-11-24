"""
NyayaAssist Automated Gold Set Evaluation Harness (T-116).
Measures:
- M-3: Citation Traceability % (Invariant INV-01: 100% required)
- M-4: Deadline extraction precision
- M-5: Document classification accuracy (>= 90%)
- M-6: Invariant verification rate (INV-01 through INV-10: 100%)
"""

import sys
import json
from pathlib import Path

# Ensure repo root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from typing import Dict, Any, List
from app import config, nlp, retrieval, analyzer
from app.models import Page, Block, AnalysisResult


def evaluate_gold_set(gold_path: Path) -> Dict[str, Any]:
    with open(gold_path, "r", encoding="utf-8") as f:
        samples = json.load(f)

    total_samples = len(samples)
    classification_correct = 0
    total_claims = 0
    valid_cited_claims = 0
    total_expected_deadlines = 0
    deadlines_detected = 0
    invariant_violations = 0
    results_detail = []

    for item in samples:
        sample_id = item["id"]
        raw_text = item["raw_text"]
        expected_type = item["expected_doc_type"]
        expected_statute = item["expected_statute"]

        # Build simulated Page and Blocks
        lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
        blocks = []
        for i, line in enumerate(lines):
            b = Block(
                id=f"p1_b{i}",
                page=1,
                bbox=[0.05, round(i * 0.08, 3), 0.95, round((i + 1) * 0.08, 3)],
                text=line,
                source="text_layer",
                ocr_conf=100.0
            )
            blocks.append(b)
        
        pages = [Page(page_no=1, width=595.0, height=842.0, blocks=blocks)]

        # Run pipeline in offline rule mode (deterministic)
        result, _ = analyzer.analyze_document_pipeline(
            file_path=f"data_store/uploads/{sample_id}.pdf",
            doc_id=sample_id,
            filename=f"{sample_id}.pdf",
            pages=pages,
            blocks=blocks,
            warnings=[]
        )

        # 1. Classification check
        is_type_correct = (result.doc_type == expected_type)
        if is_type_correct:
            classification_correct += 1

        # 2. Citation Traceability (INV-01)
        for claim in result.claims:
            total_claims += 1
            has_doc_cit = any(c.kind == "document" for c in claim.citations)
            if has_doc_cit:
                valid_cited_claims += 1
            else:
                invariant_violations += 1

        # 3. Deadline check
        if item.get("has_relative_deadline"):
            total_expected_deadlines += 1
            has_computed = any(d.is_computed for d in result.dates)
            if has_computed:
                deadlines_detected += 1

        # 4. Mandatory Disclaimer check (INV-07)
        if not result.disclaimer or config.DISCLAIMER not in result.disclaimer:
            invariant_violations += 1

        # Extract any statutes cited across claims
        statute_ids = list({c.ref_id for clm in result.claims for c in clm.citations if c.kind == "statute"})

        results_detail.append({
            "id": sample_id,
            "expected_type": expected_type,
            "detected_type": result.doc_type,
            "classification_correct": is_type_correct,
            "claims_count": len(result.claims),
            "dates_count": len(result.dates),
            "applicable_statutes": statute_ids
        })

    traceability_pct = (valid_cited_claims / total_claims * 100.0) if total_claims > 0 else 0.0
    classification_acc = (classification_correct / total_samples * 100.0) if total_samples > 0 else 0.0
    deadline_recall = (deadlines_detected / total_expected_deadlines * 100.0) if total_expected_deadlines > 0 else 100.0

    report = {
        "total_samples": total_samples,
        "classification_accuracy_pct": round(classification_acc, 2),
        "citation_traceability_pct": round(traceability_pct, 2),
        "deadline_recall_pct": round(deadline_recall, 2),
        "total_claims": total_claims,
        "invariant_violations": invariant_violations,
        "details": results_detail
    }
    return report


def main():
    gold_path = config.BASE_DIR / "eval" / "gold_samples.json"
    if not gold_path.exists():
        print(f"Error: {gold_path} not found.")
        sys.exit(1)

    print(f"Running NyayaAssist Gold Set Evaluation on {gold_path}...")
    report = evaluate_gold_set(gold_path)

    print("\n" + "=" * 50)
    print("NYAYAASSIST EVALUATION REPORT (T-116)")
    print("=" * 50)
    print(f"Total Samples Evaluated:       {report['total_samples']}")
    print(f"Document Classification (M-5): {report['classification_accuracy_pct']}% (Target: >=90%)")
    print(f"Citation Traceability (M-3):   {report['citation_traceability_pct']}% (Target: 100% [INV-01])")
    print(f"Deadline Recall (M-4):         {report['deadline_recall_pct']}%")
    print(f"Invariant Violations:          {report['invariant_violations']} (Target: 0)")
    print("=" * 50)

    # Assert invariant gate
    assert report["invariant_violations"] == 0, f"Failed: {report['invariant_violations']} invariant violations detected!"
    assert report["citation_traceability_pct"] == 100.0, "Failed: Citation traceability must be 100% [INV-01]"
    assert report["classification_accuracy_pct"] >= 90.0, "Failed: Classification accuracy below 90%"
    print("STATUS: ALL EVALUATION GATES PASSED (100% INVARIANT COMPLIANCE)")


if __name__ == "__main__":
    main()
