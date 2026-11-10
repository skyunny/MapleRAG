"""모든 check_*.py 를 순서대로 실행하고 결과를 요약한다. 모델 호출은 없다.
실행: uv run python tests/run_all.py
필요 조건: check_rag_offline / check_character(샘플 있을 때) 는 Neo4j 가 떠 있어야 하고, check_rag_offline 일부는 Langfuse 접속을 시도한다(실패해도 기본 프롬프트로 동작).
"""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORDER = ["check_ontology", "check_ingest", "check_rule_mapper", "check_extractor", "check_normalizer", "check_guard",
         "check_eval_metrics", "check_character", "check_rag_offline", "check_notebooks", "check_ui"]
env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
rows, bad = [], 0
for name in ORDER:
    f = HERE / f"{name}.py"
    if not f.exists():
        rows.append((name, "없음", 0.0)); continue
    t0 = time.time()
    p = subprocess.run([sys.executable, str(f)], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=HERE.parent)
    last = [l for l in p.stdout.strip().splitlines() if l.strip()][-1:] or [""]
    ok = p.returncode == 0
    bad += not ok
    rows.append((name, ("통과" if ok else f"실패(코드 {p.returncode}) {p.stderr.strip()[-200:]}"), time.time() - t0))
    if not ok:
        print(p.stdout[-1500:])
print("\n=== 테스트 요약 ===")
for n, r, s in rows:
    print(f"{n:22} {r:10} {s:5.1f}s")
print("결과:", "전체 통과" if not bad else f"실패 {bad}건")
sys.exit(1 if bad else 0)
