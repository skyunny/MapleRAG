"""노트북 2종을 생성한다(소스 관리 편의: .ipynb JSON 대신 이 스크립트를 수정해 재생성).
실행: uv run python notebooks/make_notebooks.py
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def md(s):
    return {"cell_type": "markdown", "metadata": {}, "source": s.strip("\n").splitlines(True)}


def code(s):
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": s.strip("\n").splitlines(True)}


def nb(cells):
    return {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                         "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}


N1 = nb([
    md("""# 01. 지식그래프 구축 (RDF 트리플 → Neo4j + n10s)
수업 노트북(Milvus 벡터 RAG)의 **'문서 → 청크 → 임베딩 → 벡터DB'** 대신 **'공지 → 추출 → 트리플 → 그래프DB'** 로 대체한 파이프라인입니다.
이 노트북은 **얇은 실행·점검용**입니다. 실제 로직은 `src/maplerag/` 에 있습니다.

| 수업 노트북 | 이 프로젝트 |
|---|---|
| 문서 로드·청킹 | `ingest/` (Open API 수집) + `extract/sections.py` (■ 소제목 단위 청크) |
| 임베딩 + Milvus 적재 | `extract/normalizer.py` (트리플화·정규화) + `graph/load_n10s.py` (n10s 적재) |
| 데이터 품질 확인 | `graph/validate.py` (SHACL 위반 건수) |

**비용 안전장치**: 아래 `RUN_LLM` 은 기본 `False` 입니다. 켜면 API 호출·비용이 발생합니다."""),
    code("""RUN_LLM = False       # True: LLM 추출 일괄 실행(유료). 기본은 캐시된 결과를 사용
RUN_LOAD = True       # Neo4j 적재(멱등: 다시 실행해도 개수 불변)
import json
from collections import Counter
from maplerag.config import key_status, settings
from maplerag.graph.neo4j_client import check_connection
print("키 상태(값은 출력 안 함):", key_status(settings))
print("Neo4j:", check_connection())"""),
    md("## 1. 수집 결과 확인 (RawDocument)\n공식 Open API 의 최신 공지만 사용합니다(robots.txt 허용 범위, 비공식 출처 미수집). 재수집은 `maplerag.ingest.run_notices` (일 1,000회 한도)."),
    code("""from maplerag.ingest.base import RAW_DIR
docs = [json.loads(l) for l in (RAW_DIR / "docs" / "notices.jsonl").read_text(encoding="utf-8").splitlines()]
print("공지", len(docs), "건 | 유형:", dict(Counter(d["doc_type"] for d in docs)), "| tier:", dict(Counter(d["tier"] for d in docs)))
dates = [d["meta"]["date"][:10] for d in docs if d["doc_type"] == "patch_note"]
print("패치노트 기간:", min(dates), "~", max(dates))"""),
    md("## 2. LLM 추출 결과 확인\n구조화 출력(Pydantic) → 코드 검증(근거 문장이 본문에 있는지, 값이 근거에 있는지)을 거친 결과입니다. 실행은 `maplerag.extract.run_batch` (유료)."),
    code("""if RUN_LLM:
    from maplerag.extract import run_batch
    run_batch.main()
rows = [json.loads(l) for l in (RAW_DIR / "docs" / "extraction_all.jsonl").read_text(encoding="utf-8").splitlines()]
ok = [r for r in rows if not r["error"]]
print(f"청크 {len(rows)}개 중 성공 {len(ok)} / 실패 {len(rows)-len(ok)}  (실패는 API 크레딧 부족 때문)")
print("변경", sum(len(r["changes"]) for r in ok), "건 / 관계", sum(len(r["relations"]) for r in ok), "건 / 코드 검증 폐기", sum(len(r["dropped"]) for r in ok), "건")
print("변경 종류:", dict(Counter(c["change_type"] for r in ok for c in r["changes"])))"""),
    md("## 3. 정규화 → RDF → SHACL 검증\n별칭 병합·중복 제거·시간(validFrom/inPatch)·출처(prov:wasDerivedFrom) 부여. 같은 입력을 두 번 넣어도 같은 결과(멱등)여야 합니다."),
    code("""from maplerag.extract.run_normalize import RDF_DIR, build
from maplerag.graph.validate import load, validate_graph
g, norm = build()
g2, _ = build()
print("트리플", len(g), "| 멱등:", set(g) == set(g2))
full = load(RDF_DIR / "notices.ttl")
for t in g:
    full.add(t)
rep = validate_graph(full)
print("SHACL conforms =", rep["conforms"], "| 위반", rep["violation_count"], "건", rep["by_path"])
print("정규화 통계:", dict(norm.stats))"""),
    md("## 4. Neo4j(n10s) 적재\n멱등 적재입니다. 기존 그래프를 지우고 다시 넣으려면 터미널에서 `uv run python -m maplerag.graph.load_n10s --drop-old` (확인 프롬프트 있음)."),
    code("""from maplerag.graph import load_n10s as L
if RUN_LOAD:
    with L.get_driver() as d, d.session() as s:
        print(L.ensure_config(s))
        print(L.import_graph(s, L.read_graph()))
        print("그래프 건수:", L.counts(s))"""),
    md("## 5. 연결 관계 둘러보기 (Cypher)\nNeo4j Browser(`http://localhost:7475`)에서 같은 쿼리를 실행하면 그래프로 볼 수 있습니다."),
    code("""from maplerag.rag.pipeline import run_read_query
