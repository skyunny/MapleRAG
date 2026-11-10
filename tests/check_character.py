"""캐릭터 조회 경로 테스트 (API·LLM 호출 없음: 수집해 둔 샘플 사용). 실행: uv run python tests/check_character.py"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from maplerag.character.lookup import ATTRIBUTION, query_graph  # noqa: E402
from maplerag.extract.rule_mapper import map_character  # noqa: E402

sample = ROOT / "data/raw/docs/character_sample.jsonl"
if not sample.exists():
    print("샘플 없음(개인정보라 저장소에 없음) → 건너뜀"); sys.exit(0)
docs = {d["doc_type"]: d for d in map(json.loads, sample.read_text(encoding="utf-8").splitlines())
        if d["doc_type"] in ("character_basic", "character_item_equipment")}
g = map_character(docs)
ocid = docs["character_basic"]["meta"]["ocid"]
ok = True
def check(n, c):
    global ok; ok &= bool(c); print("OK  " if c else "FAIL", n)
for q, kw in [("기본 정보", "Lv."), ("스타포스 높은 장비", "성"), ("잠재능력 등급", "레전드리"), ("착용 장비", "장비 ")]:
    text, rows = query_graph(g, q)
    check(f"'{q}' → 답변에 '{kw}' 포함, 근거 행 {len(rows)}", kw in text and rows)
    check("  답변에 ocid 미노출", ocid not in text)
check("기록용 URL 에 ocid 미노출", all(ocid not in d["url"] for d in docs.values()))
check("출처 표기 문구", ATTRIBUTION == "Data based on NEXON Open API")
check("영구 저장 없음: data/rdf 에 캐릭터 파일 없음", not any("character" in p.name for p in (ROOT / "data/rdf").glob("*")))
print("결과:", "OK" if ok else "FAIL"); sys.exit(0 if ok else 1)
