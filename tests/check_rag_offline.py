"""RAG 파이프라인 오프라인 테스트: LLM 은 가짜로 대체, Neo4j 는 실제 사용. 실행: uv run python tests/check_rag_offline.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from maplerag.rag import pipeline as pl  # noqa: E402

GOOD = ("MATCH (t:maple__Skill)<-[:maple__changes]-(c:maple__PatchChange)-[:maple__changeType]->(ct), "
        "(c)-[:maple__inPatch]->(p:maple__Patch), (c)-[:prov__wasDerivedFrom]->(d) WHERE t.rdfs__label = '에르다 샤워' "
        "RETURN t.rdfs__label AS target, c.maple__attribute AS attribute, c.maple__beforeValue AS before, c.maple__afterValue AS after, "
        "replace(ct.uri,'https://example.org/maple#','') AS change_type, p.rdfs__label AS patch, toString(p.maple__patchDate) AS patch_date, "
        "c.maple__evidence AS evidence, d.maple__url AS source_url ORDER BY p.maple__patchDate DESC LIMIT 5")
calls = {"cypher": 0, "answer": 0}
SCRIPT = ["MATCH (n:NoSuchLabel RETURN n",                  # 1회차: 문법 오류
          "MATCH (n) DETACH DELETE n",                       # 2회차: 가드 차단
          GOOD]                                              # 3회차: 성공


def fake_structured(prompt, schema, config, lf_prompt=None):
    if schema is pl.Analysis:
        return pl.Analysis(in_scope="날씨" not in prompt.split("질문:")[-1], reason="테스트", entity_mentions=["에르다 샤워"])
    if schema is pl.CypherOut:
        c = SCRIPT[min(calls["cypher"], len(SCRIPT) - 1)]; calls["cypher"] += 1
        return pl.CypherOut(cypher=c)
    calls["answer"] += 1
    return pl.AnswerOut(answer="테스트 답변 [1]", sufficient=True, cited_rows=[1, 99])    # 99 는 범위 밖 → 무시되어야 함


pl._structured = fake_structured
# 네트워크 없이: Langfuse 조회 대신 코드의 기본 프롬프트 사용
pl.PS.client = lambda: (_ for _ in ()).throw(RuntimeError('offline'))
ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print("OK  " if cond else "FAIL", name)


r = pl.ask("에르다 샤워는 어떻게 바뀌었나?")
check("재시도 2회 후 성공(최대 3번 실행)", r["attempts"] == 3 and r["status"] == "answered")
check("1회차 문법 오류 기록", "Syntax" in r["history"][0]["error"] or "Error" in r["history"][0]["error"])
check("2회차 쓰기 쿼리는 가드가 차단", r["history"][1]["error"].startswith("가드 차단"))
check("개체 연결 후보에 '에르다 샤워' 포함", any(c["label"] == "에르다 샤워" for c in r["candidates"]))
check("근거 트리플 생성, 범위 밖 행 번호 무시", len(r["evidence"]) == 1 and r["evidence"][0]["source_url"].startswith("https://open.api.nexon.com"))
check("출처 URL 포함", len(r["sources"]) == 1)

calls.update(cypher=0); SCRIPT[:] = ["MATCH (n) DETACH DELETE n"] * 3
r = pl.ask("에르다 샤워는?")
check("계속 실패하면 3회 시도 후 query_failed", r["status"] == "query_failed" and r["attempts"] == 3)

r = pl.ask("오늘 날씨 어때?")
check("범위 밖 질문은 거절 (Cypher 생성 없이)", r["status"] == "refused" and "cypher" not in r)

calls.update(cypher=0); SCRIPT[:] = ["MATCH (n:maple__Skill) WHERE n.rdfs__label = '없는스킬' RETURN n.rdfs__label AS x"] * 3
r = pl.ask("없는스킬?")
check("빈 결과는 1회 완화 재시도 후 no_evidence", r["status"] == "no_evidence" and r["attempts"] == 2)

# 스트리밍: 노드 순서
SCRIPT[:] = [GOOD]; calls.update(cypher=0)
order = [n for n, _ in pl.stream("에르다 샤워는 어떻게 바뀌었나?")]
check(f"stream 노드 순서 {order}", order == ["analyze_question", "link_entities", "generate_cypher", "execute_query", "generate_answer"])
print("결과:", "OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
