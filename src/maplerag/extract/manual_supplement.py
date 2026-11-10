"""수동 보충 추출: API 크레딧 없이 처리하지 못한 청크 중 가치가 큰 항목만 사람이(=세션 내 Claude 가) 직접 기입한다.
- 출처 표시: extractedBy 는 'manual' 로 구분하고, 5단계에서 LLM 추출과 같은 검증(verify)을 거친다.
- 확률표(숲의 근원 등급 확률 등)는 현재 스키마(ChangeRecord/RelationRecord)로 표현할 수 없어 포함하지 않는다(README 한계).
실행: uv run python -m maplerag.extract.manual_supplement
"""
import json

from maplerag.extract.llm_extractor import verify
from maplerag.extract.schemas import ChangeRecord, ChunkExtraction, RelationRecord
from maplerag.extract.sections import pack_chunks, split_sections
from maplerag.ingest.base import RAW_DIR

KEY = "797:fa5bda81"   # 클라이언트 1.2.412 업데이트 안내, 신규 보스 유피테르 구간


def _add(name, cls, ev):
    return ChangeRecord(target_name=name, target_class=cls, change_type="Added", evidence=ev)


def build() -> ChunkExtraction:
    ch = [_add("유피테르", "Boss", "1.신규 보스 : 유피테르"),
          _add("신을 삼킨 자", "Event", "[보스 이벤트] 신을 삼킨 자")]
    for n in ["오만의 원죄", "유피테르로이드"]:
        ch.append(_add(n, "Equipment", f"장비: {n}"))
    for n in ["뒤틀린 갈망의 편린", "갈망의 에테르넬 방어구 상자", "유피테르의 소울 조각"]:
        ch.append(_add(n, "Consumable", f"소비: {n}"))
    for p in ["기운찬", "날렵한", "총명한", "놀라운", "화려한", "강력한", "빛나는", "강인한", "위대한"]:
        ch.append(_add(f"{p} 유피테르의 소울", "Consumable", f"소비: {p} 유피테르의 소울"))
    rel = [RelationRecord(subject_name="신을 삼킨 자", subject_class="Event", predicate="rewards",
                          object_name="미니어처 크로노스", object_class="Item",
                          evidence="유피테르 격파 미션 | 미니어처 크로노스 | 1 | 월드 내 나의 캐릭터 간 이동만 가능 | 영구"),
           RelationRecord(subject_name="신을 삼킨 자", subject_class="Event", predicate="rewards",
                          object_name="탐식의 마침표", object_class="Item",
                          evidence="탐식의 마침표 | 1 | 교환 불가 | 영구")]
    return ChunkExtraction(changes=ch, relations=rel)


def main():
    docs = [json.loads(l) for l in (RAW_DIR / "docs" / "notices.jsonl").read_text(encoding="utf-8").splitlines()]
    chunk = next(c for d in docs if d["doc_type"] == "patch_note" for c in pack_chunks(split_sections(d)) if c.stable_key == KEY)
    ex = build()
    kc, kr, dropped = verify(chunk, ex)
    print(f"입력 변경 {len(ex.changes)} 관계 {len(ex.relations)} → 검증 통과 변경 {len(kc)} 관계 {len(kr)} 폐기 {len(dropped)}")
    for d in dropped:
        print("  폐기:", d.reason, d.item.get("target_name") or d.item.get("object_name"))
    out = RAW_DIR / "extraction" / "manual" / "supplement.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"chunk": KEY, "notice_id": chunk.doc_notice_id, "patch": chunk.patch_title, "date": chunk.patch_date,
                               "extracted_by": "manual", "changes": [c.model_dump() for c in kc],
                               "relations": [r.model_dump() for r in kr]}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("저장:", out)


if __name__ == "__main__":
    main()
