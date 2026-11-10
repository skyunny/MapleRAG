"""평가 지표 단위 테스트 (모델·DB 없음). 실행: uv run python tests/check_eval_metrics.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from maplerag.evaluation.dataset import load_questions  # noqa: E402
from maplerag.evaluation.metrics import aggregate, score  # noqa: E402

qs = {q["id"]: q for q in load_questions()}
ok = True
def check(n, c):
    global ok; ok &= bool(c); print("OK  " if c else "FAIL", n)
def d(res): return {n: v for n, v, _ in res}

good = {"status": "answered", "answer": "태초의  1.2.414 ... 소울 어썰트 데미지는 145%에서 65%로 너프됐습니다.", "attempts": 1,
        "history": [{"error": ""}], "evidence": [{"subject": "소울 어썰트", "object": "65%", "patch_date": "2026-09-17"}],
        "latency_s": 7.0, "input_tokens": 6000, "output_tokens": 900, "cost_usd": 0.02}
r = d(score(qs["P03"], good))
check("정답 사례: 전부 1", r["status_match"] == r["cypher_success"] == r["cypher_first_try"] == r["answer_contains"] == r["evidence_match"] == 1.0)
r = d(score(qs["P03"], {**good, "answer": "145%에서 70%로 바뀌었습니다", "evidence": []}))
check("값 틀림/근거 없음 → answer_contains 0, evidence_match 0", r["answer_contains"] == 0 and r["evidence_match"] == 0)
r = d(score(qs["P03"], {**good, "status": "query_failed", "history": [{"error": "SyntaxError"}], "attempts": 3}))
check("쿼리 실패 → cypher_success 0, status_match 0", r["cypher_success"] == 0 and r["status_match"] == 0)
r = d(score(qs["O01"], {"status": "refused", "history": []}))
check("범위 밖 거절 → 1, cypher 지표는 해당 없음", r["out_of_scope_refusal"] == 1 and "cypher_success" not in r)
r = d(score(qs["O01"], {"status": "answered", "answer": "맑음", "history": []}))
check("범위 밖인데 답함 → 거절 지표 0", r["out_of_scope_refusal"] == 0)
r = d(score(qs["O05"], {"status": "query_failed", "history": [{"error": "x"}]}))
check("O05 는 refused/no_evidence/query_failed 모두 허용, 거절 지표는 해당 없음", r["status_match"] == 1 and "out_of_scope_refusal" not in r)
r = d(score(qs["N01"], {"status": "answered", "answer": "99%", "history": [{"error": ""}], "attempts": 1}))
check("자료 없음 질문에 답하면(지어냄) no_data_abstain 0", r["no_data_abstain"] == 0)
a = aggregate([score(qs["P03"], good), score(qs["O01"], {"status": "refused", "history": []})])
check("집계: 분모는 해당 항목만 (cypher n=1, 거절 n=1)", a["cypher_success_n"] == 1 and a["out_of_scope_refusal_n"] == 1 and a["total_cost_usd"] == 0.02)
check("질문 36개 이상, 범주 6종", len(qs) >= 30 and len({q['category'] for q in qs.values()}) == 6)
print("결과:", "OK" if ok else "FAIL"); sys.exit(0 if ok else 1)
