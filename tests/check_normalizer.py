"""정규화 결과의 결정성 점검(모델·DB 없음). 실행: uv run python tests/check_normalizer.py
n10s 는 단일값 속성에 값이 둘 이상이면 하나로 덮어쓰므로, 같은 (노드, 속성)에 서로 다른 리터럴이 둘 이상 있으면
적재 순서에 따라 결과가 달라진다(실제로 이벤트 라벨에서 발생했던 결함). altLabel 만 다중값을 허용한다."""
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from rdflib import Literal  # noqa: E402

from maplerag.extract.run_normalize import RDF_DIR, build  # noqa: E402
from maplerag.graph.validate import load  # noqa: E402

g, _ = build()
for t in load(RDF_DIR / "notices.ttl"):
    g.add(t)
vals = defaultdict(set)
for s, p, o in g:
    if isinstance(o, Literal) and not str(p).endswith("altLabel"):
        vals[(s, p)].add(str(o))
bad = {k: v for k, v in vals.items() if len(v) > 1}
print("다중값 단일 속성:", len(bad), "건")
for (s, p), v in list(bad.items())[:5]:
    print("  ", str(s)[-30:], str(p).split("#")[-1], v)
g2, _ = build()
print("재빌드 동일:", set(g) >= set(g2))
ok = not bad
print("결과:", "OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
