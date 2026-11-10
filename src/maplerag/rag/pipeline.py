"""LangGraph RAG 파이프라인: 질문 → (범위 판단) → 개체 연결 → Cypher 생성 → 안전 실행(재시도≤2) → 근거 포함 답변.

노트북의 '검색(Milvus) → 생성' 대신 '그래프 질의(Text2Cypher) → 생성' 구조다. 벡터/임베딩은 쓰지 않는다.
모든 LLM 호출은 RunnableConfig 를 그대로 넘겨, 8단계에서 Langfuse CallbackHandler 를 붙이면 노드별로 트레이스가 남게 한다.
"""
from __future__ import annotations

import json
import time
from functools import lru_cache
from typing import Any, Literal, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from neo4j import READ_ACCESS, Query
from pydantic import BaseModel, Field

from maplerag.config import settings
from maplerag.graph.neo4j_client import get_driver
from maplerag.rag import prompt_store as PS
from maplerag.rag import prompts as P
from maplerag.rag.guard import TIMEOUT_SEC, CypherRejected, guard

MAX_RETRIES = 2          # 쿼리 실패 시 재생성 횟수 (최초 1회 + 재시도 2회 = 최대 3번 실행)
MAX_ROWS_TO_LLM = 40
MAX_TOKENS = 4000


# ---------------- 구조화 출력 스키마 ----------------
class Analysis(BaseModel):
    in_scope: bool
    reason: str
    entity_mentions: list[str] = Field(default_factory=list, max_length=5)


class CypherOut(BaseModel):
    cypher: str


class AnswerOut(BaseModel):
    answer: str
    sufficient: bool
    cited_rows: list[int] = Field(default_factory=list)


class RAGState(TypedDict, total=False):
    question: str
    analysis: dict
    candidates: list[dict]
    cypher: str
    attempts: int
    last_error: str
    rows: list[dict]
    history: list[dict]          # 시도별 {cypher, error, n_rows}
    answer: str
    evidence: list[dict]
    sources: list[str]
    status: Literal["answered", "refused", "no_evidence", "query_failed"]


# ---------------- 도우미 ----------------
def render(template: str, **kw: Any) -> str:
    for k, v in kw.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


@lru_cache(maxsize=1)
def _llm() -> ChatAnthropic:
    # 샘플링 파라미터는 지정하지 않는다(claude-sonnet-5-5 는 temperature 등 거부).
    return ChatAnthropic(model=settings.llm_model, max_tokens=MAX_TOKENS)


def _structured(prompt: str, schema: type[BaseModel], config: RunnableConfig | None, lf_prompt=None):
    """구조화 출력 호출. 강제 tool_choice 가 400 이라 json_schema 방식을 쓴다. 거절·파싱 실패는 예외로."""
    chain = _llm().with_structured_output(schema, method="json_schema", include_raw=True)
    if lf_prompt is not None:      # Langfuse 가 generation 을 이 프롬프트 버전에 연결하도록 메타데이터로 전달
        config = {**(config or {}), "metadata": {**((config or {}).get("metadata") or {}), "langfuse_prompt": lf_prompt}}
    out = chain.invoke(prompt, config=config)
    stop = (getattr(out["raw"], "response_metadata", {}) or {}).get("stop_reason")
    if stop == "refusal":
        raise RuntimeError("모델이 응답을 거절했습니다(refusal)")
    if out.get("parsed") is None:
        raise RuntimeError(f"구조화 출력 파싱 실패 (stop_reason={stop})")
    return out["parsed"]


def _jsonable(v: Any) -> Any:
    return v if isinstance(v, (str, int, float, bool, type(None))) else (
        [_jsonable(x) for x in v] if isinstance(v, list) else str(v))


def run_read_query(cypher: str) -> list[dict]:
    """읽기 전용 세션 + 서버측 타임아웃. 가드를 통과한 쿼리만 들어온다."""
    with get_driver() as d, d.session(default_access_mode=READ_ACCESS) as s:
        return [{k: _jsonable(v) for k, v in r.items()} for r in s.run(Query(cypher, timeout=TIMEOUT_SEC))]


