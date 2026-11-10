"""온톨로지·셰이프 파싱 + 정상 샘플 통과 + 일부러 깨뜨린 샘플의 위반 검출을 확인한다.

실행: uv run python tests/check_ontology.py
"""
import sys
from pathlib import Path

from rdflib import Literal, Namespace, RDF, XSD

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from maplerag.graph.validate import ONTOLOGY, SHAPES, load, validate_graph  # noqa: E402

MAPLE = Namespace("https://example.org/maple#")
MID = Namespace("https://example.org/maple/id/")

onto, shapes = load(ONTOLOGY), load(SHAPES)
classes = set(onto.subjects(RDF.type, __import__("rdflib").OWL.Class))
print(f"maple.ttl 트리플 {len(onto)}개 / 클래스 {len(classes)}개, shapes.ttl 트리플 {len(shapes)}개")

# 1) 정상 샘플
good = load(Path(__file__).parent / "data" / "sample_valid.ttl")
r = validate_graph(good)
print(f"[정상 샘플] 트리플 {len(good)}개 → conforms={r['conforms']}, 위반 {r['violation_count']}건")
for v in r["violations"]:
    print("   ", v["path"], "|", v["message"][:80], "|", v["focus"])

# 2) 일부러 깨뜨린 샘플 (위반이 잡히는지 확인)
bad = load(Path(__file__).parent / "data" / "sample_valid.ttl")
bad.set((MID["prob/demo-sf-17"], MAPLE.probability, Literal("1.5", datatype=XSD.decimal)))   # 범위 초과
bad.remove((MID["skill/demo-slash"], MAPLE.sourceTier, None))                                 # tier 누락
bad.set((MID["interval/demo-event"], MAPLE.endDate, Literal("2025-12-31T00:00:00+09:00", datatype=XSD.dateTime)))  # 종료<시작
bad.remove((MID["fact/chg-1"], MAPLE.evidence, None))                                         # LLM 근거 누락
rb = validate_graph(bad)
print(f"[깨뜨린 샘플] conforms={rb['conforms']}, 위반 {rb['violation_count']}건 (기대: 4건 이상)")
for v in rb["violations"]:
    print("   ", v["path"], "|", v["message"][:70], "|", v["focus"].split("/id/")[-1])

ok = r["conforms"] and not rb["conforms"]
print("결과:", "OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