print(run_read_query("MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS n ORDER BY n DESC LIMIT 12"))
print(run_read_query('''MATCH (j:maple__Job)-[:maple__hasSkill]->(s:maple__Skill)<-[:maple__changes]-(c:maple__PatchChange)-[:maple__inPatch]->(p:maple__Patch)
RETURN j.rdfs__label AS job, s.rdfs__label AS skill, c.maple__beforeValue AS before, c.maple__afterValue AS after, toString(p.maple__patchDate) AS date LIMIT 5'''))"""),
])

N2 = nb([
    md("""# 02. RAG 데모 (LangGraph + Text2Cypher + Langfuse)
`질문 → analyze_question → link_entities → generate_cypher → execute_query(재시도≤2) → generate_answer`
벡터 검색 대신 **그래프 질의**로 근거를 찾고, 답변에는 **근거 트리플·출처 URL**이 붙습니다. 로직은 `src/maplerag/rag/`.

**비용 안전장치**: `RUN_LLM = False` 가 기본입니다. 켜면 질문 1개당 모델 호출 약 3회(≈$0.02)가 발생합니다."""),
    code("""RUN_LLM = False
QUESTION = "소울 어썰트 데미지 변화는?"
from maplerag.rag.guard import CypherRejected, guard"""),
    md("## 1. Cypher 안전장치 (모델 호출 없음)\n읽기 전용·단일문·LIMIT 을 코드로 강제합니다. 모델이 쓰기 쿼리를 만들어도 실행 전에 막힙니다."),
    code("""for q in ["MATCH (n:maple__Skill) RETURN n.rdfs__label",
          "MATCH (n) DETACH DELETE n",
          "CALL apoc.cypher.doIt('x', {})",
          "MATCH (n) OPTIONAL MATCH (n)-[:r]->(m) WHERE n.x=1 RETURN n"]:
    try:
        print("허용 →", guard(q))
    except CypherRejected as e:
        print("차단 →", e, "|", q[:40])"""),
    md("## 2. 개체 연결 (모델 호출 없음)\n임베딩 없이 라벨·별칭 부분일치로 후보를 찾습니다. 같은 이름의 노드가 여러 개면 `n_nodes` 로 알려 줍니다."),
    code("""from maplerag.rag.pipeline import link_entities
for c in link_entities({"analysis": {"entity_mentions": ["에르다 샤워", "유피테르"]}})["candidates"]:
    print(c["label"], c["classes"], "노드 수:", c["n_nodes"])"""),
    md("## 3. 질문 실행 (유료, 기본 꺼짐)\n노드가 끝날 때마다 상태 변경분을 보여 주고, 마지막에 Langfuse 트레이스 링크를 출력합니다."),
    code("""if RUN_LLM:
    from maplerag.rag.service import ask_stream
    final, trace = {}, {}
    for node, upd in ask_stream(QUESTION, tags=["notebook"]):
        if node == "__trace__":
            trace = upd
            continue
        final.update(upd)
        print(f"[{node}]", {k: (v if k != "rows" else len(v)) for k, v in upd.items() if k in ("cypher", "last_error", "attempts", "status", "rows")})
    print("답변:", final["answer"])
    print("상태:", final["status"])
    for e in final.get("evidence", [])[:5]:
        print("  근거:", e["subject"], e["attribute"], e["before"], "→", e["object"], e["patch_date"], "|", e["evidence"])
    print("출처:", final["sources"])
    from langfuse import get_client
    print("트레이스:", get_client().get_trace_url(trace_id=trace["trace_id"]))
else:
    print("RUN_LLM=False: 모델 호출을 건너뜁니다. 터미널: uv run python -m maplerag.rag.run 질문")"""),
    md("## 4. 캐릭터 조회 경로 (LLM 없음)\n닉네임 → Open API → **메모리 임시 트리플** → SPARQL. 영구 저장하지 않습니다. 응답에는 `Data based on NEXON Open API` 표기가 붙습니다.\n응답 캐시가 없으면 Open API 를 최대 3회 호출합니다(개발 키: 초당 5회·일 1,000회)."),
    code("""from maplerag.character.lookup import ask_character
r = ask_character("꽃게쥬", "스타포스 높은 장비는?")
print(r.answer)
print(f"임시 트리플 {r.triples}개 / 이번 실제 API 호출 {r.live_calls}회 / {r.attribution}")"""),
    md("## 5. 평가 (Langfuse 데이터셋)\n질문 36개는 `eval/questions.jsonl` (코드와 분리). 정답이 그래프에 실제로 있는지 모델 없이 확인합니다. 실행은 터미널 `maplerag.evaluation.run_eval` (기본 3문항, 전체는 `--all --yes`)."),
    code("""from collections import Counter
from maplerag.evaluation.dataset import fingerprint, gold_check, load_questions
qs = load_questions()
print("질문", len(qs), "개:", dict(Counter(q["category"] for q in qs)), "| 지문", fingerprint(qs))
r = gold_check()
print(f"정답 검증: {r['ok']}/{r['checked']} 통과", r["problems"])"""),
])

if __name__ == "__main__":
    for name, obj in [("01_build_kg", N1), ("02_rag_demo", N2)]:
        (HERE / f"{name}.ipynb").write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        print("생성:", name)
