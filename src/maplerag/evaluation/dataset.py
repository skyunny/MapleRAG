"""평가 질문 로드·검증. 질문 원본은 eval/questions.jsonl (코드와 분리, 사람이 검토·수정하는 파일)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
QUESTIONS = ROOT / "eval" / "questions.jsonl"
DATASET_NAME = "maple-rag-eval-v1"      # 질문 정의를 바꾸면 v2 로 올려 이전 run 과 섞이지 않게 한다


def load_questions(path: Path = QUESTIONS) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def fingerprint(qs: list[dict]) -> str:
    return hashlib.sha1(json.dumps(qs, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:10]


def _norm(s: str) -> str:
    return "".join(str(s).split())


def gold_check() -> dict:
    """정답이 실제로 그래프에 있는지 모델 없이 확인한다. 답변이 가능해야 할 질문은 gold_cypher 가 1행 이상을 돌려주고,
    answer_contains 의 각 그룹에서 최소 하나의 표기가 gold 결과에 있어야 한다(없으면 '미검증 그룹')."""
    from maplerag.rag.pipeline import run_read_query
    out = {"checked": 0, "ok": 0, "problems": []}
    for q in load_questions():
        exp = q["expected"]
        if exp["status"] != ["answered"]:
            continue
        out["checked"] += 1
        rows = run_read_query(q["gold_cypher"])
        text = _norm(json.dumps(rows, ensure_ascii=False))
        bad = [g for g in exp["answer_contains"] if not any(_norm(a) in text for a in g)]
        if not rows:
            out["problems"].append((q["id"], "gold 결과 0행"))
        elif bad:
            out["problems"].append((q["id"], f"gold 에 없는 그룹: {bad}"))
        else:
            out["ok"] += 1
    return out


if __name__ == "__main__":
    qs = load_questions()
    from collections import Counter
    print(f"질문 {len(qs)}개 | 분포 {dict(Counter(q['category'] for q in qs))} | 지문 {fingerprint(qs)}")
    r = gold_check()
    print(f"정답 검증: {r['ok']}/{r['checked']} 통과")
    for p in r["problems"]:
        print("  문제:", p)
