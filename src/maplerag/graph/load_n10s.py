"""RDF → Neo4j(n10s) 적재. 멱등: 같은 URI 는 같은 노드로 합쳐진다(재적재 시 개수 불변을 verify 로 확인).

설정 근거(neosemantics 공식 문서 config/reference 확인):
- handleVocabUris=SHORTEN / handleRDFTypes=LABELS : 속성·관계명은 'maple__xxx' 접두 형태, rdf:type 은 노드 라벨.
- handleMultival=ARRAY + multivalPropList=[skos:altLabel] : 별칭만 배열, 나머지는 단일값(OVERWRITE).
- keepLangTag=false : 라벨이 'name@ko' 로 오염되지 않게 한다(한국어 단일 언어).
- Graph Config 는 데이터 적재 후 변경 불가 → 바꾸려면 drop_old(전체 삭제) 후 재적재.
- 사전 조건: (:Resource {uri}) 유일성 제약.
실행: uv run python -m maplerag.graph.load_n10s [--drop-old]   (drop-old 는 확인 프롬프트가 있음)
"""
import argparse
import sys

from rdflib import Graph

from maplerag.graph.neo4j_client import get_driver
from maplerag.graph.validate import load
from maplerag.ingest.base import RAW_DIR

RDF_DIR = RAW_DIR.parent / "rdf"
CHUNK = 5000
ALT = "http://www.w3.org/2004/02/skos/core#altLabel"
CONFIG = {"handleVocabUris": "SHORTEN", "handleMultival": "ARRAY", "multivalPropList": [ALT],
          "keepLangTag": False, "handleRDFTypes": "LABELS"}


PREFIXES = {"maple": "https://example.org/maple#", "prov": "http://www.w3.org/ns/prov#"}


def read_graph() -> Graph:
    g = load(RDF_DIR / "notices.ttl")
    g.parse(RDF_DIR / "extraction.ttl", format="turtle")
    return g


def ensure_config(s) -> str:
    s.run("CREATE CONSTRAINT n10s_unique_uri IF NOT EXISTS FOR (r:Resource) REQUIRE r.uri IS UNIQUE").consume()
    if s.run("CALL n10s.graphconfig.show()").data():
        return "기존 Graph Config 사용"
    s.run("CALL n10s.graphconfig.init($c)", c=CONFIG).consume()
    # 접두어를 미리 등록해야 라벨/속성이 ns0__ 대신 maple__ 처럼 읽기 좋게 저장된다(Text2Cypher 프롬프트에도 유리)
    for pfx, ns in PREFIXES.items():
        s.run("CALL n10s.nsprefixes.add($p, $n)", p=pfx, n=ns).consume()
    return "Graph Config 신규 생성"


def drop_old(s) -> None:
    """전체 삭제(파괴적). 호출자가 사용자 확인을 받은 뒤에만 부른다."""
    while s.run("MATCH (n) WITH n LIMIT 10000 DETACH DELETE n RETURN count(*) AS c").single()["c"]:
        pass
    s.run("CALL n10s.graphconfig.drop()").consume()
    for c in s.run("SHOW CONSTRAINTS YIELD name RETURN name").data():
        s.run(f"DROP CONSTRAINT {c['name']}").consume()


def import_graph(s, g: Graph) -> dict:
    """N-Triples 줄 단위로 나눠 inline 적재. (BNode 가 없는 그래프라 줄 분할이 안전하다 — 있으면 중단)"""
    nt = sorted(g.serialize(format="nt").splitlines())      # 정렬: 적재 순서를 고정(단일값 속성 덮어쓰기의 비결정성 방지)
    if any(" _:" in l or l.startswith("_:") for l in nt):
        raise RuntimeError("BNode 가 있어 줄 단위 분할 적재를 할 수 없습니다")
    loaded = 0
    for i in range(0, len(nt), CHUNK):
        r = s.run("CALL n10s.rdf.import.inline($p, 'N-Triples')", p="\n".join(nt[i:i + CHUNK])).single()
        if r["terminationStatus"] != "OK":
            raise RuntimeError(f"적재 실패: {r['terminationStatus']} {r.get('extraInfo')}")
        loaded += r["triplesLoaded"]
    return {"sent": len(nt), "loaded": loaded}


def counts(s) -> dict:
    q = lambda c: s.run(c).single()["c"]
    return {"nodes": q("MATCH (n) RETURN count(n) AS c"), "relationships": q("MATCH ()-[r]->() RETURN count(r) AS c"),
            "resources": q("MATCH (n:Resource) RETURN count(n) AS c")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop-old", action="store_true", help="기존 그래프를 전부 삭제하고 다시 적재(파괴적)")
    a = ap.parse_args()
    g = read_graph()
    with get_driver() as d, d.session() as s:
        if a.drop_old:
            n = counts(s)["nodes"]
            if input(f"Neo4j 의 노드 {n}개를 모두 삭제합니다. 계속하려면 'yes' 입력: ").strip() != "yes":
                print("취소"); sys.exit(1)
            drop_old(s)
        print(ensure_config(s))
        print("RDF 트리플", len(g), "| 적재:", import_graph(s, g))
        print("그래프 건수:", counts(s))


if __name__ == "__main__":
    main()
