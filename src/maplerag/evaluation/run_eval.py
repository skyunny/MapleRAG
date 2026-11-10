"""평가 실행기. 기본은 '모델 호출 없는' 동작만 한다.

  uv run python -m maplerag.evaluation.run_eval --upload                 # Langfuse 데이터셋 등록(멱등, 모델 호출 없음)
  uv run python -m maplerag.evaluation.run_eval --dry-run                # 실행 대상·비용 추정만 출력
  uv run python -m maplerag.evaluation.run_eval --run --limit 3          # 3문항만 실제 실행 (모델 호출 발생)
  uv run python -m maplerag.evaluation.run_eval --run --ids P01,O01      # 지정 문항
  uv run python -m maplerag.evaluation.run_eval --run --all --yes        # 전체 실행(비용 추정 후 명시 승인 필요)

run 이름 규칙: {YYYYMMDD-HHMM}_{모델}_p{분석.Cypher.답변 프롬프트 버전}_n{문항수}_{라벨}
  예) 20261111-1530_claude-sonnet-5-5_p1.2.1_n36_baseline   → 모델·프롬프트·규모를 이름만으로 구분
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from langchain_core.callbacks import UsageMetadataCallbackHandler

from maplerag.config import settings
from maplerag.evaluation.dataset import DATASET_NAME, fingerprint, load_questions
from maplerag.evaluation.metrics import aggregate, estimate_cost, score
from maplerag.graph.neo4j_client import get_driver

EXPERIMENT = "maple-rag-eval"
# 호출당 평균 비용 추정(8단계 실측 1건 기준 + 재시도 여유): 답변 가능 ≈ $0.022, 자료 없음 ≈ $0.03, 범위 밖 ≈ $0.003
EST = {"answered": 0.022, "no_evidence": 0.03, "refused": 0.003, "other": 0.03}


def item_id(qid: str) -> str:
    return f"{DATASET_NAME}-{qid}"


def upload() -> int:
    from maplerag.rag.prompt_store import register_all
    from maplerag.rag.tracing import get_langfuse
    print("프롬프트 등록(멱등):", register_all("자동 등록: 코드의 prompts.py 와 동기화"))
    lf = get_langfuse()
    qs = load_questions()
    try:
        lf.create_dataset(name=DATASET_NAME, description="메이플 RAG 평가 질문(eval/questions.jsonl)",
                          metadata={"fingerprint": fingerprint(qs), "count": len(qs)})
    except Exception as e:                           # 이미 있으면 항목만 upsert
        print("데이터셋 생성 건너뜀:", str(e)[:80])
    for q in qs:
        lf.create_dataset_item(dataset_name=DATASET_NAME, id=item_id(q["id"]), input={"question": q["question"]},
                               expected_output={**q["expected"], "gold_cypher": q.get("gold_cypher")},
                               metadata={"qid": q["id"], "category": q["category"], "note": q.get("note", "")})
    lf.flush()
    return len(qs)


def estimate(qs: list[dict]) -> float:
    return sum(EST.get(q["expected"]["status"][0], EST["other"]) for q in qs)


def prompt_versions() -> str:
    from maplerag.rag.prompt_store import PROMPTS, client
    vs = []
    for n in ("maple-analyze-question", "maple-text2cypher", "maple-answer"):
        vs.append(str(client().get_prompt(n, label="production", type="text", cache_ttl_seconds=0).version))
    return ".".join(vs)


def run_name(n: int, label: str) -> str:
    return f"{datetime.now():%Y%m%d-%H%M}_{settings.llm_model}_p{prompt_versions()}_n{n}_{label}"


def node_count() -> int:
    with get_driver().session() as s:
        return s.run("MATCH (n) RETURN count(n) AS c").single()["c"]


def _question_of(item) -> str:
    inp = item["input"] if isinstance(item, dict) else item.input
    return inp["question"]


def make_task():
    from langfuse.langchain import CallbackHandler
    from maplerag.rag.pipeline import build_graph
    graph = build_graph()

    def task(*, item, **kwargs):
        usage = UsageMetadataCallbackHandler()
        cfg = {"callbacks": [CallbackHandler(), usage], "run_name": "maple-rag",
               "metadata": {"langfuse_tags": ["maplerag", "eval", f"model:{settings.llm_model}"]}}
        t0 = time.time()
        st = graph.invoke({"question": _question_of(item)}, config=cfg)
        lat = time.time() - t0
        tin = sum(u.get("input_tokens", 0) for u in usage.usage_metadata.values())
        tout = sum(u.get("output_tokens", 0) for u in usage.usage_metadata.values())
        return {"status": st.get("status"), "answer": st.get("answer"), "evidence": st.get("evidence"),
                "sources": st.get("sources"), "history": st.get("history"), "attempts": st.get("attempts", 0),
                "latency_s": round(lat, 2), "input_tokens": tin, "output_tokens": tout, "cost_usd": round(estimate_cost(tin, tout), 5)}
    return task


def make_evaluator(by_qid: dict):
    from langfuse import Evaluation

    def evaluator(*, input, output, expected_output=None, metadata=None, **kwargs):
        q = by_qid[(metadata or {}).get("qid") or next(k for k, v in by_qid.items() if v["question"] == input["question"])]
        return [Evaluation(name=n, value=v, comment=c or None) for n, v, c in score(q, output)]
    return evaluator


def make_run_evaluator():
    from langfuse import Evaluation

    def run_eval(*, item_results, **kwargs):
        per = [[(e.name, e.value, "") for e in r.evaluations] for r in item_results]
        return [Evaluation(name=k, value=v) for k, v in aggregate(per).items()]
    return run_eval


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", action="store_true"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true"); ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--ids", default=""); ap.add_argument("--all", action="store_true")
    ap.add_argument("--yes", action="store_true", help="--all 실행 승인"); ap.add_argument("--label", default="baseline")
    a = ap.parse_args()
    qs = load_questions()
    if a.ids:
        want = set(a.ids.split(","))
        qs = [q for q in qs if q["id"] in want]
    elif not a.all:
        qs = qs[: a.limit]
    if a.upload:
        print(f"데이터셋 '{DATASET_NAME}' 에 {upload()}개 항목 등록(멱등)")
    if a.dry_run or (a.run and a.all and not a.yes):
        print(f"대상 {len(qs)}문항 / 추정 비용 약 ${estimate(qs):.2f} (재시도가 잦으면 최대 2배) | 모델 호출 약 {sum(1 if q['expected']['status'][0]=='refused' else 3 for q in qs)}회 이상")
        if a.run:
            print("전체 실행은 --yes 가 필요합니다."); sys.exit(2)
        return
    if not a.run:
        return
    from maplerag.rag.tracing import get_langfuse
    lf = get_langfuse()
    upload()
    by_qid = {q["id"]: q for q in load_questions()}
    ds = lf.get_dataset(DATASET_NAME)
    wanted = {item_id(q["id"]) for q in qs}
    items = [i for i in ds.items if i.id in wanted]
    name = run_name(len(items), a.label)
    before = node_count()
    print(f"실행: {name} | {len(items)}문항 | 추정 ${estimate(qs):.2f}")
    res = lf.run_experiment(name=EXPERIMENT, run_name=name, data=items, task=make_task(),
                            evaluators=[make_evaluator(by_qid)], run_evaluators=[make_run_evaluator()], max_concurrency=1,
                            metadata={"dataset": DATASET_NAME, "fingerprint": fingerprint(load_questions())})
    lf.flush()
    after = node_count()
    print(res.format() if hasattr(res, "format") else res)
    print(f"그래프 노드 수 변화: {before} → {after} ({'변화 없음' if before == after else '!! 변경됨'})")


if __name__ == "__main__":
    main()
