from __future__ import annotations

import json
from pathlib import Path


def test_ab_question_fixture_has_fifteen_unique_questions():
    path = Path(__file__).parent / "fixtures" / "retrieval_harness" / "ab_questions.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    questions = [row["query"] for row in rows]
    assert len(questions) == 15
    assert len(set(questions)) == 15
    assert all(isinstance(question, str) and question.strip() for question in questions)
