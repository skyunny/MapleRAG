"""Cypher 가드 테스트 (DB·LLM 불필요). 실행: uv run python tests/check_guard.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from maplerag.rag.guard import CypherRejected, guard  # noqa: E402

OK = [
    ("MATCH (n:maple__Skill) RETURN n.rdfs__label", "LIMIT 50"),                       # LIMIT 자동 추가
    ("MATCH (n) RETURN n LIMIT 500", "LIMIT 100"),                                      # 과도한 LIMIT 축소
    ("MATCH (n) WHERE n.rdfs__label CONTAINS '세트 SET DELETE' RETURN n LIMIT 5", "LIMIT 5"),  # 문자열 안 키워드는 허용
    ("MATCH (n) RETURN n // CREATE 주석\nLIMIT 3", "LIMIT 3"),
    ("MATCH (n) RETURN n;", "LIMIT 50"),
    ("MATCH (n)-[:r]->(ct) WHERE ct.uri = 'https://example.org/maple#Buff' RETURN n LIMIT 5", "LIMIT 5"),   # 문자열 안 // 보존(실제 버그였음)                                                # 끝 세미콜론은 제거
]
BAD = ["MATCH (n) OPTIONAL MATCH (n)-[:r]->(m) WHERE n.x=1 RETURN n",
       "CREATE (n:X) RETURN n", "MATCH (n) DETACH DELETE n", "MATCH (n) SET n.a=1 RETURN n",
       "MATCH (n) REMOVE n.a RETURN n", "MERGE (n:X {a:1}) RETURN n", "CALL db.labels()",
       "CALL apoc.cypher.doIt('x',{})", "MATCH (n) RETURN n; MATCH (m) DETACH DELETE m",
       "LOAD CSV FROM 'file:///x' AS r RETURN r", "MATCH (n) CALL { WITH n SET n.a=1 } RETURN n",
       "DROP INDEX x", "", "SHOW CONSTRAINTS", "MATCH (n) DELETE n", "MATCH (n) FOREACH (x IN [1] | SET n.a=1) RETURN n",
       "MATCH (n) RETURN n UNION CALL n10s.rdf.delete.inline('', 'Turtle')"]
fail = 0
for q, tail in OK:
    try:
        out = guard(q)
        good = out.rstrip().endswith(tail)
    except CypherRejected as e:
        good, out = False, str(e)
    fail += not good
    print("OK  " if good else "FAIL", "허용 기대:", q[:50].replace("\n", " "), "→", out[-30:])
for q in BAD:
    try:
        guard(q); rejected = False
    except CypherRejected:
        rejected = True
    fail += not rejected
    print("OK  " if rejected else "FAIL", "차단 기대:", q[:60])
print("결과:", "OK" if fail == 0 else f"FAIL {fail}건")
sys.exit(1 if fail else 0)