# ---------------- 노드 ----------------
def analyze_question(state: RAGState, config: RunnableConfig) -> dict:
    r = PS.resolve("maple-analyze-question", scope=P.SCOPE_TEXT, question=state["question"])
    a = _structured(r.text, Analysis, config, r.lf_prompt)
    return {"analysis": a.model_dump(), "attempts": 0, "history": []}


def refuse(state: RAGState) -> dict:
    why = state["analysis"].get("reason", "")
    return {"status": "refused", "rows": [], "evidence": [], "sources": [],
            "answer": f"이 시스템은 메이플스토리 게임 정보(패치·스킬·아이템·이벤트 등) 질문에만 답할 수 있어요. ({why})"}


LINK_CYPHER = """
MATCH (n:Resource)
WHERE n.rdfs__label IS NOT NULL AND (
      replace(n.rdfs__label, ' ', '') CONTAINS $m
   OR any(a IN coalesce(n.skos__altLabel, []) WHERE replace(a, ' ', '') CONTAINS $m))
WITH n.rdfs__label AS label,
     [l IN labels(n) WHERE l STARTS WITH 'maple__' | replace(l, 'maple__', '')] AS classes, collect(n.uri) AS uris,
     CASE WHEN replace(n.rdfs__label, ' ', '') = $m THEN 0 ELSE 1 END AS rank
RETURN label, classes, size(uris) AS n_nodes, uris[0..3] AS sample_uris, rank
ORDER BY rank, size(label) LIMIT 5"""


def link_entities(state: RAGState) -> dict:
    """질문 속 이름 → 그래프 노드 후보. 임베딩 없이 라벨/별칭 부분일치(공백 무시)로 찾는다.
    같은 이름의 노드가 여러 개(예: 직업별 동명 스킬)면 (label, classes) 로 묶어 n_nodes 를 알려 준다 —
    uri 몇 개만 골라 쓰면 나머지가 누락되므로, 생성 단계가 label 일치로 모두 포함하도록 유도한다."""
    cands, seen = [], set()
    for m in state["analysis"].get("entity_mentions", []):
        key = m.replace(" ", "")
        if not key:
            continue
        for r in run_read_query_params(LINK_CYPHER, {"m": key}):
            k = (r["label"], tuple(r["classes"]))
            if k not in seen:
                seen.add(k)
                cands.append({"mention": m, "label": r["label"], "classes": r["classes"], "n_nodes": r["n_nodes"],
                              "sample_uris": r["sample_uris"]})
    return {"candidates": cands}


def run_read_query_params(cypher: str, params: dict) -> list[dict]:
    with get_driver() as d, d.session(default_access_mode=READ_ACCESS) as s:
        return [r.data() for r in s.run(Query(cypher, timeout=TIMEOUT_SEC), params)]


def generate_cypher(state: RAGState, config: RunnableConfig) -> dict:
    fb = ""
    if state.get("history"):
        last = state["history"][-1]
        fb = (f"[이전 시도 실패] 쿼리:\n{last['cypher']}\n" +
              (f"오류: {last['error']}" if last["error"] else "결과가 0행이었습니다. 조건을 완화(CONTAINS, 별칭, 다른 라벨)해 다시 작성하세요."))
    r = PS.resolve("maple-text2cypher", schema=P.GRAPH_SCHEMA, examples=P.CYPHER_EXAMPLES,
                   analysis=json.dumps(state["analysis"], ensure_ascii=False),
                   candidates=json.dumps(state.get("candidates", []), ensure_ascii=False) or "[]",
                   feedback=fb, question=state["question"])
    return {"cypher": _structured(r.text, CypherOut, config, r.lf_prompt).cypher.strip()}


