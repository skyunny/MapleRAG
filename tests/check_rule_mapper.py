"""규칙 매퍼 검증: 수집한 실제 데이터(data/raw) → RDF → SHACL. 실행: uv run python tests/check_rule_mapper.py"""
import json
import sys
from collections import Counter
from pathlib import Path

from rdflib import RDF, Namespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from maplerag.extract.rule_mapper import map_character, map_notices  # noqa: E402
from maplerag.graph.validate import validate_graph  # noqa: E402

MAPLE = Namespace("https://example.org/maple#")


def stats(g):
    return dict(Counter(str(o).split("#")[-1] for o in g.objects(None, RDF.type)))


notices = [json.loads(l) for l in (ROOT / "data/raw/docs/notices.jsonl").read_text(encoding="utf-8").splitlines()]
g1 = map_notices(notices)
r1 = validate_graph(g1)
print(f"[공지] 트리플 {len(g1)}개, 클래스별: {stats(g1)}")
print(f"       SHACL conforms={r1['conforms']}, 위반 {r1['violation_count']}건 {r1['by_path']}")
for v in r1["violations"][:5]:
    print("       ", v["message"][:70], "|", v["focus"].split("/id/")[-1])

chars = {}
for l in (ROOT / "data/raw/docs/character_sample.jsonl").read_text(encoding="utf-8").splitlines():
    d = json.loads(l); chars[d["doc_type"]] = d
g2 = map_character(chars)
r2 = validate_graph(g2)
print(f"[캐릭터(임시)] 트리플 {len(g2)}개, 클래스별: {stats(g2)}")
print(f"       SHACL conforms={r2['conforms']}, 위반 {r2['violation_count']}건 {r2['by_path']}")
for v in r2["violations"][:5]:
    print("       ", v["message"][:70], "|", v["focus"].split("/id/")[-1][:30])

# 재실행 멱등성: 같은 입력이면 같은 트리플(블랭크 노드 Fact 제외)
g1b = map_notices(notices)
nb = lambda g: {t for t in g if not any(type(x).__name__ == "BNode" for x in t)}
print("[멱등성] 공지 매핑 2회 결과 동일:", nb(g1) == nb(g1b))
sys.exit(0 if r1["conforms"] and r2["conforms"] else 1)
