"""5단계: 추출 결과 정규화 → RDF 저장 → SHACL 검증 → 통계. 실행: uv run python -m maplerag.extract.run_normalize"""
import json
from collections import Counter

from rdflib import RDF, Graph
from rdflib.namespace import PROV

from maplerag.extract.normalizer import Normalizer
from maplerag.extract.rule_mapper import MAPLE
from maplerag.graph.validate import load, validate_graph
from maplerag.ingest.base import RAW_DIR

RDF_DIR = RAW_DIR.parent / "rdf"


def build() -> tuple[Graph, Normalizer]:
    notices = [json.loads(l) for l in (RAW_DIR / "docs" / "notices.jsonl").read_text(encoding="utf-8").splitlines()]
    base = load(RDF_DIR / "notices.ttl")
    recs = [r for r in map(json.loads, (RAW_DIR / "docs" / "extraction_all.jsonl").read_text(encoding="utf-8").splitlines()) if not r["error"]]
    for r in recs:
        r["extracted_by"] = "llm"
    man = json.loads((RAW_DIR / "extraction" / "manual" / "supplement.json").read_text(encoding="utf-8"))
    recs.append({**man, "extracted_by": "manual"})
    n = Normalizer(notices, existing=base)
    n.run(recs)
    return n.g, n


def main():
    g, n = build()
    g2, _ = build()
    print(f"멱등성: 두 번 빌드한 트리플 수 {len(g)} == {len(g2)} → {len(g) == len(g2)}, 동형 {set(g) == set(g2)}")
    RDF_DIR.mkdir(parents=True, exist_ok=True)
    out = RDF_DIR / "extraction.ttl"
    g.serialize(out, format="turtle")
    print("저장:", out, "| 추출분 트리플", len(g))

    full = load(RDF_DIR / "notices.ttl")
    for t in g:
        full.add(t)
    print("전체(공지+추출) 트리플", len(full))
    rep = validate_graph(full)
    print(f"SHACL: conforms={rep['conforms']} 위반 {rep['violation_count']}건 by_path={rep['by_path']}")
    for v in rep["violations"][:5]:
        print("  ", v["focus"][-40:], v["path"][-20:], v["message"][:80])

    print("--- 정규화 통계 ---")
    for k, v in n.stats.items():
        print(f"  {k}: {v}")
    cls = Counter()
    for s, o in full.subject_objects(RDF.type):
        if str(o).startswith(str(MAPLE)):
            cls[str(o).split("#")[1]] += 1
    print("클래스별 개수:", dict(cls.most_common()))
    rel = Counter(str(p).split("#")[1] for s, p, o in full if str(p).startswith(str(MAPLE)) and p in
                  [MAPLE.hasSkill, MAPLE.rewards, MAPLE.dropsItem, MAPLE.appearsIn, MAPLE.usableBy, MAPLE.givenBy,
                   MAPLE.hasChange, MAPLE.changes, MAPLE.inPatch, MAPLE.aboutFact])
    print("관계별 개수:", dict(rel.most_common()))
    # 추적성: 모든 PatchChange 가 출처·패치·근거를 가지는가
    pcs = list(full.subjects(RDF.type, MAPLE.PatchChange))
    ok = sum(1 for c in pcs if (c, PROV.wasDerivedFrom, None) in full and (c, MAPLE.inPatch, None) in full and (c, MAPLE.evidence, None) in full)
    print(f"추적성: PatchChange {len(pcs)}건 중 출처+패치+근거 모두 보유 {ok}건")


if __name__ == "__main__":
    main()