def execute_query(state: RAGState) -> dict:
    attempts = state.get("attempts", 0) + 1
    entry = {"cypher": state["cypher"], "error": "", "n_rows": 0}
    rows: list[dict] = []
    try:
        safe = guard(state["cypher"])
        entry["cypher"] = safe
        t0 = time.time()
        rows = run_read_query(safe)
        entry["seconds"] = round(time.time() - t0, 2)
        entry["n_rows"] = len(rows)
    except CypherRejected as e:
        entry["error"] = f"가드 차단: {e}"
    except Exception as e:                                   # 문법 오류, 타임아웃 등
        entry["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    return {"attempts": attempts, "rows": rows, "last_error": entry["error"], "history": state.get("history", []) + [entry]}


def after_execute(state: RAGState) -> str:
    retry_left = state["attempts"] <= MAX_RETRIES
    if state["last_error"]:
        return "retry" if retry_left else "answer"
    if not state["rows"] and state["attempts"] == 1:         # 빈 결과는 1회만 완화 재시도
        return "retry"
    return "answer"


def _evidence(rows: list[dict], cited: list[int]) -> tuple[list[dict], list[str]]:
    """인용된 행에서 근거 트리플(주어-변경-값 + 패치 + 원문 문장 + 출처 URL)을 코드로 만든다. LLM 이 지어내지 못하게."""
    ev, urls = [], []
    for i in cited:
        if not 1 <= i <= len(rows):
            continue
        r = rows[i - 1]
        ev.append({"row": i, "subject": r.get("target") or r.get("event"), "predicate": r.get("change_type") or "rewards",
                   "attribute": r.get("attribute"), "before": r.get("before"), "object": r.get("after") or r.get("reward"),
                   "patch": r.get("patch"), "patch_date": r.get("patch_date"), "evidence": r.get("evidence"),
                   "source_url": r.get("source_url"), "raw": r})
        if r.get("source_url") and r["source_url"] not in urls:
            urls.append(r["source_url"])
    return ev, urls


def generate_answer(state: RAGState, config: RunnableConfig) -> dict:
    if state.get("last_error"):
        return {"status": "query_failed", "evidence": [], "sources": [],
                "answer": f"질의를 만들거나 실행하지 못했습니다({MAX_RETRIES + 1}회 시도). 마지막 오류: {state['last_error']}"}
    rows = state.get("rows", [])
    if not rows:
        return {"status": "no_evidence", "evidence": [], "sources": [],
                "answer": "그래프에서 관련 정보를 찾지 못했습니다. (수집 범위: 최신 패치노트 20건·이벤트 공지)"}
    shown = rows[:MAX_ROWS_TO_LLM]
    listing = "\n".join(f"[{i}] {json.dumps(r, ensure_ascii=False)}" for i, r in enumerate(shown, 1))
    r = PS.resolve("maple-answer", question=state["question"], n_rows=len(rows), n_shown=len(shown), rows=listing)
    out = _structured(r.text, AnswerOut, config, r.lf_prompt)
    ev, urls = _evidence(shown, out.cited_rows)
    if not out.sufficient:
        return {"status": "no_evidence", "answer": out.answer, "evidence": [], "sources": []}
    return {"status": "answered", "answer": out.answer, "evidence": ev, "sources": urls}


# ---------------- 그래프 조립 ----------------
def build_graph():
    g = StateGraph(RAGState)
    g.add_node("analyze_question", analyze_question)
    g.add_node("refuse", refuse)
    g.add_node("link_entities", link_entities)
    g.add_node("generate_cypher", generate_cypher)
    g.add_node("execute_query", execute_query)
    g.add_node("generate_answer", generate_answer)
    g.add_edge(START, "analyze_question")
    g.add_conditional_edges("analyze_question", lambda s: "link" if s["analysis"]["in_scope"] else "refuse",
                            {"link": "link_entities", "refuse": "refuse"})
    g.add_edge("link_entities", "generate_cypher")
    g.add_edge("generate_cypher", "execute_query")
    g.add_conditional_edges("execute_query", after_execute, {"retry": "generate_cypher", "answer": "generate_answer"})
    g.add_edge("generate_answer", END)
    g.add_edge("refuse", END)
    return g.compile()


def ask(question: str, config: RunnableConfig | None = None) -> RAGState:
    return build_graph().invoke({"question": question}, config=config)


def stream(question: str, config: RunnableConfig | None = None):
    """노드가 끝날 때마다 (노드명, 상태 변경분) 을 내보낸다 — 진행 상황을 단계별로 보여 줄 때 쓴다."""
    for chunk in build_graph().stream({"question": question}, config=config, stream_mode="updates"):
        for node, upd in chunk.items():
            yield node, upd
