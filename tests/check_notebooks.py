"""노트북의 코드 셀을 순서대로 실행해 깨지지 않는지 확인한다(기본값 RUN_LLM=False → 모델 호출 없음).
실행: uv run python tests/check_notebooks.py   (Neo4j 필요. 01 노트북은 멱등 적재를 포함)"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ok = True
for name in ["01_build_kg", "02_rag_demo"]:
    nb = json.loads((ROOT / "notebooks" / f"{name}.ipynb").read_text(encoding="utf-8"))
    ns: dict = {}
    src_all = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    if "RUN_LLM = False" not in src_all:
        print("FAIL", name, "RUN_LLM 기본값이 False 가 아님"); ok = False; continue
    for i, c in enumerate(nb["cells"]):
        if c["cell_type"] != "code":
            continue
        try:
            exec(compile("".join(c["source"]), f"{name}[{i}]", "exec"), ns)
        except Exception as e:
            print(f"FAIL {name} 셀 {i}: {type(e).__name__}: {str(e)[:200]}"); ok = False; break
    else:
        print("OK  ", name, "코드 셀 전부 실행")
print("결과:", "OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
